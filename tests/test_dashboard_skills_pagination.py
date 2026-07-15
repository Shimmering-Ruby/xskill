"""海量 skill 分页:/skills 支持 limit/offset 分页 + name 定向查,total/by_state 按全量。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard import router as router_mod
from xskill.dashboard.mount import mount_dashboard
from xskill.pipeline.registry import get_connection


def _fake_catalog(n):
    return [
        {
            "name": f"s{i}",
            "state": "main" if i % 2 else "staging",
            "version": "1",
            "candidates": 0,
            "source": "native",
            "description": "",
        }
        for i in range(n)
    ]


def _client(tmp_path, monkeypatch, n):
    db = tmp_path / "r.db"
    get_connection(db).close()
    app = FastAPI()
    mount_dashboard(app, {"dashboard": {"enabled": True, "public": True}}, db_path=db)

    def fake_skills_catalog(*_args, **_kwargs):
        return _fake_catalog(n)

    monkeypatch.setattr(router_mod, "skills_catalog", fake_skills_catalog)
    return TestClient(app)


def test_default_returns_all_backward_compatible(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, 250).get("/api/v1/dashboard/skills").json()
    assert body["total"] == 250
    assert len(body["skills"]) == 250  # limit=0 默认返回全部(向后兼容)


def test_limit_offset_returns_one_page(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, 250).get(
        "/api/v1/dashboard/skills?limit=100&offset=100").json()
    assert body["total"] == 250  # total 仍按全量
    assert len(body["skills"]) == 100
    assert body["skills"][0]["name"] == "s100"
    assert body["offset"] == 100 and body["limit"] == 100


def test_by_state_counts_full_catalog_not_page(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, 250).get(
        "/api/v1/dashboard/skills?limit=10").json()
    assert sum(body["by_state"].values()) == 250  # 概览计数按全量,不受分页影响


def test_name_filter_returns_single_skill(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, 10000).get(
        "/api/v1/dashboard/skills?name=s4242").json()
    assert len(body["skills"]) == 1  # 1 万 skill 里定向查一条,不返回全量
    assert body["skills"][0]["name"] == "s4242"
    assert body["total"] == 10000
