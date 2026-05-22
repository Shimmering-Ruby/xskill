"""test_server_chat_removed.py -- chat 端点已随 web 面板移除的回归锚点

web 面板删除后，server.py 里只为面板服务的 /api/v1/chat* 端点一并移除。
本测试锁定它们不再注册——任何重新引入都会让 404 断言失败。
其余编程 API（/skills、/trajectories）作为保留面不在此测试范围。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def client(tmp_path):
    """构造一个 deps 全 mock 的 TestClient（参照已删 test_chat_tools 的写法）。"""
    from xskill.api import app as srv
    srv._config = {"llm": {}, "embedding": {}, "watcher": {"poll_interval": 30}}
    srv._skill_dir = tmp_path / "skill"
    srv._skill_dir.mkdir(exist_ok=True)
    try:
        with patch("xskill.api.app.create_embed_client"), \
             patch("xskill.api.app.init_context"):
            from xskill.api import create_app
            from starlette.testclient import TestClient
            app = create_app()
            yield TestClient(app, raise_server_exceptions=False)
    finally:
        srv._config = None
        srv._skill_dir = None


@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/chat"),
    ("post", "/api/v1/chat/stream"),
    ("post", "/api/v1/chat/archive"),
    ("post", "/api/v1/chat/tool"),
    ("get", "/api/v1/chat/tools"),
])
def test_chat_endpoints_removed(client, method, path):
    """所有 /api/v1/chat* 端点都应已注销——返回 404。"""
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 404, (
        f"{method.upper()} {path} 仍然存在（status={resp.status_code}）；"
        "chat 端点应随 web 面板一并移除"
    )


def test_chat_symbols_not_exported():
    """server 模块不再暴露 chat 工具符号（_exec_tool / CHAT_TOOLS*）。"""
    from xskill.api import app as srv
    for sym in ("_exec_tool", "CHAT_TOOLS", "CHAT_TOOLS_INFO"):
        assert not hasattr(srv, sym), f"server 仍导出已删的 chat 符号 {sym}"
