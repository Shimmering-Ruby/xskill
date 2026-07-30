"""ux_scores_store — registry.db 中 UX 体验分的读写与盘→库同步。

盘上 ``<skill>/.ux_scores.jsonl`` 仍是 append 落点；本模块把记录写入
``ux_scores`` 表，供 ranked / canary / 看板读路径使用。定时任务调用
``sync_ux_scores_from_skill_dir`` 做全量/增量一致性维护。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from xskill.pipeline.registry import pooled_connection

logger = logging.getLogger("xskill.pipeline.ux_scores_store")

UX_SCORES_FILENAME = ".ux_scores.jsonl"
META_LAST_SYNC = "last_sync_at"


def _record_keys(record: dict) -> tuple[str, str, str, str]:
    skill_name = str(record.get("skill_name") or "")
    side = str(record.get("side") or "main")
    atom_id = str(record.get("atom_id") or "")
    traj_id = str(record.get("traj_id") or "")
    return skill_name, side, atom_id, traj_id


def insert_ux_score(
    record: dict,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """``INSERT OR IGNORE`` 一条 UX 记录。返回是否新插入。"""
    skill_name, side, atom_id, traj_id = _record_keys(record)
    if not skill_name:
        return False
    if not atom_id and not traj_id:
        # 无幂等键时用 scored_at+score 弱去重不够稳；拒绝空键写入
        logger.warning("ux_scores insert skipped: missing atom_id and traj_id")
        return False
    scored_at = str(record.get("scored_at") or "")
    if not scored_at:
        scored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        score = float(record.get("score"))
    except (TypeError, ValueError):
        return False
    with pooled_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ux_scores("
            " skill_name, side, commit_sha, score, scored_at,"
            " atom_id, traj_id, reasons, user_model"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                skill_name,
                side,
                str(record.get("commit_sha") or ""),
                score,
                scored_at,
                atom_id,
                traj_id,
                str(record.get("reasons") or ""),
                str(record.get("user_model") or ""),
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def load_ux_scores_for_skill(
    skill_name: str,
    *,
    side: Optional[str] = None,
    days: int = 30,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """从 DB 读某 skill 的 UX 分（字段口径对齐 jsonl 记录）。"""
    if not skill_name:
        return []
    clauses = ["skill_name = ?"]
    params: list = [skill_name]
    if side is not None:
        clauses.append("side = ?")
        params.append(side)
    if days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        clauses.append("scored_at >= ?")
        params.append(cutoff_iso)
    sql = (
        "SELECT skill_name, side, commit_sha, score, scored_at,"
        " atom_id, traj_id, reasons, user_model"
        f" FROM ux_scores WHERE {' AND '.join(clauses)}"
        " ORDER BY scored_at DESC"
    )
    with pooled_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for row in rows:
        out.append({
            "skill_name": row["skill_name"],
            "side": row["side"],
            "commit_sha": row["commit_sha"],
            "score": row["score"],
            "scored_at": row["scored_at"],
            "atom_id": row["atom_id"] or None,
            "traj_id": row["traj_id"] or None,
            "reasons": row["reasons"],
            "user_model": row["user_model"],
        })
    return out


def avg_scores_for_refs(
    refs: dict[str, str],
    *,
    side: str = "main",
    days: int = 30,
    db_path: Optional[Path] = None,
) -> dict[str, float]:
    """批量查 ``skill_name → avg(score)``（限定 side + commit_sha + 近 days）。

    ``refs`` 为 ``{skill_name: commit_sha}``。无分的 skill 不出现在返回字典中。
    """
    if not refs:
        return {}
    cutoff_iso = ""
    if days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    # SQLite 无原生 tuple IN 多列，按 sha 分组批量查再过滤
    by_sha: dict[str, list[str]] = {}
    for name, sha in refs.items():
        if not name or not sha:
            continue
        by_sha.setdefault(sha, []).append(name)
    result: dict[str, float] = {}
    with pooled_connection(db_path) as conn:
        for sha, names in by_sha.items():
            placeholders = ",".join("?" for _ in names)
            params: list = [side, sha, *names]
            time_clause = ""
            if cutoff_iso:
                time_clause = " AND scored_at >= ?"
                params.append(cutoff_iso)
            rows = conn.execute(
                "SELECT skill_name, AVG(score) AS avg_score"
                " FROM ux_scores"
                f" WHERE side = ? AND commit_sha = ? AND skill_name IN ({placeholders})"
                f"{time_clause}"
                " GROUP BY skill_name",
                params,
            ).fetchall()
            for row in rows:
                if row["avg_score"] is not None:
                    result[row["skill_name"]] = float(row["avg_score"])
    return result


def load_all_usage_records(*, db_path: Optional[Path] = None) -> list[dict]:
    """看板统一视图：全库 UX 行 → ``{skill, side, sha, score, ...}``。"""
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT skill_name, side, commit_sha, score, scored_at,"
            " atom_id, traj_id, user_model"
            " FROM ux_scores"
            " ORDER BY scored_at DESC"
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        atom_id = row["atom_id"] or ""
        traj_id = row["traj_id"] or ""
        if not traj_id and atom_id and "__" in atom_id:
            traj_id = atom_id.rsplit("__", 1)[0]
        out.append({
            "skill": row["skill_name"],
            "side": row["side"] or "main",
            "sha": row["commit_sha"] or "unknown",
            "score": row["score"],
            "scored_at": row["scored_at"] or "",
            "atom_id": atom_id,
            "traj_id": traj_id,
            "user_model": row["user_model"] or "",
        })
    return out


def sync_ux_scores_from_skill_dir(
    skill_dir: Path | str,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """扫 ``skill_dir`` 下各 skill 的 ``.ux_scores.jsonl`` → 入库。

    返回 ``{skills, lines, inserted}``。
    """
    root = Path(skill_dir)
    skills = 0
    lines = 0
    inserted = 0
    if not root.is_dir():
        return {"skills": 0, "lines": 0, "inserted": 0}
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        path = d / UX_SCORES_FILENAME
        if not path.is_file():
            continue
        skills += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("ux sync read failed %s: %s", path, exc)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("ux sync bad json in %s: %s", path, exc)
                continue
            if not rec.get("skill_name"):
                rec["skill_name"] = d.name
            if insert_ux_score(rec, db_path=db_path):
                inserted += 1
    _set_meta(META_LAST_SYNC, datetime.now(timezone.utc).isoformat(timespec="seconds"),
              db_path=db_path)
    return {"skills": skills, "lines": lines, "inserted": inserted}


def _set_meta(key: str, value: str, *, db_path: Optional[Path] = None) -> None:
    with pooled_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO ux_scores_meta(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_meta(key: str, *, db_path: Optional[Path] = None) -> Optional[str]:
    with pooled_connection(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM ux_scores_meta WHERE key = ?", (key,)
        ).fetchone()
    return None if row is None else str(row["value"])
