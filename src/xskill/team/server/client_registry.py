"""client_registry.py — team server 的 client 注册表（SP1）

server 需要持久化的只有三样：client 注册表、skill git 仓、汇聚的
ux_score 明细。这个文件是第一样。

client_id 是 server 生成的 uuid——它同时是 ① canary 分桶 key（喂
pick_side）② 上传轨迹的落盘分桶（clients/<client_id>/sessions/）③
手改分支命名（user-staging/<client_id>）。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    label      TEXT DEFAULT '',
    hostname   TEXT DEFAULT '',
    joined_at  TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClientRegistry:
    """SQLite 支撑的 client 注册表。每次操作开新连接（规模小，几十个 client）。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def register(self, *, label: str = "", hostname: str = "") -> str:
        """注册一个新 client，返回新生成的 client_id。"""
        client_id = uuid.uuid4().hex
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO clients (client_id, label, hostname, joined_at, last_seen)"
                " VALUES (?, ?, ?, ?, ?)",
                (client_id, label, hostname, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return client_id

    def exists(self, client_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def touch(self, client_id: str) -> None:
        """更新 last_seen。client_id 不存在则静默 no-op。"""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE clients SET last_seen=? WHERE client_id=?",
                (_now(), client_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, client_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM clients ORDER BY joined_at"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
