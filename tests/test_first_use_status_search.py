"""First-use / uninitialized skill dir: status + skill search (#46 residue)."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

import xskill.api.app as api_app


def _configure_api(monkeypatch, tmp_path, *, embedding=None):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(api_app, "_skill_dir", skill_dir)
    monkeypatch.setattr(api_app, "_config", {
        "llm": {},
        "embedding": embedding if embedding is not None else {},
        "watcher": {"poll_interval": 30},
    })
    app = FastAPI()
    app.include_router(api_app.router)
    return app, skill_dir


def test_api_skill_search_missing_index_skips_embedding_client(
    monkeypatch, tmp_path, caplog,
):
    app, _skill_dir = _configure_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api_app,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    with caplog.at_level(logging.WARNING, logger="xskill.server"):
        resp = TestClient(app).post(
            "/api/v1/skills/search", json={"query": "heartbeat", "top_k": 2},
        )

    assert resp.status_code == 200
    assert resp.json() == []
    assert any(
        "skill search skipped" in r.message and ".skill_index.pkl" in r.message
        for r in caplog.records
    )


def test_api_skill_search_unset_embedding_skips_client(
    monkeypatch, tmp_path, caplog,
):
    app, skill_dir = _configure_api(monkeypatch, tmp_path)
    (skill_dir / ".skill_index.pkl").write_bytes(b"placeholder")
    monkeypatch.setattr(
        api_app,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    with caplog.at_level(logging.WARNING, logger="xskill.server"):
        resp = TestClient(app).post(
            "/api/v1/skills/search", json={"query": "heartbeat", "top_k": 2},
        )

    assert resp.status_code == 200
    assert resp.json() == []
    assert any(
        "skill search skipped" in r.message
        and "embedding.base_url/model unset" in r.message
        for r in caplog.records
    )


def test_api_skill_resolve_missing_index_skips_embedding_client(
    monkeypatch, tmp_path, caplog,
):
    app, _skill_dir = _configure_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api_app,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    with caplog.at_level(logging.WARNING, logger="xskill.server"):
        resp = TestClient(app).post(
            "/api/v1/skills/resolve", json={"query": "heartbeat"},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "skill_name": None, "path": None, "side": "none", "sha": "",
    }
    assert any("skill resolve skipped" in r.message for r in caplog.records)


def test_sdk_skill_search_missing_index_skips_embedding_client(
    monkeypatch, tmp_path, caplog,
):
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

    with caplog.at_level(logging.WARNING, logger="xskill"):
        assert core.XSkill().search_skills("heartbeat", top_k=2) == []
    assert any(
        "skill search skipped" in r.message and ".skill_index.pkl" in r.message
        for r in caplog.records
    )


def test_sdk_skill_search_unset_embedding_skips_client(
    monkeypatch, tmp_path, caplog,
):
    from xskill import core
    from xskill.utils import llm

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / ".skill_index.pkl").write_bytes(b"placeholder")
    monkeypatch.setattr(core, "load_config", lambda config_path=None: {"embedding": {}})
    monkeypatch.setattr(core, "get_skill_dir", lambda: skill_dir)
    monkeypatch.setattr(
        llm,
        "create_embed_client",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("unexpected embed client"),
        ),
    )

    with caplog.at_level(logging.WARNING, logger="xskill"):
        assert core.XSkill().search_skills("heartbeat", top_k=2) == []
    assert any(
        "embedding.base_url/model unset" in r.message for r in caplog.records
    )
