"""块 6 调度器:命令/超时正确、失败落日志、串行不重叠、stop 及时。"""
from __future__ import annotations

import subprocess
import threading

import pytest

from xskill.pipeline.scheduler import IntervalSubprocessScheduler


def _sched(command=None, interval=0.01, timeout=5.0):
    return IntervalSubprocessScheduler(
        "test", command or ["true"], interval=interval, timeout=timeout,
    )


def test_rejects_nonpositive_interval_and_timeout():
    with pytest.raises(ValueError):
        _sched(interval=0)
    with pytest.raises(ValueError):
        _sched(timeout=-1)


def test_run_once_invokes_subprocess_with_command_and_timeout(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _sched(command=["xskill", "sweep", "--once"], timeout=42.0)._run_once()
    assert seen["command"] == ["xskill", "sweep", "--once"]
    assert seen["timeout"] == 42.0


def test_nonzero_returncode_logs_warning(monkeypatch, caplog):
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        _sched()._run_once()
    assert any("退出码" in record.getMessage() for record in caplog.records)


def test_timeout_logs_warning(monkeypatch, caplog):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        _sched()._run_once()
    assert any("上限被杀" in record.getMessage() for record in caplog.records)


def test_spawn_failure_logs_warning(monkeypatch, caplog):
    def fake_run(_command, **_kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        _sched()._run_once()
    assert any("启动子进程失败" in record.getMessage() for record in caplog.records)


def test_runs_are_serial_no_overlap(monkeypatch):
    """subprocess.run 阻塞 → 同一任务不可能自重叠:任一时刻并发数最多 1。"""
    concurrent = {"now": 0, "max": 0}
    lock = threading.Lock()
    gate = threading.Event()

    def fake_run(command, **_kwargs):
        with lock:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        gate.wait(0.05)
        with lock:
            concurrent["now"] -= 1
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    scheduler = _sched(interval=0.001)
    scheduler.start()
    gate.wait(0.2)
    scheduler.stop(timeout=2.0)
    assert concurrent["max"] == 1


def test_stop_joins_thread_promptly():
    scheduler = _sched(command=["true"], interval=100.0)  # 长周期,靠 stop 中断 wait
    scheduler.start()
    scheduler.stop(timeout=2.0)
    assert scheduler._thread is not None and not scheduler._thread.is_alive()
