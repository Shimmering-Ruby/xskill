"""tests/test_cli_serve_guard.py — serve 单实例守卫（0.6.1a2）

防双 daemon 抢同一 registry（rebuild 后旧 daemon 可能用旧模型抢先处理）。
"""
from __future__ import annotations

import argparse

from xskill.cli import cmd_serve, build_parser


def _serve_args(**over):
    base = dict(host="127.0.0.1", port=8000, home=None, debug=False,
                server=False, force=False)
    base.update(over)
    return argparse.Namespace(**base)


class _FakeXskill:
    def __init__(self):
        self.served = False

    def serve(self, **kw):
        self.served = True


def test_serve_parser_has_force_flag():
    args = build_parser().parse_args(["serve", "--force"])
    assert args.force is True
    assert build_parser().parse_args(["serve"]).force is False


def test_serve_refuses_when_daemon_running(monkeypatch, capsys):
    monkeypatch.setattr("xskill.runtime.read_status",
                        lambda: {"running": True, "pid": 4242, "port": 8000})
    fake = _FakeXskill()

    rc = cmd_serve(_serve_args(), fake)

    assert rc == 2
    assert fake.served is False, "拒绝时不应真的启动 serve"
    assert "4242" in capsys.readouterr().err


def test_serve_force_bypasses_guard(monkeypatch):
    monkeypatch.setattr("xskill.runtime.read_status",
                        lambda: {"running": True, "pid": 4242, "port": 8000})
    monkeypatch.setattr("xskill.runtime.write_running", lambda **kw: None)
    fake = _FakeXskill()

    rc = cmd_serve(_serve_args(force=True), fake)

    assert rc == 0
    assert fake.served is True


def test_serve_starts_when_no_daemon_running(monkeypatch):
    monkeypatch.setattr("xskill.runtime.read_status", lambda: {"running": False})
    monkeypatch.setattr("xskill.runtime.write_running", lambda **kw: None)
    fake = _FakeXskill()

    rc = cmd_serve(_serve_args(), fake)

    assert rc == 0
    assert fake.served is True
