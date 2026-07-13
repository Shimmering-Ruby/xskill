"""SQLite 高并发读写回归：WAL 不应在每次连接重复切换。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import numpy as np
import pytest


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


class _ConcurrencyProbe:
    """Record peak concurrency in a small, deterministic timing window."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


class _ExecuteProbeConnection:
    def __init__(self, connection, probe: _ConcurrencyProbe):
        self._connection = connection
        self._probe = probe

    def execute(self, sql, *args, **kwargs):
        self._probe.enter()
        try:
            time.sleep(0.01)
            return self._connection.execute(sql, *args, **kwargs)
        finally:
            self._probe.leave()

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _slow_connect_probe(monkeypatch, sqlite_module):
    original_connect = sqlite_module.connect
    connect_probe = _ConcurrencyProbe()
    execute_probe = _ConcurrencyProbe()

    def connect(*args, **kwargs):
        connect_probe.enter()
        try:
            time.sleep(0.01)
            connection = original_connect(*args, **kwargs)
        finally:
            connect_probe.leave()
        return _ExecuteProbeConnection(connection, execute_probe)

    monkeypatch.setattr(sqlite_module, "connect", connect)
    return connect_probe, execute_probe


def test_connect_open_lock_is_released_when_connect_raises():
    """A failed open must not block the next SQLite connection attempt."""
    from xskill._sqlite_connect import connect_with_lock

    expected = RuntimeError("mock connect failure")

    def fail_connect():
        raise expected

    with pytest.raises(RuntimeError) as exc_info:
        connect_with_lock(fail_connect)
    assert exc_info.value is expected

    marker = object()
    assert connect_with_lock(lambda: marker) is marker


def test_profile_connect_open_is_serial_but_sql_remains_concurrent(
    tmp_path, monkeypatch,
):
    """30 profile workers only serialise connect(), not connection usage."""
    from xskill.recommend import _sqlite_base
    from xskill.recommend.profile_store import ProfileStore

    store = ProfileStore(tmp_path / "profiles.db")
    connect_probe, execute_probe = _slow_connect_probe(
        monkeypatch, _sqlite_base.sqlite3,
    )
    barrier = threading.Barrier(30)

    def load_profile(index: int) -> None:
        barrier.wait(timeout=10)
        assert store.load(f"client-{index:03d}") is None

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(load_profile, range(30)))

    assert connect_probe.max_active == 1
    assert execute_probe.max_active > 1


def test_registry_connect_open_is_serial_but_sql_remains_concurrent(
    tmp_path, monkeypatch,
):
    """Registry connection bursts use the same narrow connect-only lock."""
    from xskill.pipeline import registry

    db_path = tmp_path / "registry.db"
    registry.get_connection(db_path).close()
    connect_probe, execute_probe = _slow_connect_probe(monkeypatch, registry.sqlite3)
    barrier = threading.Barrier(30)

    def open_registry(_index: int) -> None:
        barrier.wait(timeout=10)
        conn = registry.get_connection(db_path)
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=30) as executor:
        list(executor.map(open_registry, range(30)))

    assert connect_probe.max_active == 1
    assert execute_probe.max_active > 1


def test_server_sqlite_call_sites_share_process_wide_connect_open_lock(
    tmp_path, monkeypatch,
):
    """Profile, registry, client auth and dashboard share one open guard."""
    from xskill.dashboard.explore import users_status
    from xskill.pipeline import registry
    from xskill.recommend import _sqlite_base
    from xskill.recommend.profile_store import ProfileStore
    from xskill.team.server.client_registry import ClientRegistry

    profile_store = ProfileStore(tmp_path / "profiles.db")
    registry_path = tmp_path / "registry.db"
    registry.get_connection(registry_path).close()
    client_registry = ClientRegistry(tmp_path / "team_clients.db")
    # All call sites import the same sqlite3 module, so one probe observes
    # every database-open path in this server workload.
    connect_probe, execute_probe = _slow_connect_probe(
        monkeypatch, _sqlite_base.sqlite3,
    )
    barrier = threading.Barrier(40)

    def open_from_server_modules(index: int) -> None:
        barrier.wait(timeout=10)
        if index % 4 == 0:
            assert profile_store.load(f"client-{index:03d}") is None
            return
        if index % 4 == 1:
            conn = registry.get_connection(registry_path)
        elif index % 4 == 2:
            conn = client_registry._conn()
        else:
            assert users_status(registry_path)["users"] == []
            return
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()

    try:
        with ThreadPoolExecutor(max_workers=40) as executor:
            list(executor.map(open_from_server_modules, range(40)))
    finally:
        client_registry.close()

    assert connect_probe.max_active == 1
    assert execute_probe.max_active > 1


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
