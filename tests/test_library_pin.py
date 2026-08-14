"""技能库登录后带 pinned / in_push，供空心星 pin。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.auth import (
    build_auth_router, configure_auth, ensure_dashboard_secret,
)
from xskill.dashboard.console import build_console_router
from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline import registry as R
from xskill.team.server.api import init_team_context
from xskill.team.server.client_registry import ClientRegistry


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd),
                   capture_output=True, text=True, check=True)


def _make_skill(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d)
    _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d)
    _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "v1"], d)
    return d


def _library_client(tmp_path, monkeypatch):
    skills = tmp_path / "skill"
    skills.mkdir()
    for name in ("alpha", "beta"):
        _make_skill(skills, name)
    db = tmp_path / "r.db"
    R.get_connection(db).close()
    monkeypatch.setattr("xskill.config.get_registry_db_path", lambda: db)
    monkeypatch.setattr(
        "xskill.pipeline.registry.get_registry_db_path", lambda: db)
    reg = ClientRegistry(tmp_path / "c.db")
    cid = reg.register(user_name="alice")
    token = reg.ensure_dashboard_token(cid)
    init_team_context(
        join_token="jt", client_registry=reg, skill_dir=skills,
        traj_root=tmp_path / "traj",
        register_dir=lambda path, label: None)
    from xskill.api import app as app_mod
    app_mod._config = {"team": {"server": {"skill_slots": 3, "ranked_slots": 2}}}
    configure_auth(
        secret=ensure_dashboard_secret(tmp_path / "sec.json"),
        admins=["boss"], admin_password="pw",
        registry_provider=lambda: reg)
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db))
    app.include_router(build_auth_router())
    app.include_router(build_console_router(db_path=db))
    anon = TestClient(app)
    alice = TestClient(app)
    assert alice.post("/api/v1/dashboard/login",
                      json={"user_name": "alice", "secret": token}
                      ).status_code == 200
    return {"anon": anon, "alice": alice, "db": db}


def test_anonymous_skills_have_no_pin_fields(tmp_path, monkeypatch):
    env = _library_client(tmp_path, monkeypatch)
    body = env["anon"].get("/api/v1/dashboard/skills").json()
    assert body["total"] >= 2
    assert "viewer" not in body
    for row in body["skills"]:
        assert "pinned" not in row
        assert "in_push" not in row


def test_logged_in_skills_include_pin_state(tmp_path, monkeypatch):
    env = _library_client(tmp_path, monkeypatch)
    alice, db = env["alice"], env["db"]
    R.set_skill_pref(user_key="alice", skill_name="alpha", pref="pinned",
                     set_by="alice", db_path=db)
    body = alice.get("/api/v1/dashboard/skills").json()
    assert body["viewer"]["can_pin"] is True
    by_name = {row["name"]: row for row in body["skills"]}
    assert by_name["alpha"]["pinned"] is True
    assert by_name["alpha"]["in_push"] is True
    assert by_name["alpha"]["user_removable"] is True
    assert by_name["beta"]["pinned"] is False


def test_library_pin_via_prefs_then_skills_list(tmp_path, monkeypatch):
    env = _library_client(tmp_path, monkeypatch)
    alice = env["alice"]
    r = alice.post("/api/v1/dashboard/my/prefs",
                   json={"skill_name": "beta", "action": "pin"})
    assert r.status_code == 200, r.text
    body = alice.get("/api/v1/dashboard/skills").json()
    by_name = {row["name"]: row for row in body["skills"]}
    assert by_name["beta"]["pinned"] is True
    assert by_name["beta"]["in_push"] is True
