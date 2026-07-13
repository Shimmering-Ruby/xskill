from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import xskill.api.app as api_app


def test_status_returns_null_branch_for_non_git_skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(api_app, "_skill_dir", tmp_path)
    app = FastAPI()
    app.include_router(api_app.router)

    response = TestClient(app).get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "skill_dir": str(tmp_path),
        "skill_count": 0,
        "git_branch": None,
    }


@pytest.mark.asyncio
async def test_status_propagates_unexpected_git_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(api_app, "_skill_dir", tmp_path)

    def fail_current_branch(_skill_dir):
        raise RuntimeError("unexpected git failure")

    monkeypatch.setattr(api_app, "current_branch", fail_current_branch)

    with pytest.raises(RuntimeError, match="unexpected git failure"):
        await api_app.api_status()
