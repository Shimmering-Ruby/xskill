"""ux_scores_store — registry.db 中 UX 体验分的读写与盘→库同步。

盘上 ``<skill>/.ux_scores.jsonl`` 仍是 append 落点；本模块把记录写入
``ux_scores`` 表，供 ranked / canary 读路径使用。定时合扫入口见
``skill_dir_sync.sync_skill_disk_projections``（同轮处理 pending 等投影）；
``sync_ux_scores_from_skill_dir`` 为其兼容包装，只返回 UX 统计。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from xskill.pipeline.registry import pooled_connection

logger = logging.getLogger("xskill.pipeline.ux_scores_store")

UX_SCORES_FILENAME = ".ux_scores.jsonl"
META_LAST_SYNC = "last_sync_at"
META_FILE_MTIME_PREFIX = "mtime:"  # + relative skill name
# SQLite 默认变量上限约 999（旧版）；side/sha/cutoff 各占位后留余量
_IN_BATCH_SIZE = 500


def _record_keys(record: dict) -> tuple[str, str, str, str]:
    skill_name = str(record.get("skill_name") or "")
    side = str(record.get("side") or "main")
    atom_id = str(record.get("atom_id") or "")
    traj_id = str(record.get("traj_id") or "")
    return skill_name, side, atom_id, traj_id


def _row_tuple(record: dict) -> tuple | None:
    skill_name, side, atom_id, traj_id = _record_keys(record)
    if not skill_name:
        return None
    if not atom_id and not traj_id:
        logger.warning("ux_scores insert skipped: missing atom_id and traj_id")
        return None
    scored_at = str(record.get("scored_at") or "")
    if not scored_at:
        scored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        score = float(record.get("score"))
    except (TypeError, ValueError):
        return None
    return (
        skill_name,
        side,
        str(record.get("commit_sha") or ""),
        score,
        scored_at,
        atom_id,
        traj_id,
        str(record.get("reasons") or ""),
        str(record.get("user_model") or ""),
    )


def insert_ux_score(
    record: dict,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """``INSERT OR IGNORE`` 一条 UX 记录。返回是否新插入。"""
    row = _row_tuple(record)
    if row is None:
        return False
    with pooled_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ux_scores("
            " skill_name, side, commit_sha, score, scored_at,"
            " atom_id, traj_id, reasons, user_model"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            row,
        )
        conn.commit()
        return cur.rowcount > 0


def insert_ux_scores_many(
    records: list[dict],
    *,
    db_path: Optional[Path] = None,
) -> int:
    """同连接批量 ``INSERT OR IGNORE``，一次 commit。返回新插入行数。"""
    rows = []
    for record in records:
        row = _row_tuple(record)
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    with pooled_connection(db_path) as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO ux_scores("
            " skill_name, side, commit_sha, score, scored_at,"
            " atom_id, traj_id, reasons, user_model"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return max(conn.total_changes - before, 0)


def load_ux_scores_for_skill(
    skill_name: str,
    *,
    side: Optional[str] = None,
    days: int = 30,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """从 DB 读某 skill 的 UX 分（字段口径对齐 jsonl 记录）。

    镜像失败且 sync 未到时，DB 可能暂时缺最新分（样本偏少、偏保守）；
    调用方（``recent_scores`` / ``SkillCanaryOps.ux_scores``）在空结果时
    回退盘文件。
    """
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
    batch_size: int = _IN_BATCH_SIZE,
) -> dict[str, float]:
    """批量查 ``skill_name → avg(score)``（限定 side + commit_sha + 近 days）。

    ``refs`` 为 ``{skill_name: commit_sha}``。无分的 skill 不出现在返回字典中。
    ``skill_name IN (...)`` 按 ``batch_size``（默认 500）分批，避开 SQLite
    变量上限（旧版约 999）。
    """
    if not refs:
        return {}
    if batch_size < 1:
        raise ValueError(f"batch_size 必须是正整数，got {batch_size!r}")
    cutoff_iso = ""
    if days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    by_sha: dict[str, list[str]] = {}
    for name, sha in refs.items():
        if not name or not sha:
            continue
        by_sha.setdefault(sha, []).append(name)
    result: dict[str, float] = {}
    with pooled_connection(db_path) as conn:
        for sha, names in by_sha.items():
            for start in range(0, len(names), batch_size):
                chunk = names[start:start + batch_size]
                placeholders = ",".join("?" for _ in chunk)
                params: list = [side, sha, *chunk]
                time_clause = ""
                if cutoff_iso:
                    time_clause = " AND scored_at >= ?"
                    params.append(cutoff_iso)
                rows = conn.execute(
                    "SELECT skill_name, AVG(score) AS avg_score"
                    " FROM ux_scores"
                    f" WHERE side = ? AND commit_sha = ?"
                    f" AND skill_name IN ({placeholders})"
                    f"{time_clause}"
                    " GROUP BY skill_name",
                    params,
                ).fetchall()
                for row in rows:
                    if row["avg_score"] is not None:
                        result[row["skill_name"]] = float(row["avg_score"])
    return result


def load_all_usage_records(*, db_path: Optional[Path] = None) -> list[dict]:
    """全库 UX 行 → ``{skill, side, sha, score, ...}``（不按 skill_dir 隔离）。"""
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

    实现已并入 ``skill_dir_sync.sync_skill_disk_projections``（与 pending
    等同轮 ``iterdir``）。本函数只返回 UX 统计，保持旧调用方兼容。
    """
    from xskill.pipeline.skill_dir_sync import sync_skill_disk_projections

    return sync_skill_disk_projections(skill_dir, db_path=db_path)["ux"]


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
