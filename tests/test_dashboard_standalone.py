"""块 1 独立只读看板进程:serve_builtin=False 只挂只读路由,不挂 auth/console/敏感端点。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.mount import mount_dashboard
from xskill.pipeline.registry import get_connection

_CFG = {"dashboard": {"enabled": True, "public": True}}


def _paths(app) -> set[str]:
    return {getattr(route, "path", None) for route in app.routes}


def _build(tmp_path, *, serve_builtin: bool) -> FastAPI:
    db = tmp_path / "r.db"
    get_connection(db).close()
    app = FastAPI()
    mount_dashboard(app, _CFG, serve_builtin=serve_builtin, db_path=db)
    return app


def test_standalone_serves_readonly_aggregate(tmp_path):
    app = _build(tmp_path, serve_builtin=False)
    assert TestClient(app).get("/api/v1/dashboard/overview").status_code == 200


def test_standalone_hides_sensitive_and_write_routes(tmp_path):
    app = _build(tmp_path, serve_builtin=False)
    paths = _paths(app)
    assert "/api/v1/dashboard/overview" in paths  # 聚合只读端点仍在
    assert "/api/v1/dashboard/users" not in paths  # 敏感内容端点物理不注册
    assert "/api/v1/dashboard/login" not in paths  # 登录写路由不挂载
    # TestClient 实访敏感端点 → 404
    assert TestClient(app).get("/api/v1/dashboard/users").status_code == 404


def test_serve_builtin_mounts_sensitive_and_auth(tmp_path):
    """对照:serve_builtin=True(api 进程形态)才挂敏感端点与登录路由。"""
    paths = _paths(_build(tmp_path, serve_builtin=True))
    assert "/api/v1/dashboard/users" in paths
    assert "/api/v1/dashboard/login" in paths
