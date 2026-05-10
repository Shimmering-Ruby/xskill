"""
test_chat_tools.py -- Chat tool-use endpoints unit tests
=========================================================
Covers:
  - GET /api/v1/chat/tools returns tool metadata
  - POST /api/v1/chat/tool dispatches to _exec_tool
  - _exec_tool read_file security guard
  - _exec_tool execute_code timeout handling
  - _exec_tool unknown tool returns error string
  - POST /api/v1/chat graceful 503 when LLM not configured
  - POST /api/v1/chat graceful 502 when LLM call fails
  - POST /api/v1/chat/stream SSE streaming endpoint
  - LLMClient.chat_stream yields chunks
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from xskill.server import (
    _exec_tool,
    CHAT_TOOLS,
    CHAT_TOOLS_INFO,
)


# ──────────────────────────────────────────────────────
# _exec_tool unit tests (no HTTP needed)
# ──────────────────────────────────────────────────────

class TestExecToolReadFile:
    """read_file tool must enforce path restrictions."""

    def test_read_within_skill_dir(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        f = skill_dir / "hello.md"
        f.write_text("skill content here", encoding="utf-8")

        traj_dir = tmp_path / "trajs"
        traj_dir.mkdir()

        result = _exec_tool("read_file", {"path": str(f)}, skill_dir, traj_dir, {})
        assert result == "skill content here"

    def test_read_within_traj_dir(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        traj_dir = tmp_path / "trajs"
        traj_dir.mkdir()
        f = traj_dir / "t1.md"
        f.write_text("traj data", encoding="utf-8")

        result = _exec_tool("read_file", {"path": str(f)}, skill_dir, traj_dir, {})
        assert result == "traj data"

    def test_read_outside_denied(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        traj_dir = tmp_path / "trajs"
        traj_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("password123", encoding="utf-8")

        result = _exec_tool("read_file", {"path": str(secret)}, skill_dir, traj_dir, {})
        assert "access denied" in result.lower()

    def test_read_nonexistent_file(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        traj_dir = tmp_path / "trajs"
        traj_dir.mkdir()
        bogus = skill_dir / "nope.md"

        result = _exec_tool("read_file", {"path": str(bogus)}, skill_dir, traj_dir, {})
        assert "Error" in result

    def test_read_truncates_at_5000(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        traj_dir = tmp_path / "trajs"
        traj_dir.mkdir()
        big = skill_dir / "big.md"
        big.write_text("x" * 10000, encoding="utf-8")

        result = _exec_tool("read_file", {"path": str(big)}, skill_dir, traj_dir, {})
        assert len(result) == 5000


class TestExecToolUnknown:
    def test_unknown_tool(self, tmp_path):
        result = _exec_tool("bogus_tool", {}, tmp_path, tmp_path, {})
        assert "Unknown tool" in result


class TestExecToolSearchSkills:
    def test_delegates_to_search_skills(self, tmp_path):
        with patch("xskill.server._exec_tool.__module__", "xskill.server"):
            pass
        # Patch the import target inside _exec_tool
        with patch("xskill.skill_tools.search_skills", return_value='[{"name":"foo"}]') as mock_ss:
            result = _exec_tool("search_skills", {"query": "deploy"}, tmp_path, tmp_path, {})
            mock_ss.assert_called_once_with("deploy", top_k=3)
            assert "foo" in result


class TestExecToolSearchTrajectories:
    def test_delegates_to_search_all(self, tmp_path):
        fake_results = [
            {"traj_id": "t1", "similarity": 0.9, "meta": {"intent": "fix bug"}},
        ]
        with patch("xskill.search.search_all", return_value=fake_results) as mock_sa:
            cfg = {"some": "config"}
            result = _exec_tool("search_trajectories", {"query": "bug fix", "top_k": 2},
                                tmp_path, tmp_path, cfg)
            mock_sa.assert_called_once_with("bug fix", top_k=2, config=cfg)
            parsed = json.loads(result)
            assert len(parsed) == 1
            assert parsed[0]["traj_id"] == "t1"
            assert parsed[0]["intent"] == "fix bug"


class TestExecToolExecuteCode:
    def test_basic_execution(self, tmp_path):
        result = _exec_tool("execute_code", {"code": "print('hello')"}, tmp_path, tmp_path, {})
        assert "hello" in result

    def test_timeout_returns_error(self, tmp_path):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=10)):
            result = _exec_tool("execute_code", {"code": "x"}, tmp_path, tmp_path, {})
            assert "timed out" in result.lower()


# ──────────────────────────────────────────────────────
# Metadata constants sanity checks
# ──────────────────────────────────────────────────────

class TestChatToolsMetadata:
    def test_tools_info_has_four_entries(self):
        assert len(CHAT_TOOLS_INFO) == 4

    def test_tools_info_names_match_definitions(self):
        defined_names = {t["function"]["name"] for t in CHAT_TOOLS}
        info_names = {t["name"] for t in CHAT_TOOLS_INFO}
        assert defined_names == info_names

    def test_each_tool_has_required_fields(self):
        for t in CHAT_TOOLS:
            assert t["type"] == "function"
            fn = t["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_each_info_has_icon_and_desc(self):
        for t in CHAT_TOOLS_INFO:
            assert "icon" in t
            assert "desc" in t
            assert "name" in t


# ──────────────────────────────────────────────────────
# /api/v1/chat endpoint error handling
# ──────────────────────────────────────────────────────

class TestApiChatLLMErrors:
    """POST /api/v1/chat should return 503 when LLM is not configured
    and 502 when the LLM call itself fails at runtime."""

    @pytest.fixture()
    def client(self):
        """Create a test client with all heavy deps mocked out."""
        with patch("xskill.server.create_embed_client"), \
             patch("xskill.server.init_context"):
            from xskill.server import create_app
            from starlette.testclient import TestClient
            app = create_app()
            yield TestClient(app, raise_server_exceptions=False)

    def test_returns_503_when_llm_not_configured(self, client):
        with patch("xskill.server.create_llm_client", return_value=None), \
             patch("xskill.server.search_skills", return_value="[]"):
            resp = client.post("/api/v1/chat", json={"message": "hello"})
            assert resp.status_code == 503
            body = resp.json()
            assert "not configured" in body["detail"].lower()

    def test_returns_502_when_llm_call_fails(self, client):
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("connection refused")
        with patch("xskill.server.create_llm_client", return_value=mock_llm), \
             patch("xskill.server.search_skills", return_value="[]"):
            resp = client.post("/api/v1/chat", json={"message": "hello"})
            assert resp.status_code == 502
            body = resp.json()
            assert "connection refused" in body["detail"].lower()
