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
