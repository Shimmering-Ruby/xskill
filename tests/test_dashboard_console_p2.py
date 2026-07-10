"""test_dashboard_console_p2.py —— P2 控制面（登录/角色/prefs/manifest 注入/生命周期/设置页）

覆盖 openspec dashboard-console-redesign P2 的 SHALL 条款：
- 2.2 登录:匿名 401 / 普通用户写 admin 端点 403 / admin 口令空=关闭
- 2.4 注入顺序:blocked 排除 → pinned 占位 → ranked → recommended 回填
- 2.4d 超量写入侧拒绝(409),含全局 pin 全员合计
- 2.4c retire 不分发不裁决;delete 需二次确认,prefs 清理
- 2.9 校验失败不落盘不生效
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.auth import (
    build_auth_router, configure_auth, ensure_dashboard_secret,
)
from xskill.dashboard.console import build_console_router
from xskill.pipeline import registry as R
from xskill.team.server.api import init_team_context
from xskill.team.server.client_registry import ClientRegistry
from xskill.team.server.skill_manifest import build_manifest


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


@pytest.fixture()
def console_env(tmp_path):
    """team ctx + auth + console app。alice=普通用户,boss=admin。"""
    skills = tmp_path / "skills"
    skills.mkdir()
    _git(["init", "-q"], skills)
    _git(["checkout", "-q", "-b", "main"], skills)
    _git(["config", "user.email", "t@t"], skills)
    _git(["config", "user.name", "t"], skills)
    for n in ("alpha", "beta", "gamma"):
        _make_skill(skills, n)
    db = tmp_path / "r.db"
    reg = ClientRegistry(tmp_path / "c.db")
    cid = reg.register(user_name="alice")
    token = reg.ensure_dashboard_token(cid)
    init_team_context(
        join_token="jt", client_registry=reg, skill_dir=skills,
        traj_root=tmp_path / "traj", probability=0.2, ranked_slots=2,
        total_slots=3, register_dir=lambda p, l: None)
    configure_auth(
        secret=ensure_dashboard_secret(tmp_path / "sec.json"),
        admins=["boss"], admin_password="pw",
        registry_provider=lambda: reg)
    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_console_router(db_path=db))

    alice = TestClient(app)
    r = alice.post("/api/v1/dashboard/login",
                   json={"user_name": "alice", "secret": token})
    assert r.status_code == 200 and r.json()["role"] == "user"
    boss = TestClient(app)
    r = boss.post("/api/v1/dashboard/login",
                  json={"user_name": "boss", "secret": "pw"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    return {"app": app, "alice": alice, "boss": boss, "db": db,
            "skills": skills, "token": token, "registry": reg}


# ── 2.2 登录/角色 ─────────────────────────────────────────────────────

def test_anonymous_gets_401(console_env):
    anon = TestClient(console_env["app"])
    assert anon.get("/api/v1/dashboard/my/manifest").status_code == 401
    assert anon.get("/api/v1/dashboard/admin/skills").status_code == 401


def test_wrong_credentials_401(console_env):
    anon = TestClient(console_env["app"])
    assert anon.post("/api/v1/dashboard/login",
                     json={"user_name": "alice", "secret": "bad"}
                     ).status_code == 401
    # admin 名单里的人用错口令也 401(token 路径不给 admin 角色)
    assert anon.post("/api/v1/dashboard/login",
                     json={"user_name": "boss", "secret": "bad"}
                     ).status_code == 401


def test_user_hitting_admin_endpoint_403(console_env):
    alice = console_env["alice"]
    assert alice.get("/api/v1/dashboard/admin/skills").status_code == 403
    assert alice.post(
        "/api/v1/dashboard/admin/skill/alpha/retire").status_code == 403
    assert alice.get("/api/v1/dashboard/admin/config").status_code == 403


def test_logout_invalidates_session(console_env):
    boss = console_env["boss"]
    assert boss.get("/api/v1/dashboard/admin/skills").status_code == 200
    boss.post("/api/v1/dashboard/logout")
    assert boss.get("/api/v1/dashboard/admin/skills").status_code == 401


def test_dashboard_token_issued_and_idempotent(console_env):
    reg = console_env["registry"]
    cid = reg.find_by_user_name("alice")
    assert reg.ensure_dashboard_token(cid) == console_env["token"]
    assert reg.dashboard_token_for("alice") == console_env["token"]


# ── 2.4 manifest 注入顺序 ─────────────────────────────────────────────

def test_injection_order_blocked_pinned_ranked_recommended(console_env):
    db, skills = console_env["db"], console_env["skills"]
    R.set_skill_pref(user_key="alice", skill_name="gamma", pref="pinned",
                     set_by="alice", db_path=db)
    R.set_skill_pref(user_key="alice", skill_name="alpha", pref="blocked",
                     set_by="alice", db_path=db)
    prefs = R.effective_prefs("alice", db_path=db)
    resp = build_manifest(client_id="c", skill_dir=skills, probability=0.2,
                          ranked_slots=2, total_slots=3,
                          prefs=prefs, retired=set())
    got = [(s.skill_name, s.bucket) for s in resp.slots]
    assert got[0] == ("gamma", "pinned")
    assert all(n != "alpha" for n, _ in got)


def test_global_pin_ordered_before_user_pin(console_env):
    db = console_env["db"]
    R.set_skill_pref(user_key="alice", skill_name="beta", pref="pinned",
                     set_by="alice", db_path=db)
    R.set_skill_pref(user_key=R.GLOBAL_PREF_KEY, skill_name="gamma",
                     pref="pinned", set_by="boss", db_path=db)
    prefs = R.effective_prefs("alice", db_path=db)
    assert prefs["pinned"] == ["gamma", "beta"]
    assert prefs["pin_meta"]["gamma"]["scope"] == "global"


def test_retired_not_distributed_even_if_pinned(console_env):
    db, skills = console_env["db"], console_env["skills"]
    R.set_skill_pref(user_key="alice", skill_name="gamma", pref="pinned",
                     set_by="alice", db_path=db)
    resp = build_manifest(client_id="c", skill_dir=skills, probability=0.2,
                          ranked_slots=2, total_slots=3,
                          prefs=R.effective_prefs("alice", db_path=db),
                          retired={"gamma"})
    assert all(s.skill_name != "gamma" for s in resp.slots)


# ── 2.4d 写入侧超量拒绝 ───────────────────────────────────────────────

def test_pin_quota_rejected_at_write_side(console_env):
    alice, boss = console_env["alice"], console_env["boss"]
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "alpha", "action": "pin"}
                      ).status_code == 200
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "beta", "action": "pin"}
                      ).status_code == 200
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "gamma", "action": "pin"}
                      ).status_code == 200
    # 第 4 个超 total_slots=3 → 409
    r = alice.post("/api/v1/dashboard/my/prefs",
                   json={"skill_name": "s4", "action": "pin"})
    assert r.status_code == 409
    # 全局 pin 会把 alice 顶爆 → 对全员合计校验后 409
    r = boss.post("/api/v1/dashboard/admin/prefs",
                  json={"user_key": R.GLOBAL_PREF_KEY,
                        "skill_name": "s5", "action": "pin"})
    assert r.status_code == 409


def test_admin_set_pref_immutable_by_user(console_env):
    alice, boss = console_env["alice"], console_env["boss"]
    assert boss.post("/api/v1/dashboard/admin/prefs",
                     json={"user_key": "alice", "skill_name": "beta",
                           "action": "pin"}).status_code == 200
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "beta", "action": "clear"}
                      ).status_code == 403
    # 全局 pin 的条目不可屏蔽
    assert boss.post("/api/v1/dashboard/admin/prefs",
                     json={"user_key": R.GLOBAL_PREF_KEY,
                           "skill_name": "alpha", "action": "pin"}
                     ).status_code == 200
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "alpha", "action": "block"}
                      ).status_code == 403


# ── 2.4c 生命周期 ────────────────────────────────────────────────────

def test_retire_stops_distribution_and_canary(console_env, tmp_path):
    alice, boss = console_env["alice"], console_env["boss"]
    assert boss.post(
        "/api/v1/dashboard/admin/skill/alpha/retire").status_code == 200
    m = alice.get("/api/v1/dashboard/my/manifest").json()
    assert all(s["skill_name"] != "alpha" for s in m["slots"])
    # canary 判定直接短路——check_and_decide 读默认注册库(生产=同一个库;
    # 本 fixture 的 console 用独立 tmp 库,故这里在默认库上单独验证)
    from xskill.canary import check_and_decide
    R.retire_skill(skill_name="alpha", set_by="t")
    try:
        assert check_and_decide(
            console_env["skills"] / "alpha")["action"] == "retired"
    finally:
        R.unretire_skill(skill_name="alpha")
    # 恢复在役
    assert boss.post(
        "/api/v1/dashboard/admin/skill/alpha/unretire").status_code == 200
    m = alice.get("/api/v1/dashboard/my/manifest").json()
    assert any(s["skill_name"] == "alpha" for s in m["slots"])


def test_delete_requires_confirm_and_purges_prefs(console_env):
    alice, boss, db = (console_env["alice"], console_env["boss"],
                       console_env["db"])
    assert alice.post("/api/v1/dashboard/my/prefs",
                      json={"skill_name": "gamma", "action": "pin"}
                      ).status_code == 200
    r = boss.request("DELETE", "/api/v1/dashboard/admin/skill/gamma",
                     json={"confirm_name": "not-gamma"})
    assert r.status_code == 400
    r = boss.request("DELETE", "/api/v1/dashboard/admin/skill/gamma",
                     json={"confirm_name": "gamma"})
    assert r.status_code == 200
    assert not (console_env["skills"] / "gamma").exists()
    assert all(p["skill_name"] != "gamma"
               for p in R.prefs_for("alice", db_path=db))


# ── 2.3/2.5/2.6 读视图 ──────────────────────────────────────────────

def test_my_views_empty_db_do_not_crash(console_env):
    alice = console_env["alice"]
    c = alice.get("/api/v1/dashboard/my/contributions")
    assert c.status_code == 200
    assert c.json()["steps"]["trajs"] == 0
    assert alice.get("/api/v1/dashboard/my/reco-trigger").json()["rows"] == []


def test_users_matrix_lists_clients_with_version(console_env):
    boss = console_env["boss"]
    reg = console_env["registry"]
    cid = reg.find_by_user_name("alice")
    reg.touch(cid, version="0.9.9")
    um = boss.get("/api/v1/dashboard/admin/users-matrix").json()
    row = next(u for u in um["users"] if u["user"] == "alice")
    assert row["client_version"] == "0.9.9"


# ── 2.9 设置页 ──────────────────────────────────────────────────────

def test_config_validate_and_reload(console_env, tmp_path, monkeypatch):
    import xskill.config as C
    from xskill.api import app as app_mod
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text("skill_dir: /tmp/s\nllm:\n  base_url: http://x/v1\n",
                    encoding="utf-8")
    monkeypatch.setattr(C, "CONFIG_PATH", cfgp)
    monkeypatch.setattr(app_mod, "_config",
                        {"skill_dir": "/tmp/s",
                         "llm": {"base_url": "http://x/v1"}})
    boss = console_env["boss"]
    # 坏 YAML → 400,不落盘不生效
    before = cfgp.read_text()
    r = boss.post("/api/v1/dashboard/admin/config/reload",
                  json={"raw": "llm: [broken"})
    assert r.status_code == 400 and cfgp.read_text() == before
    # canary 段热生效,llm 段标注需重启
    new = ("skill_dir: /tmp/s\nllm:\n  base_url: http://y/v1\n"
           "canary:\n  probability: 0.5\n")
    r = boss.post("/api/v1/dashboard/admin/config/reload", json={"raw": new})
    body = r.json()
    assert r.status_code == 200
    assert "canary" in body["hot_reloaded"]
    assert "llm" in body["needs_restart"]
    assert app_mod._config["canary"]["probability"] == 0.5
