"""_sqlite_base.py — SQLite 存储基类（消除 ProfileStore / RecoStore 的样板重复）

提供连接管理 + schema 初始化钩子，子类只给 ``_SCHEMA`` 和 db_path。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from xskill._sqlite_connect import connect_with_lock


_DB_LOCKS_GUARD = threading.Lock()
_WAL_LOCKS: dict[Path, threading.Lock] = {}
_SCHEMA_LOCKS: dict[Path, threading.Lock] = {}


def _lock_for(mapping: dict[Path, threading.Lock], db_path: Path) -> threading.Lock:
    """返回进程内按 SQLite 文件共享的锁。"""
    key = db_path.expanduser().resolve()
    with _DB_LOCKS_GUARD:
        return mapping.setdefault(key, threading.Lock())


class _SqliteStore:
    """SQLite 存储基类：每操作开新连接（规模小），``_SCHEMA`` 由子类提供。

    子类需设类属性 ``_SCHEMA``（CREATE TABLE IF NOT EXISTS ... 脚本）。
    可重写 ``_migrate(conn)`` 做幂等加列迁移。
    """

    _SCHEMA: str = ""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # WAL is persistent for the database.  Configure it once per store
        # instance instead of taking the journal-mode/schema lock on every
        # profile read and write.  Profile refresh deliberately shares one
        # store across its fixed worker set, so this also covers concurrent
        # first use safely.
        self._journal_mode_ready = False
        self._journal_mode_lock = threading.Lock()
        self._db_journal_mode_lock = _lock_for(_WAL_LOCKS, self.db_path)
        self._db_schema_lock = _lock_for(_SCHEMA_LOCKS, self.db_path)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = connect_with_lock(sqlite3.connect, str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL 允许画像刷新写入时，/sync、看板等读取方继续工作。busy_timeout
        # 必须逐连接设置；synchronous 也是连接级设置，不能只在建库时执行一次。
        conn.execute("PRAGMA busy_timeout=10000")
        if not self._journal_mode_ready:
            with self._journal_mode_lock:
                if not self._journal_mode_ready:
                    # ProfileStore / RecoStore 是两个实例，但共用同一
                    # team_profile.db。实例内锁之外还需要按 DB 文件串行
                    # 首次 WAL 设置，获锁后重查可避免重复赋值。
                    with self._db_journal_mode_lock:
                        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                        if str(mode).lower() != "wal":
                            conn.execute("PRAGMA journal_mode=WAL")
                    self._journal_mode_ready = True
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        # 不同 store 的 schema 都在同一数据库中，首次并发构造时必须
        # 串行 executescript/迁移，否则仍可在 WAL 设置之后抢 schema 写锁。
        with self._db_schema_lock:
            conn = self._conn()
            try:
                conn.executescript(self._SCHEMA)
                conn.commit()
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """子类重写：幂等加列 / 数据迁移。缺省 no-op。"""
