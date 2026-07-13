"""免密登录链接（xskill dashboard）：签发 / 兑换 / 单次性 / 不可冒充会话。"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.cli import build_parser, cmd_dashboard
from xskill.dashboard.auth import (
    SESSION_COOKIE,
    build_auth_router,
    configure_auth,
    ensure_dashboard_secret,
    issue_login_link_token,
)
from xskill.team.server import api as server_api
from xskill.team.server.client_registry import ClientRegistry


@pytest.fixture()
def link_env(tmp_path):
    registry = ClientRegistry(tmp_path / "c.db")
    named_client_id = registry.register(user_name="alice")
    anonymous_client_id = registry.register()
    server_api.init_team_context(
        join_token="jt", client_registry=registry,
        skill_dir=tmp_path / "skills", traj_root=tmp_path / "traj",
        probability=0.2, ranked_slots=2, total_slots=3,
        register_dir=lambda p, l: None,
    )
    configure_auth(
        secret=ensure_dashboard_secret(tmp_path / "sec.json"),
        admins=[], admin_password="",
        registry_provider=lambda: registry,
    )
    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(server_api.router)
    return {
        "app": app,
        "named_client_id": named_client_id,
        "anonymous_client_id": anonymous_client_id,
    }


def _team_headers(client_id):
    return {"X-Xskill-Token": "jt", "X-Xskill-Client": client_id}


class TestIssueEndpoint:
    def test_named_client_gets_link(self, link_env):
        client = TestClient(link_env["app"])
        r = client.post("/api/v1/team/dashboard_link",
                        headers=_team_headers(link_env["named_client_id"]))
        assert r.status_code == 200
        assert r.json()["user"] == "alice"
        assert r.json()["path"].startswith("/api/v1/dashboard/login/link?t=")

    def test_anonymous_client_rejected(self, link_env):
        client = TestClient(link_env["app"])
        r = client.post("/api/v1/team/dashboard_link",
                        headers=_team_headers(link_env["anonymous_client_id"]))
        assert r.status_code == 400
        assert "--name" in r.json()["detail"]

    def test_bad_token_rejected(self, link_env):
        client = TestClient(link_env["app"])
        r = client.post("/api/v1/team/dashboard_link", headers={
            "X-Xskill-Token": "wrong",
            "X-Xskill-Client": link_env["named_client_id"],
        })
        assert r.status_code == 401


class TestRedeem:
    def _issue_path(self, link_env):
        client = TestClient(link_env["app"])
        r = client.post("/api/v1/team/dashboard_link",
                        headers=_team_headers(link_env["named_client_id"]))
        return r.json()["path"]

    def test_link_logs_in_as_self_and_redirects(self, link_env):
        path = self._issue_path(link_env)
        client = TestClient(link_env["app"])

        r = client.get(path, follow_redirects=False)

        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert SESSION_COOKIE in r.cookies
        me = client.get("/api/v1/dashboard/me")
        assert me.status_code == 200
        assert me.json() == {"user": "alice", "role": "user"}

    def test_link_is_single_use(self, link_env):
        path = self._issue_path(link_env)
        first_client = TestClient(link_env["app"])
        assert first_client.get(
            path, follow_redirects=False,
        ).status_code == 303

        second_client = TestClient(link_env["app"])
        r = second_client.get(path, follow_redirects=False)

        assert r.status_code == 401
        assert "已被使用" in r.json()["detail"]

    def test_tampered_token_rejected(self, link_env):
        path = self._issue_path(link_env)
        client = TestClient(link_env["app"])

        r = client.get(path[:-4] + "AAAA", follow_redirects=False)

        assert r.status_code == 401

    def test_expired_link_rejected(self, link_env, monkeypatch):
        path = self._issue_path(link_env)
        client = TestClient(link_env["app"])
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 601)

        r = client.get(path, follow_redirects=False)

        assert r.status_code == 401

    def test_link_token_cannot_be_used_as_session_cookie(self, link_env):
        link_token = issue_login_link_token("alice")
        client = TestClient(link_env["app"], cookies={SESSION_COOKIE: link_token})

        me = client.get("/api/v1/dashboard/me")

        assert me.status_code == 401, "ott token 直接当会话 cookie 必须被拒"


class TestCmdDashboard:
    def test_prints_clickable_url(self, monkeypatch, tmp_path, capsys):
        import json
        state_file = tmp_path / "team_client.json"
        state_file.write_text(json.dumps({
            "server_url": "http://srv:8000",
            "client_id": "cid",
            "join_token": "jt",
        }), encoding="utf-8")
        monkeypatch.setattr(
            "xskill.config.get_team_client_state_path", lambda: state_file,
        )

        class _FakeResponse:
            def read(self):
                return json.dumps({
                    "user": "alice",
                    "path": "/api/v1/dashboard/login/link?t=abc.def",
                }).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        captured_requests: list = []

        def fake_urlopen(request, timeout=0):
            captured_requests.append(request)
            return _FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        args = build_parser().parse_args(["dashboard"])
        assert cmd_dashboard(args) == 0
        printed = capsys.readouterr().out
        assert "http://srv:8000/api/v1/dashboard/login/link?t=abc.def" in printed
        assert "alice" in printed
        assert captured_requests[0].headers["X-xskill-token"] == "jt"

    def test_no_state_and_no_local_serve_errors(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(
            "xskill.config.get_team_client_state_path",
            lambda: tmp_path / "absent.json",
        )
        monkeypatch.setattr("xskill.runtime.read_status", lambda: {"running": False})

        args = build_parser().parse_args(["dashboard"])
        assert cmd_dashboard(args) == 1
        assert "connect" in capsys.readouterr().err


def test_issue_endpoint_503_when_team_context_missing(link_env, monkeypatch):
    """standalone（无 team ctx）：签发端点须 503 而非 500。"""
    monkeypatch.setattr(server_api._ctx, "client_registry", None)
    client = TestClient(link_env["app"])

    r = client.post("/api/v1/team/dashboard_link",
                    headers=_team_headers(link_env["named_client_id"]))

    assert r.status_code == 503
