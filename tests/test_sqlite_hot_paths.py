"""SQLite 高并发读写回归：WAL 不应在每次连接重复切换。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np


class _TrackedConnection:
    def __init__(self, connection, statements: list[str], lock: threading.Lock):
        self._connection = connection
        self._statements = statements
        self._lock = lock

    def execute(self, sql, *args, **kwargs):
        with self._lock:
            self._statements.append(" ".join(str(sql).lower().split()))
        return self._connection.execute(sql, *args, **kwargs)

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _tracking_connect(monkeypatch, sqlite_module):
    original_connect = sqlite_module.connect
    statements: list[str] = []
    lock = threading.Lock()

    def connect(*args, **kwargs):
        return _TrackedConnection(original_connect(*args, **kwargs), statements, lock)

    monkeypatch.setattr(sqlite_module, "connect", connect)
    return statements


def _journal_mode_assignments(statements: list[str]) -> list[str]:
    """只统计修改 journal_mode 的 PRAGMA，不把状态查询算进去。"""
    return [sql for sql in statements if sql == "pragma journal_mode=wal"]


def test_registry_new_db_concurrent_open_assigns_wal_once(tmp_path, monkeypatch):
    """新 DB 的首批连接并发打开时，WAL 赋值与 schema 初始化不竞态。"""
    from xskill.pipeline import registry

    db_path = tmp_path / "new-registry.db"
    statements = _tracking_connect(monkeypatch, registry.sqlite3)
    barrier = threading.Barrier(30)

    def open_registry(_index: int) -> None:
        barrier.wait(timeout=10)
        conn = registry.get_connection(db_path)
        try:
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trajectories'"
            ).fetchone() is not None
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(open_registry, range(30)))

    assert len(_journal_mode_assignments(statements)) == 1


def test_registry_concurrent_usage_writes_do_not_reassign_wal(tmp_path, monkeypatch):
    """600 次 LLM usage 写入期间，热连接不重复赋值 WAL。"""
    from xskill.pipeline import registry

    db_path = tmp_path / "registry.db"
    registry.get_connection(db_path).close()
    statements = _tracking_connect(monkeypatch, registry.sqlite3)

    def write_usage(index: int) -> None:
        registry.record_usage(
            step="skill_edit",
            model="mock-tool-call",
            prompt=index,
            completion=1,
            total=index + 1,
            cost_usd=0,
            price_source="config",
            db_path=db_path,
        )

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(write_usage, range(600)))

    summary = registry.usage_summary(db_path)
    assert summary["total_calls"] == 600
    assert _journal_mode_assignments(statements) == []


def test_profile_and_reco_stores_share_first_use_locks(tmp_path, monkeypatch):
    """多个 store 实例共用新 DB 时，WAL 只赋值一次且 schema 都完整。"""
    from xskill.recommend import _sqlite_base
    from xskill.recommend.profile_store import ProfileStore
    from xskill.recommend.reco_store import RecoStore

    db_path = tmp_path / "new-profile.db"
    statements = _tracking_connect(monkeypatch, _sqlite_base.sqlite3)
    barrier = threading.Barrier(30)

    def create_store(index: int):
        barrier.wait(timeout=10)
        store_type = ProfileStore if index % 2 == 0 else RecoStore
        return store_type(db_path)

    with ThreadPoolExecutor(max_workers=30) as executor:
        stores = list(executor.map(create_store, range(30)))

    assert len(stores) == 30
    assert len(_journal_mode_assignments(statements)) == 1
    conn = _sqlite_base.sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"client_interest", "recommendations"} <= tables


def test_profile_store_30_workers_do_not_touch_journal_mode_on_hot_path(
    tmp_path, monkeypatch,
):
    """画像 worker 共享的 store 初始化后，读写不再访问 journal_mode。"""
    from xskill.recommend import _sqlite_base
    from xskill.recommend.profile_store import ProfileStore

    store = ProfileStore(tmp_path / "profiles.db")
    statements = _tracking_connect(monkeypatch, _sqlite_base.sqlite3)

    def refresh_profile(index: int) -> None:
        user_id = f"client-{index:03d}"
        points = np.asarray([[float(index), 1.0]])
        assert store.get_revision(user_id) is None
        assert store.load_vector_cache_entries(user_id, "mock-embed") == {}
        store.upsert(
            user_id,
            feature_tensor=points,
            mean_tensor=points[0],
            used_skills=[],
            points=points,
            point_meta=[{"atom_id": f"atom-{index:03d}", "summary": "mock"}],
            embed_model="mock-embed",
            source_revision=f"rev-{index:03d}",
        )

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(refresh_profile, range(300)))

    assert len(store.all_means()) == 300
    assert not any(sql.startswith("pragma journal_mode") for sql in statements)
