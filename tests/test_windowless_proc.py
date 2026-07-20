"""test_windowless_proc.py — 控制台子进程不弹 Windows 黑窗的统一 flag helper。

平台无关：monkeypatch sys.platform，在 Linux CI 上验证两个分支。
"""
from __future__ import annotations

import subprocess

import xskill.utils.proc as proc
import xskill.team.client.updater as updater


def test_windowless_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(proc.sys, "platform", "win32")
    kwargs = proc.windowless_subprocess_kwargs()
    assert "creationflags" in kwargs
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_windowless_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(proc.sys, "platform", "linux")
    assert proc.windowless_subprocess_kwargs() == {}


def test_windowless_kwargs_merge_extra_creationflags(monkeypatch):
    monkeypatch.setattr(proc.sys, "platform", "win32")
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    kwargs = proc.windowless_subprocess_kwargs(extra_creationflags=0x00000008)

    assert kwargs == {"creationflags": no_window | 0x00000008}


def test_updater_pip_install_passes_no_window_on_windows(monkeypatch):
    """updater 的 pip 升级子进程在 Windows 分支必须带 creationflags——否则每次
    自动更新都会闪黑窗（issue #125 同源）。"""
    monkeypatch.setattr(proc.sys, "platform", "win32")

    captured_kwargs = {}

    def fake_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    client_updater = updater.AutoUpdater(package="xskill")
    client_updater._install("9.9.9")

    assert "creationflags" in captured_kwargs
