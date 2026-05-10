"""
registry.py -- SQLite 路径注册表
==================================

管理 ``~/.xskill/registry.db``，**只存路径和状态**，不存内容。

两张表：

- ``watch_dirs``   用户注册的待监听目录
- ``trajectories`` 每条轨迹文件的发现/索引状态
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from xskill.config import get_registry_db_path

logger = logging.getLogger("xskill.registry")

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watch_dirs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT UNIQUE NOT NULL,
    label      TEXT DEFAULT '',
    auto_index INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trajectories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_dir_id  INTEGER NOT NULL REFERENCES watch_dirs(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    has_meta      INTEGER DEFAULT 0,
    has_embedding INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'discovered',
    process_action TEXT,
    skill_generated TEXT,
    skill_used    TEXT,
    canary_side   TEXT,
    ux_score      REAL,
    error_msg     TEXT,
    retry_count   INTEGER DEFAULT 0,
    file_mtime    REAL DEFAULT 0,
    discovered_at TEXT DEFAULT (datetime('now')),
    indexed_at    TEXT,
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(watch_dir_id, filename)
);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """打开（或创建）注册表 DB。首次调用自动建表。"""
    if db_path is None:
        db_path = get_registry_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    # Migrate existing DBs that lack new columns
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns missing from older schema versions."""
    cur = conn.execute("PRAGMA table_info(trajectories)")
    cols = {row[1] for row in cur.fetchall()}
    migrations = [
        ("status", "TEXT DEFAULT 'discovered'"),
        ("process_action", "TEXT"),
        ("skill_generated", "TEXT"),
        ("ux_score", "REAL"),
        ("error_msg", "TEXT"),
        ("retry_count", "INTEGER DEFAULT 0"),
        ("updated_at", "TEXT"),
        ("process_log", "TEXT"),
    ]
    for col, typedef in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE trajectories ADD COLUMN {col} {typedef}")
    # Backfill status from has_meta/has_embedding for pre-existing rows
    conn.execute(
        "UPDATE trajectories SET status='indexed'"
        " WHERE has_embedding=1 AND (status IS NULL OR status='discovered')"
    )
    conn.execute(
        "UPDATE trajectories SET status='meta_done'"
        " WHERE has_meta=1 AND has_embedding=0 AND (status IS NULL OR status='discovered')"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Watch directory management
# ---------------------------------------------------------------------------

def register_dir(
    dir_path: str | Path,
    label: str = "",
    auto_index: bool = True,
    *,
    db_path: Optional[Path] = None,
) -> int:
    """注册一个目录。幂等：已存在则更新 label/auto_index，返回 id。"""
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO watch_dirs (path, label, auto_index) VALUES (?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET label=excluded.label, auto_index=excluded.auto_index",
            (dir_path, label, int(auto_index)),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM watch_dirs WHERE path=?", (dir_path,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def unregister_dir(dir_path: str | Path, *, db_path: Optional[Path] = None) -> bool:
    """移除目录及其轨迹记录。返回 True 表示找到并删除。"""
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM watch_dirs WHERE path=?", (dir_path,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_watch_dirs(*, db_path: Optional[Path] = None) -> list[dict]:
    """返回所有注册目录及统计信息。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT w.*, "
            "  (SELECT COUNT(*) FROM trajectories t WHERE t.watch_dir_id=w.id) AS traj_count,"
            "  (SELECT COUNT(*) FROM trajectories t WHERE t.watch_dir_id=w.id AND t.has_embedding=1) AS indexed_count"
            " FROM watch_dirs w ORDER BY w.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_watch_dir(dir_path: str | Path, *, db_path: Optional[Path] = None) -> dict | None:
    """查询单个目录记录。"""
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM watch_dirs WHERE path=?", (dir_path,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trajectory tracking
# ---------------------------------------------------------------------------

def discover_trajectories(
    watch_dir_id: int,
    dir_path: Path,
    *,
    db_path: Optional[Path] = None,
) -> list[str]:
    """扫描目录中的 traj_*.md，upsert 到 DB。返回新发现的文件名列表。"""
    dir_path = Path(dir_path)
    conn = get_connection(db_path)
    new_files: list[str] = []
    try:
        existing = {
            row["filename"]
            for row in conn.execute(
                "SELECT filename FROM trajectories WHERE watch_dir_id=?",
                (watch_dir_id,),
            ).fetchall()
        }

        for md in sorted(dir_path.glob("traj_*.md")):
            if md.name.endswith(".meta"):
                continue
            mtime = md.stat().st_mtime
            if md.name not in existing:
                conn.execute(
                    "INSERT INTO trajectories (watch_dir_id, filename, file_mtime)"
                    " VALUES (?, ?, ?)",
                    (watch_dir_id, md.name, mtime),
                )
                new_files.append(md.name)
            else:
                # 更新 mtime（用于变更检测）
                conn.execute(
                    "UPDATE trajectories SET file_mtime=? WHERE watch_dir_id=? AND filename=?",
                    (mtime, watch_dir_id, md.name),
                )

        conn.commit()
        return new_files
    finally:
        conn.close()


def mark_meta_done(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET has_meta=1 WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def mark_indexed(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET has_embedding=1, indexed_at=?"
            " WHERE watch_dir_id=? AND filename=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def mark_skill_used(
    watch_dir_id: int,
    filename: str,
    skill_used: str,
    canary_side: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET skill_used=?, canary_side=?"
            " WHERE watch_dir_id=? AND filename=?",
            (skill_used, canary_side, watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_unindexed(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回缺少 meta 或 embedding 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND (has_meta=0 OR has_embedding=0)"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def get_needs_meta(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回缺少 meta 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND has_meta=0"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def get_needs_embedding(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回有 meta 但缺 embedding 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND has_meta=1 AND has_embedding=0"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cross-dataset search support
# ---------------------------------------------------------------------------

def all_index_paths(*, db_path: Optional[Path] = None) -> list[Path]:
    """返回所有注册目录中实际存在 index.pkl 的路径。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT path FROM watch_dirs ORDER BY id").fetchall()
        result = []
        for r in rows:
            p = Path(r["path"])
            if (p / "index.pkl").is_file():
                result.append(p)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Status management
# ---------------------------------------------------------------------------

_NOW = "datetime('now')"


def update_traj_status(
    watch_dir_id: int,
    filename: str,
    status: str,
    *,
    process_action: str | None = None,
    skill_generated: str | None = None,
    ux_score: float | None = None,
    error_msg: str | None = None,
    db_path: Optional[Path] = None,
) -> None:
    """更新轨迹状态及关联字段。"""
    conn = get_connection(db_path)
    try:
        sets = ["updated_at=datetime('now')"]
        vals: list = []
        if status is not None:
            sets.append("status=?")
            vals.append(status)
        if process_action is not None:
            sets.append("process_action=?")
            vals.append(process_action)
        if skill_generated is not None:
            sets.append("skill_generated=?")
            vals.append(skill_generated)
        if ux_score is not None:
            sets.append("ux_score=?")
            vals.append(ux_score)
        if error_msg is not None:
            sets.append("error_msg=?")
            vals.append(error_msg)
        vals.extend([watch_dir_id, filename])
        conn.execute(
            f"UPDATE trajectories SET {', '.join(sets)}"
            " WHERE watch_dir_id=? AND filename=?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def update_traj_log(
    watch_dir_id: int,
    filename: str,
    log_json: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """Store process log (JSON string) for a trajectory."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET process_log=?, updated_at=datetime('now')"
            " WHERE watch_dir_id=? AND filename=?",
            (log_json, watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_traj_log(
    watch_dir_id: int,
    filename: str,
    *,
    db_path: Optional[Path] = None,
) -> str | None:
    """Retrieve the stored process log JSON for a trajectory."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT process_log FROM trajectories WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        ).fetchone()
        return row["process_log"] if row else None
    finally:
        conn.close()


def get_trajs_by_status(
    watch_dir_id: int,
    status: str,
    *,
    limit: int = 0,
    max_retries: int = 3,
    db_path: Optional[Path] = None,
) -> list[str]:
    """按状态查询文件名。error 状态自动过滤超过 max_retries 的。"""
    conn = get_connection(db_path)
    try:
        sql = "SELECT filename FROM trajectories WHERE watch_dir_id=? AND status=?"
        params: list = [watch_dir_id, status]
        if status == "error":
            sql += " AND retry_count < ?"
            params.append(max_retries)
        sql += " ORDER BY filename"
        if limit > 0:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, params).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def increment_retry(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET retry_count = retry_count + 1"
            " WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_status_counts(
    watch_dir_id: int | None = None, *, db_path: Optional[Path] = None
) -> dict[str, int]:
    """返回各状态的轨迹数量。watch_dir_id=None 时统计全部。"""
    conn = get_connection(db_path)
    try:
        if watch_dir_id is not None:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM trajectories"
                " WHERE watch_dir_id=? GROUP BY status",
                (watch_dir_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM trajectories GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
    finally:
        conn.close()
