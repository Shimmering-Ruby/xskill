"""技能详情「当前推送对象」routing API + pin side 覆盖。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.auth import (
    build_auth_router, configure_auth, ensure_dashboard_secret,
)
from xskill.dashboard import console as console_mod
from xskill.dashboard.console import build_console_router
from xskill.pipeline import registry as R
from xskill.team.server.api import init_team_context
from xskill.team.server.client_registry import ClientRegistry
from xskill.team.server.skill_manifest import _reset_manifest_cache_for_tests


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd),
                   capture_output=True, text=True, check=True)


def _make_skill(root: Path, name: str, *, with_staging: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d)
    _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d)
    _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "v1"], d)
    if with_staging:
        _git(["checkout", "-q", "-b", "staging"], d)
        (d / "SKILL.md").write_text(f"# {name} staging\n", encoding="utf-8")
        _git(["add", "."], d)
        _git(["commit", "-q", "-m", "staging"], d)
        _git(["checkout", "-q", "main"], d)
    return d


@pytest.fixture()
def routing_env(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    for n in ("alpha", "beta", "gamma"):
        _make_skill(skills, n, with_staging=(n == "alpha"))
    db = tmp_path / "r.db"
    reg = ClientRegistry(tmp_path / "c.db")
    alice_cid = reg.register(user_name="alice")
    bob_cid = reg.register(user_name="bob")
    alice_token = reg.ensure_dashboard_token(alice_cid)

    def configure_watch_dir(path: Path, label: str, auto_index: bool) -> None:
        R.register_dir(
            path, label=label, auto_index=auto_index,
            ecosystem="team_client", db_path=db,
        )

    init_team_context(
        join_token="jt", client_registry=reg, skill_dir=skills,
        traj_root=tmp_path / "traj",
        register_dir=lambda path, label: configure_watch_dir(path, label, True),
        configure_watch_dir=configure_watch_dir)
    from xskill.api import app as app_mod
    app_mod._config = {
        "team": {"server": {"skill_slots": 3, "ranked_slots": 2}},
        "canary": {"probability": 0.5},
    }
    configure_auth(
        secret=ensure_dashboard_secret(tmp_path / "sec.json"),
        admins=["boss"], admin_password="pw",
        registry_provider=lambda: reg)
    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_console_router(db_path=db))
    _reset_manifest_cache_for_tests()
    console_mod._bump_routing_epoch()

    alice = TestClient(app)
    assert alice.post("/api/v1/dashboard/login",
                      json={"user_name": "alice", "secret": alice_token}
                      ).status_code == 200
    boss = TestClient(app)
    assert boss.post("/api/v1/dashboard/login",
                     json={"user_name": "boss", "secret": "pw"}
                     ).status_code == 200
    return {
        "alice": alice, "boss": boss, "db": db, "skills": skills,
        "registry": reg, "alice_cid": alice_cid, "bob_cid": bob_cid,
    }


def test_routing_meta_and_users(routing_env):
    boss = routing_env["boss"]
    r = boss.get("/api/v1/dashboard/skill/alpha/routing")
    assert r.status_code == 200
    meta = r.json()
    assert meta["skill"] == "alpha"
    assert meta["has_staging"] is True
    assert "counts" in meta
    assert "staging" not in meta  # 全量列表禁止出现在 meta
    assert meta["counts"]["users"] == 2
    assert meta["counts"]["in_manifest"] == meta["counts"]["staging"] + meta["counts"]["main"]

    ru = boss.get("/api/v1/dashboard/skill/alpha/routing/users",
                  params={"filter": "in", "limit": 8})
    assert ru.status_code == 200
    body = ru.json()
    assert body["total"] == meta["counts"]["in_manifest"]
    assert len(body["users"]) <= 8
    names = {u["user"] for u in body["users"]}
    assert names <= {"alice", "bob"}
    for u in body["users"]:
        assert u["in_manifest"] is True
        assert u["side"] in ("main", "staging")
        assert u["sha"]


def test_routing_pin_side_override(routing_env):
    boss = routing_env["boss"]
    r = boss.post("/api/v1/dashboard/admin/prefs", json={
        "user_key": "alice", "skill_name": "alpha",
        "action": "pin", "side": "staging",
    })
    assert r.status_code == 200, r.text

    row = boss.get("/api/v1/dashboard/skill/alpha/routing/user/alice").json()
    assert row["in_manifest"] is True
    assert row["side"] == "staging"
    assert row["overridden"] is True
    assert row["pinned"] is True

    stg = boss.get("/api/v1/dashboard/skill/alpha/routing/users",
                   params={"filter": "staging"}).json()
    assert any(u["user"] == "alice" for u in stg["users"])

    # clear_side 保留 pin，清覆盖
    assert boss.post("/api/v1/dashboard/admin/prefs", json={
        "user_key": "alice", "skill_name": "alpha", "action": "clear_side",
    }).status_code == 200
    row2 = boss.get("/api/v1/dashboard/skill/alpha/routing/user/alice").json()
    assert row2["pinned"] is True
    assert row2["overridden"] is False


def test_routing_typeahead_and_404(routing_env):
    alice = routing_env["alice"]
    q = alice.get("/api/v1/dashboard/skill/alpha/routing/users",
                  params={"q": "ali", "limit": 8})
    assert q.status_code == 200
    hits = q.json()["users"]
    assert any(u["user"] == "alice" for u in hits)

    assert alice.get("/api/v1/dashboard/skill/nope/routing").status_code == 404


def test_user_can_pin_own_side_via_my_prefs(routing_env):
    alice = routing_env["alice"]
    r = alice.post("/api/v1/dashboard/my/prefs", json={
        "skill_name": "alpha", "action": "pin", "side": "main",
    })
    assert r.status_code == 200, r.text
    row = alice.get("/api/v1/dashboard/skill/alpha/routing/user/alice").json()
    assert row["side"] == "main"
    assert row["overridden"] is True
    assert row["pinned"] is True


def test_no_staging_skill_routing(routing_env):
    boss = routing_env["boss"]
    meta = boss.get("/api/v1/dashboard/skill/beta/routing").json()
    assert meta["has_staging"] is False
    assert meta["counts"]["staging"] == 0
    users = boss.get("/api/v1/dashboard/skill/beta/routing/users",
                     params={"filter": "main"}).json()
    for u in users["users"]:
        assert u["side"] == "main"


def test_admin_assignment_and_matrix_slots(routing_env):
    boss = routing_env["boss"]
    assert boss.post("/api/v1/dashboard/admin/prefs", json={
        "user_key": "alice", "skill_name": "alpha",
        "action": "pin", "side": "staging",
    }).status_code == 200
    assign = boss.get("/api/v1/dashboard/admin/user/alice/assignment").json()
    assert assign["user"] == "alice"
    assert assign["slots"]
    alpha = next(s for s in assign["slots"] if s["skill_name"] == "alpha")
    assert alpha["side"] == "staging"
    assert alpha["side_mutable"] is True
    assert alpha["overridden"] is True

    matrix = boss.get("/api/v1/dashboard/admin/users-matrix").json()
    alice = next(u for u in matrix["users"] if u["user"] == "alice")
    assert alice["current_slots"] >= 1
    assert alice["staging_slots"] >= 1
    assert "exposures" in alice  # 历史字段仍保留


def test_skills_q_substring(routing_env):
    alice = routing_env["alice"]
    # /skills 挂在 dashboard router（非 console）；走同一 app 的公开只读端点
    from xskill.dashboard.router import build_dashboard_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=routing_env["db"]))
    client = TestClient(app)
    # 投影表依赖 skill_dir=db 旁 skill/；此处仅校验 q 参数被接受
    r = client.get("/api/v1/dashboard/skills", params={"q": "alp", "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert "skills" in body
    assert "total" in body
