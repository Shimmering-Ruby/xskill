from __future__ import annotations

from pathlib import Path

from xskill.team.client import updater as updater_mod
from xskill.team.client.updater import AutoUpdater


def test_pypi_newer_version_uses_pypi_and_skips_server(monkeypatch):
    installed: list[str] = []
    restarted: list[bool] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: "1.1.0")
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("server fallback used")),
    )
    monkeypatch.setattr(updater_mod, "_restart", lambda: restarted.append(True))

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    monkeypatch.setattr(updater, "_install", lambda version: installed.append(version) or True)

    updater._check_and_update()

    assert installed == ["1.1.0"]
    assert restarted == [True]


def test_current_pypi_version_still_checks_server(monkeypatch):
    """回归(2026-07-10):PyPI 无新版时曾直接 return,不查 server。

    内网场景 server 预置 wheel 常领先公网 PyPI(先内部分发、后补发)。
    旧行为下只要 pypi.org JSON API 可达且无新版,server 渠道永远不会被
    查询,内网更新静默失效。现在 PyPI 不领先时必须继续问 server。"""
    fallback_reasons: list[str] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: "1.0.0")

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    monkeypatch.setattr(
        updater,
        "_install",
        lambda version: (_ for _ in ()).throw(AssertionError("pypi install used")),
    )
    monkeypatch.setattr(
        updater,
        "_check_server_fallback",
        lambda current_str, current, *, reason: fallback_reasons.append(reason),
    )

    updater._check_and_update()

    assert fallback_reasons == ["pypi_not_ahead"], (
        "PyPI 无新版时必须仍查询 server——server wheel 领先 PyPI 是内网常态")


def test_pip_hang_returns_false_instead_of_blocking(monkeypatch):
    """回归(2026-07-10):subprocess.run 没有 timeout,pip 挂死(代理黑洞
    涓涓细流)会把单线程 updater 永久卡死,之后每小时的检查全部消失。"""
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        assert kwargs.get("timeout"), "pip 调用必须带 subprocess timeout"
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")

    assert updater._install("9.9.9") is False
    assert updater._install_wheel(Path("/tmp/fake.whl")) is False


def test_pypi_query_failure_falls_back_to_server_wheel(monkeypatch):
    installed_wheels: list[str] = []
    restarted: list[bool] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: None)
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id: {
            "version": "1.2.0",
            "wheel_available": True,
            "wheel_filename": "xskill-1.2.0-py3-none-any.whl",
        },
    )

    def fake_download(
        server_url: str,
        join_token: str,
        client_id: str,
        dest_dir: Path,
        filename: str | None,
    ) -> Path:
        wheel = dest_dir / (filename or "xskill-1.2.0-py3-none-any.whl")
        wheel.write_bytes(b"wheel")
        return wheel

    monkeypatch.setattr(updater_mod, "_download_server_wheel", fake_download)
    monkeypatch.setattr(updater_mod, "_restart", lambda: restarted.append(True))

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    monkeypatch.setattr(
        updater,
        "_install_wheel",
        lambda wheel: installed_wheels.append(wheel.name) or True,
    )

    updater._check_and_update()

    assert installed_wheels == ["xskill-1.2.0-py3-none-any.whl"]
    assert restarted == [True]


def test_pypi_install_failure_can_fallback_to_server_wheel(monkeypatch):
    installed_wheels: list[str] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: "1.2.0")
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id: {
            "version": "1.2.0",
            "wheel_available": True,
            "wheel_filename": "xskill-1.2.0-py3-none-any.whl",
        },
    )

    def fake_download(
        server_url: str,
        join_token: str,
        client_id: str,
        dest_dir: Path,
        filename: str | None,
    ) -> Path:
        wheel = dest_dir / (filename or "xskill-1.2.0-py3-none-any.whl")
        wheel.write_bytes(b"wheel")
        return wheel

    monkeypatch.setattr(updater_mod, "_download_server_wheel", fake_download)
    monkeypatch.setattr(updater_mod, "_restart", lambda: None)

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    monkeypatch.setattr(updater, "_install", lambda version: False)
    monkeypatch.setattr(
        updater,
        "_install_wheel",
        lambda wheel: installed_wheels.append(wheel.name) or True,
    )

    updater._check_and_update()

    assert installed_wheels == ["xskill-1.2.0-py3-none-any.whl"]


def test_server_fallback_skips_non_newer_server_version(monkeypatch):
    downloaded: list[bool] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.2.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: None)
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id: {
            "version": "1.2.0",
            "wheel_available": True,
            "wheel_filename": "xskill-1.2.0-py3-none-any.whl",
        },
    )
    monkeypatch.setattr(
        updater_mod,
        "_download_server_wheel",
        lambda *args, **kwargs: downloaded.append(True),
    )

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    updater._check_and_update()

    assert downloaded == []


def test_pip_respects_system_index_by_default(monkeypatch):
    """回归(0.6.8):曾写死 -i pypi.org/simple/,强行绕过用户 pip.ini 里配的
    企业镜像,代理环境下必超时。缺省必须不传 -i(尊重 pip 配置),显式传入
    pypi_url 时才覆盖。"""
    captured: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _R()

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)

    AutoUpdater()._install("1.2.3")
    assert "-i" not in captured[-1], "缺省不许传 -i,必须尊重系统 pip 配置"

    AutoUpdater(pypi_url="https://mirror.example/simple/")._install("1.2.3")
    idx = captured[-1].index("-i")
    assert captured[-1][idx + 1] == "https://mirror.example/simple/"
