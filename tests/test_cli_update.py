from __future__ import annotations

import json

from xskill.cli import build_parser, cmd_update


def test_manual_update_respects_system_pip_index(monkeypatch, tmp_path):
    """
    手动 update 与自动 updater 一样，不得绕过企业 pip 配置。
    @category: integration
    @lane: integration
    @dependency: updater pip adapter
    @complexity: low
    ROI: 64
    """
    captured: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: tmp_path / "team_client.json",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._current_version", lambda package: "1.0.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._latest_pypi_version",
        lambda package: "1.1.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._restart", lambda: None,
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, **kwargs: captured.append(command) or _Result(),
    )

    args = build_parser().parse_args(["update"])
    assert cmd_update(args) == 0
    assert captured
    assert "-i" not in captured[0]


def test_manual_update_pip_failure_falls_back_to_server_wheel(monkeypatch, tmp_path):
    """
    手动 update 与后台 updater 对齐：pip 失败要走 team server wheel 回退，
    且 CLI 路径不重启（execv 重跑 update 会以报错收场）（#88）。
    @category: integration
    @lane: integration
    @dependency: updater server fallback
    @complexity: medium
    ROI: 70
    """
    state_file = tmp_path / "team_client.json"
    state_file.write_text(json.dumps({
        "server_url": "http://srv:8000",
        "client_id": "client-1",
        "join_token": "tok",
    }), encoding="utf-8")
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path", lambda: state_file,
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._current_version", lambda package: "1.0.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._latest_pypi_version", lambda package: "1.1.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater.AutoUpdater._install",
        lambda self, target_version: False,
    )
    fallback_calls: list[dict] = []

    def fake_fallback(self, current_str, current, *, reason, restart=True):
        fallback_calls.append({
            "server_url": self.server_url,
            "client_id": self.client_id,
            "reason": reason,
            "restart": restart,
        })
        return True

    monkeypatch.setattr(
        "xskill.team.client.updater.AutoUpdater._check_server_fallback",
        fake_fallback,
    )

    args = build_parser().parse_args(["update"])
    assert cmd_update(args) == 0
    assert fallback_calls == [{
        "server_url": "http://srv:8000",
        "client_id": "client-1",
        "reason": "pypi_install_failed",
        "restart": False,
    }]


def test_manual_update_pypi_query_failure_tries_server_then_errors(monkeypatch, tmp_path):
    """
    PyPI 查询失败时也应先试 server 通道，server 也不可用才报错退出（#88）。
    @category: integration
    @lane: integration
    @dependency: updater server fallback
    @complexity: low
    ROI: 66
    """
    state_file = tmp_path / "team_client.json"
    state_file.write_text(json.dumps({
        "server_url": "http://srv:8000",
        "client_id": "client-1",
        "join_token": "tok",
    }), encoding="utf-8")
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path", lambda: state_file,
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._current_version", lambda package: "1.0.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._latest_pypi_version", lambda package: None,
    )
    fallback_reasons: list[str] = []

    def fake_fallback(self, current_str, current, *, reason, restart=True):
        fallback_reasons.append(reason)
        return False

    monkeypatch.setattr(
        "xskill.team.client.updater.AutoUpdater._check_server_fallback",
        fake_fallback,
    )

    args = build_parser().parse_args(["update"])
    assert cmd_update(args) == 1
    assert fallback_reasons == ["pypi_query_failed"]


def test_manual_update_corrupt_state_file_keeps_pypi_path(monkeypatch, tmp_path):
    """
    team_client.json 损坏只应禁用 server 回退，不得挡住 PyPI 主路径。
    @category: integration
    @lane: integration
    @dependency: updater server fallback
    @complexity: low
    ROI: 60
    """
    state_file = tmp_path / "team_client.json"
    state_file.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path", lambda: state_file,
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._current_version", lambda package: "1.0.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater._latest_pypi_version", lambda package: "1.1.0",
    )
    monkeypatch.setattr(
        "xskill.team.client.updater.AutoUpdater._install",
        lambda self, target_version: True,
    )
    monkeypatch.setattr("xskill.team.client.updater._restart", lambda: None)

    args = build_parser().parse_args(["update"])
    assert cmd_update(args) == 0
