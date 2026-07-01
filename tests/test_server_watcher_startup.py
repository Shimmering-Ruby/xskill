"""server 启动必起 watcher —— 即便 registry 为空。

回归锚点:watcher 的 ``_loop`` 每轮跑 ``on_poll_hook`` 做生态再检测,daemon
空 home 启动后新装的 agent 全靠这个 poll 循环接管(Bug #5)。历史上 watcher
启动有个 ``if dirs`` 门,靠 startup 必注册 chat_archive 凑出 ≥1 个 watch dir
才不踩坑;chat_archive 随 web 面板移除后,空 home 下 watcher 不再启动 ——
本测试锁死"无条件起 watcher"。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_watcher_starts_even_with_empty_registry(tmp_path):
    from xskill.api import app as srv
    from starlette.testclient import TestClient

    srv._config = {
        "llm": {"base_url": "x", "model": "y", "api_key": "z"},
        "embedding": {},
        "watcher": {"poll_interval": 30},
    }
    srv._skill_dir = tmp_path / "skill"
    srv._skill_dir.mkdir()
    srv._watcher_ref.clear()
    try:
        with patch("xskill.api.app.create_llm_client", return_value=MagicMock()), \
             patch("xskill.api.app.create_embed_client", return_value=MagicMock()), \
             patch("xskill.api.app.init_skill_authoring_tool_context"), \
             patch("xskill.ecosystems.detect_known_ecosystems", return_value=[]):
            from xskill.api import create_app
            app = create_app()
            # 进入 context = startup 事件已跑完;退出 = shutdown 停掉 watcher
            with TestClient(app):
                watcher = srv._watcher_ref.get("instance")
                assert watcher is not None, (
                    "空 registry 下 watcher 未启动 —— poll-hook 生态再检测会失效,"
                    "daemon 启动后新装的 agent 永远接管不了"
                )
                assert watcher.is_running
    finally:
        w = srv._watcher_ref.get("instance")
        if w is not None:
            w.stop()
        srv._watcher_ref.clear()
        srv._config = None
        srv._skill_dir = None
