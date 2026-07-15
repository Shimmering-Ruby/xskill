"""server 启动必起 sweep 调度器 —— 即便 registry 为空。

回归锚点:watcher 拆为短命 ``sweep --once`` 子进程后,web startup 无条件起 sweep
调度器(定时 spawn 采集/蒸馏子进程),即便空 home / 空 registry。历史上 watcher 启动
有个 ``if dirs`` 门,空 home 下不起——本测试锁死"无条件起采集机制"。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_sweep_scheduler_starts_even_with_empty_registry(tmp_path):
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
                assert "sweep" in names, (
                    "空 registry 下 sweep 调度器未启动 —— 采集/蒸馏子进程不会被定时 "
                    "spawn,daemon 启动后新装的 agent 永远接管不了"
                )
    finally:
        srv._schedulers.clear()
        srv._config = None
        srv._skill_dir = None
