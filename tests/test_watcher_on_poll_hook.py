"""Bug #5 regression: DirectoryWatcher 必须每轮 _loop 调一次 on_poll_hook，
让 server 端的"生态检测 + ingester 启动"能 daemon 运行中也跑（pick up 新装的 agent）。"""
from __future__ import annotations

import time

from xskill.watcher import DirectoryWatcher


def test_on_poll_hook_called_at_least_once(tmp_path):
    """hook 在 watcher 启动后被调到（验证 wiring；cycle 节奏交给 docker e2e）。"""
    calls: list[float] = []

    def hook():
        calls.append(time.time())

    db_path = tmp_path / "registry.db"
    watcher = DirectoryWatcher(
        llm=None, embed_client=None, config={},
        skill_dir=tmp_path, poll_interval=0.3, db_path=db_path,
        home_root=tmp_path,
        on_poll_hook=hook,
    )
    watcher.start()
    try:
        # 给一轮 poll + _scan_once 留出充足时间（_scan_once 含 DB 初始化 + 多步收割，
        # 首轮可能 1s+）
        time.sleep(3.0)
    finally:
        watcher.stop()

    assert len(calls) >= 1, f"hook never called in 3s (wiring broken)"
    assert watcher.on_poll_hook is hook, "hook reference not stored on watcher"


def test_on_poll_hook_exception_does_not_kill_watcher(tmp_path):
    """hook 抛异常 watcher 必须继续跑，不应整个 _loop 退出。"""
    counter = {"n": 0}

    def bad_hook():
        counter["n"] += 1
        raise ValueError("simulated hook failure")

    db_path = tmp_path / "registry.db"
    watcher = DirectoryWatcher(
        llm=None, embed_client=None, config={},
        skill_dir=tmp_path, poll_interval=0.3, db_path=db_path,
        home_root=tmp_path,
        on_poll_hook=bad_hook,
    )
    watcher.start()
    try:
        time.sleep(3.0)
        assert watcher.is_running, "watcher should still be alive despite hook throws"
        assert counter["n"] >= 1, f"bad hook should still have been called at least once"
    finally:
        watcher.stop()


def test_on_poll_hook_none_is_default(tmp_path):
    """没传 hook 时 watcher 仍能正常跑（向后兼容）。"""
    db_path = tmp_path / "registry.db"
    watcher = DirectoryWatcher(
        llm=None, embed_client=None, config={},
        skill_dir=tmp_path, poll_interval=0.3, db_path=db_path,
        home_root=tmp_path,
    )
    assert watcher.on_poll_hook is None
    watcher.start()
    try:
        time.sleep(0.5)
        assert watcher.is_running
    finally:
        watcher.stop()
