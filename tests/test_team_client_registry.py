from __future__ import annotations

import concurrent.futures
import threading

from xskill.team.server.client_registry import ClientRegistry


def test_register_returns_unique_ids(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    a = reg.register(label="alice-laptop", hostname="alice")
    b = reg.register(label="bob-laptop", hostname="bob")
    assert a != b
    assert reg.exists(a) and reg.exists(b)
    assert not reg.exists("nonexistent")


def test_touch_updates_last_seen(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    cid = reg.register(label="x", hostname="x")
    before = reg.get(cid)["last_seen"]
    reg.touch(cid)
    after = reg.get(cid)["last_seen"]
    assert after >= before


def test_list_returns_all(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    reg.register(label="a", hostname="a")
    reg.register(label="b", hostname="b")
    rows = reg.list()
    assert len(rows) == 2
    assert {r["label"] for r in rows} == {"a", "b"}


def test_registry_connections_use_wal_normal_and_busy_timeout(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    conn = reg._conn()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
    finally:
        conn.close()


def test_authenticate_and_touch_is_atomic_and_preserves_version(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    cid = reg.register(label="x", hostname="x", client_version="1.0")

    assert reg.authenticate_and_touch(cid, None) is True
    assert reg.get(cid)["client_version"] == "1.0"
    assert reg.authenticate_and_touch("nonexistent", "2.0") is False


def test_100_concurrent_authenticate_and_touch_calls_succeed(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    cid = reg.register(label="x", hostname="x")
    worker_count = 100
    start = threading.Barrier(worker_count)

    def authenticate() -> bool:
        start.wait(timeout=30)
        return reg.authenticate_and_touch(cid, "1.2.3")

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(authenticate) for _ in range(worker_count)]
        results = [future.result(timeout=30) for future in futures]

    assert results == [True] * worker_count
