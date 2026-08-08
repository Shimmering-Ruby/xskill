"""First-use / uninitialized skill dir: status + skill search (#46 residue)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import xskill.api.app as api_app


def _configure_api(monkeypatch, tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(api_app, "_skill_dir", skill_dir)
    monkeypatch.setattr(api_app, "_config", {
        "llm": {},
        "embedding": {},
        "watcher": {"poll_interval": 30},
    })
    app = FastAPI()
    app.include_router(api_app.router)
    return app, skill_dir


def test_api_skill_search_missing_index_skips_embedding_client(monkeypatch, tmp_path):
    app, _skill_dir = _configure_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api_app,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    resp = TestClient(app).post(
        "/api/v1/skills/search", json={"query": "heartbeat", "top_k": 2},
    )

    assert resp.status_code == 200
    assert resp.json() == []


def test_api_skill_resolve_missing_index_skips_embedding_client(monkeypatch, tmp_path):
    app, _skill_dir = _configure_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api_app,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    resp = TestClient(app).post(
        "/api/v1/skills/resolve", json={"query": "heartbeat"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "skill_name": None, "path": None, "side": "none", "sha": "",
    }


def test_sdk_skill_search_missing_index_skips_embedding_client(monkeypatch, tmp_path):
    from xskill import core
    from xskill.utils import llm

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(core, "load_config", lambda config_path=None: {"embedding": {}})
    monkeypatch.setattr(core, "get_skill_dir", lambda: skill_dir)
    monkeypatch.setattr(
        llm,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    assert core.XSkill().search_skills("heartbeat", top_k=2) == []
