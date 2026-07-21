"""server 启动必起常驻 watcher worker —— 即便 registry 为空。

回归锚点:web startup 无条件启动常驻 watcher 子进程，即便空 home /
空 registry。历史上 watcher 启动
有个 ``if dirs`` 门,空 home 下不起——本测试锁死"无条件起采集机制"。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_persistent_watcher_starts_even_with_empty_registry(tmp_path):
    from xskill.api import app as srv
    from starlette.testclient import TestClient

    srv._config = {
        "llm": {"base_url": "x", "model": "y", "api_key": "z"},
        "embedding": {},
        "watcher": {"poll_interval": 30},
    }
    srv._skill_dir = tmp_path / "skill"
    srv._skill_dir.mkdir()
    srv._schedulers.clear()
    try:
        with patch("xskill.api.app.create_llm_client", return_value=MagicMock()), \
             patch("xskill.api.app.create_embed_client", return_value=MagicMock()), \
             patch("xskill.api.app.init_skill_authoring_tool_context"), \
             patch("xskill.pipeline.scheduler.IntervalSubprocessScheduler.start"):
            from xskill.api import create_app
            app = create_app()
            # 进入 context = startup 事件已跑完;退出 = shutdown 停掉调度器
            with TestClient(app):
                names = [scheduler._name for scheduler in srv._schedulers]
                watcher = next(
                    scheduler for scheduler in srv._schedulers
                    if scheduler._name == "watcher"
                )
                assert watcher._persistent is True
    finally:
        srv._schedulers.clear()
        srv._config = None
        srv._skill_dir = None
