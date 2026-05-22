"""Bug #2 regression: server.py 启动时 LLM/embed 客户端构造失败必须直接抛、
不能让 daemon 带 None client 继续跑（CLAUDE.md 第 1 条：不写 fallback）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _raise_bad_config(_, *a, **k):
    raise RuntimeError("invalid llm config (test)")


def _return_none(_, *a, **k):
    return None


@pytest.fixture
def _stub_loaded(monkeypatch, tmp_path):
    """预置 srv._config / _skill_dir，让 create_app 的 _ensure_loaded 短路。

    否则 _ensure_loaded → load_config() 会去读真实 ~/.xskill/config.yaml，
    测试就依赖机器本地状态——干净环境 / CI 上必挂（本测试此前正是如此）。
    """
    from xskill.api import app as srv

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(srv, "_config", {
        "llm": {"base_url": "x", "model": "y", "api_key": "z"},
        "embedding": {},
        "watcher": {"poll_interval": 30},
    })
    monkeypatch.setattr(srv, "_skill_dir", skill_dir)


def test_startup_raises_when_create_llm_client_raises(monkeypatch, tmp_path, _stub_loaded):
    """create_llm_client 抛 → daemon startup 也应直接抛，不静默降级 llm=None。"""
    from xskill.api import app as srv

    monkeypatch.setattr(srv, "create_llm_client", _raise_bad_config)
    app = srv.create_app(home_root=tmp_path)

    with pytest.raises(RuntimeError, match="invalid llm config"):
        with TestClient(app):
            pass  # 触发 startup event


def test_startup_raises_when_create_llm_client_returns_none(monkeypatch, tmp_path, _stub_loaded):
    """create_llm_client 返回 None（内部 except 吞错的路径） → daemon 启动应显式断言失败。"""
    from xskill.api import app as srv

    monkeypatch.setattr(srv, "create_llm_client", _return_none)
    app = srv.create_app(home_root=tmp_path)

    with pytest.raises(RuntimeError, match="LLM client could not be created"):
        with TestClient(app):
            pass


def test_startup_raises_when_create_embed_client_fails(monkeypatch, tmp_path, _stub_loaded):
    """create_embed_client 抛 → daemon startup 也应直接抛。"""
    from xskill.api import app as srv

    # LLM 不抛、只让 embed 抛
    monkeypatch.setattr(srv, "create_embed_client", _raise_bad_config)
    app = srv.create_app(home_root=tmp_path)

    with pytest.raises(RuntimeError, match="invalid llm config"):
        with TestClient(app):
            pass
