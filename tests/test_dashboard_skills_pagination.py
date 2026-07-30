"""海量 skill 分页:/skills 支持 limit/offset 分页 + name 定向查,total/by_state 按全量。

分页走投影表 :func:`skills_catalog_page`；测试在扫盘 backfill 入口注入假清单。
"""
from __future__ import annotations

from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import xskill.skill.catalog_store as catalog_store
from xskill.dashboard import router as router_mod
from xskill.dashboard.mount import mount_dashboard
from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline.registry import get_connection


def _fake_catalog_rows(skill_dir, n):
    root_key = catalog_store.catalog_root_key(skill_dir)
    rows = []
    for index in range(n):
        name = f"s{index:04d}"
        rows.append({
            "catalog_key": f"native:{name}",
            "root_key": root_key,
            "name": name,
            "repo_name": name,
            "source": "native",
            "state": "main" if index % 2 else "staging",
            "description": "",
            "version": 1,
            "candidates": 0,
            "candidates_count": 0,
            "main_sha": "",
            "staging_sha": "",
            "distributable": 1,
            "search_id": name,
            "hub": "",
            "skill_id": "",
            "use_count": 0,
        })
    return rows


def _client(tmp_path, monkeypatch, n):
    db = tmp_path / "r.db"
    get_connection(db).close()
    monkeypatch.setattr(
        "xskill.config.get_registry_db_path",
        lambda: tmp_path / "_skills_catalog_registry.db",
    )
    monkeypatch.setattr(
        "xskill.pipeline.registry.get_registry_db_path",
        lambda: tmp_path / "_skills_catalog_registry.db",
    )
    app = FastAPI()
    mount_dashboard(app, {"dashboard": {"enabled": True, "public": True}}, db_path=db)

    def fake_scan(skill_dir, skillhub=None):  # noqa: ARG001
        return _fake_catalog_rows(skill_dir, n)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", fake_scan)
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
    assert body["skills"][0]["name"] == "s0100"
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


def test_standalone_projects_10000_skills_for_all_page_and_name(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xskill.config.get_registry_db_path",
        lambda: tmp_path / "_skills_catalog_registry.db",
    )
    monkeypatch.setattr(
        "xskill.pipeline.registry.get_registry_db_path",
        lambda: tmp_path / "_skills_catalog_registry.db",
    )

    def fake_scan(skill_dir, skillhub=None):  # noqa: ARG001
        return _fake_catalog_rows(skill_dir, 10000)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", fake_scan)
    monkeypatch.setattr(
        router_mod,
        "_build_skillhub",
        Mock(return_value=None),
    )
    router = build_dashboard_router(
        db_path=tmp_path / "r.db",
        default_harness="unknown",
        default_model="unknown",
        expose_sensitive=False,
    )
    skills_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/v1/dashboard/skills"
    )

    all_skills = skills_endpoint()
    assert all_skills["total"] == 10000
    assert len(all_skills["skills"]) == 10000
    assert set(all_skills["skills"][0]) == {
        "name", "state", "source", "version", "candidates",
    }

    page = skills_endpoint(limit=100, offset=4200)
    assert len(page["skills"]) == 100
    assert page["skills"][0]["name"] == "s4200"
    assert page["offset"] == 4200
    assert page["limit"] == 100

    named = skills_endpoint(name="s4242")
    assert named["total"] == 10000
    assert named["skills"] == [{
        "name": "s4242",
        "state": "staging",
        "source": "native",
        "version": 1,
        "candidates": 0,
    }]


def test_standalone_projection_reuses_catalog_page_list(tmp_path, monkeypatch):
    source_rows = [
        {
            "name": f"s{i}",
            "state": "main" if i % 2 else "staging",
            "version": "1",
            "candidates": 0,
            "source": "native",
            "description": "",
        }
        for i in range(10000)
    ]
    original_list = source_rows
    original_first_row = source_rows[0]
    page = {
        "total": 10000,
        "by_state": {"main": 5000, "staging": 5000},
        "offset": 0,
        "limit": 0,
        "skills": source_rows,
    }
    monkeypatch.setattr(
        router_mod,
        "skills_catalog_page",
        Mock(return_value=page),
    )
    monkeypatch.setattr(
        router_mod,
        "_build_skillhub",
        Mock(return_value=None),
    )
    router = build_dashboard_router(
        db_path=tmp_path / "r.db",
        default_harness="unknown",
        default_model="unknown",
        expose_sensitive=False,
    )
    skills_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/v1/dashboard/skills"
    )
    body = skills_endpoint()
    assert body is page
    assert body["skills"] is original_list
    assert body["skills"][0] is not original_first_row
    assert len(body["skills"]) == 10000
