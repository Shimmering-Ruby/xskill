from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    assert updater._install_wheel(Path("/tmp/fake.whl"), "9.9.9") is False


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_server_wheel_not_reaching_target_does_not_restart(monkeypatch):
    """回归:server wheel 装完 pip rc=0 但版本没推进到目标 → 判失败、不重启。
    否则装了个没升级的 wheel 仍触发 _restart,客户端回来还是老版本、每轮又升
    又重启,把用户反复打掉线(线上 0.6.15 掉线现象)。"""
    def fake_run(cmd, **kwargs):
        if any("importlib.metadata" in str(c) for c in cmd):
            return _FakeResult(stdout="1.0.0")   # 核验:仍是旧版本,未达 target
        return _FakeResult()                     # pip install wheel: rc=0

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    assert updater._install_wheel(Path("/tmp/x.whl"), "1.2.0") is False


def test_server_wheel_reaching_target_succeeds(monkeypatch):
    """server wheel 装完版本确达目标 → 成功(可安全重启)。"""
    def fake_run(cmd, **kwargs):
        if any("importlib.metadata" in str(c) for c in cmd):
            return _FakeResult(stdout="1.2.0")
        return _FakeResult()

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    assert updater._install_wheel(Path("/tmp/x.whl"), "1.2.0") is True


def test_pypi_query_failure_falls_back_to_server_wheel(monkeypatch):
    installed_wheels: list[str] = []
    restarted: list[bool] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: None)
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id, use_proxy=False: {
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
        use_proxy: bool = False,
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
        lambda wheel, target: installed_wheels.append(wheel.name) or True,
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
        lambda server_url, join_token, client_id, use_proxy=False: {
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
        use_proxy: bool = False,
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
        lambda wheel, target: installed_wheels.append(wheel.name) or True,
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
        lambda server_url, join_token, client_id, use_proxy=False: {
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

    def pip_install_cmd(commands):
        return next(c for c in commands
                    if "install" in c and any("1.2.3" in part for part in c))

    captured.clear()
    AutoUpdater()._install("1.2.3")
    assert "-i" not in pip_install_cmd(captured), "缺省不许传 -i,必须尊重系统 pip 配置"

    captured.clear()
    AutoUpdater(pypi_url="https://mirror.example/simple/")._install("1.2.3")
    install_cmd = pip_install_cmd(captured)
    idx = install_cmd.index("-i")
    assert install_cmd[idx + 1] == "https://mirror.example/simple/"


def test_server_requests_default_to_no_proxy_opener(monkeypatch):
    """回归(2026-07-14):server 方向 urllib 走默认 opener 会吃系统代理,内网 IP
    被代理黑洞超时,PyPI 失败后的 server 回退也一起死。缺省必须用
    ProxyHandler({}) 直连;use_proxy=True 才恢复走代理。"""
    import json
    import urllib.request

    recorded_handlers: list[tuple] = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"version": "1.0.0"}).encode("utf-8")

    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResponse()

    def fake_build_opener(*handlers):
        recorded_handlers.append(handlers)
        return _FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    updater_mod._server_version("http://server", "tok", "cid")
    assert len(recorded_handlers[-1]) == 1
    assert isinstance(recorded_handlers[-1][0], urllib.request.ProxyHandler)
    assert recorded_handlers[-1][0].proxies == {}, "缺省必须注入空代理映射直连"

    updater_mod._server_version("http://server", "tok", "cid", use_proxy=True)
    assert recorded_handlers[-1] == (), "use_proxy=True 时不得注入 ProxyHandler,走系统代理"


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_pip_success_but_wrong_version_falls_back_to_server(monkeypatch):
    """回归(2026-07-14):镜像只同步到旧版时 pip 可能返回 0 却没把版本推到目标,
    装后必须另起解释器核验;核验不符一律视为安装失败并走 server 回退。"""
    installed_wheels: list[str] = []

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: "1.2.0")
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id, use_proxy=False: {
            "version": "1.2.0",
            "wheel_available": True,
            "wheel_filename": "xskill-1.2.0-py3-none-any.whl",
        },
    )

    def fake_download(
        server_url, join_token, client_id, dest_dir, filename, use_proxy=False,
    ):
        wheel = dest_dir / (filename or "xskill-1.2.0-py3-none-any.whl")
        wheel.write_bytes(b"wheel")
        return wheel

    monkeypatch.setattr(updater_mod, "_download_server_wheel", fake_download)
    monkeypatch.setattr(updater_mod, "_restart", lambda: None)

    def fake_run(cmd, **kwargs):
        if "install" in cmd:
            return _FakeProc(returncode=0)
        return _FakeProc(returncode=0, stdout="1.0.0\n")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    monkeypatch.setattr(
        updater,
        "_install_wheel",
        lambda wheel, target: installed_wheels.append(wheel.name) or True,
    )

    updater._check_and_update()

    assert installed_wheels == ["xskill-1.2.0-py3-none-any.whl"]


def test_pip_and_server_fallback_both_fail_logs_warning(monkeypatch, caplog):
    """回归(2026-07-14):pip 与 server 回退双双失败时,过去只在 DEBUG 记录,全链
    静默。现在必须留一条 WARNING,一行含目标版本 + pip 失败摘要 + server 回退失败摘要。"""
    import logging

    monkeypatch.setattr(updater_mod, "_current_version", lambda package: "1.0.0")
    monkeypatch.setattr(updater_mod, "_latest_pypi_version", lambda package: "1.2.0")
    monkeypatch.setattr(
        updater_mod,
        "_server_version",
        lambda server_url, join_token, client_id, use_proxy=False: None,
    )

    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stderr="ERROR: 镜像无 1.2.0")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)

    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")
    with caplog.at_level(logging.WARNING, logger="xskill.team.client.updater"):
        updater._check_and_update()

    combined = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "1.2.0" in record.getMessage()
        and "镜像无 1.2.0" in record.getMessage()
        and "查询 server 版本失败" in record.getMessage()
    ]
    assert combined, [r.getMessage() for r in caplog.records]


# ═══════════════════════════════════════════════════════════════
# 自动更新失效审计（docs/auto-update-audit.md）的回归护栏
# ═══════════════════════════════════════════════════════════════


class _ExitCalled(Exception):
    """替身 os._exit：真退出会打死测试进程，这里改成抛异常并记录退出码。"""

    def __init__(self, code: int):
        super().__init__(f"os._exit({code})")
        self.code = code


class _FakeSpawnedProc:
    """替身子进程。``alive=True`` → wait() 超时(还在跑)；否则立即返回退出码。"""

    def __init__(self, pid: int = 4242, alive: bool = True, returncode: int = 0):
        self.pid = pid
        self._alive = alive
        self.returncode = None if alive else returncode

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="spawned", timeout=timeout)
        return self.returncode


def _windows_restart_env(monkeypatch, state: dict, proc: _FakeSpawnedProc,
                         logs_dir: Path | None = None):
    """把 _restart 摆到 Windows 上：伪造 daemon state / Popen / os._exit。

    返回 (spawned_argv_list, exit_codes)——两个 list 由调用方断言。
    """
    import xskill.config
    from xskill.team.client import service as service_mod

    spawned: list[list[str]] = []
    exit_codes: list[int] = []

    monkeypatch.setattr(updater_mod.sys, "platform", "win32")
    monkeypatch.setattr(service_mod, "read_daemon_state", lambda: state)
    if logs_dir is not None:
        monkeypatch.setattr(xskill.config, "get_logs_dir", lambda: logs_dir)

    def fake_popen(argv, **kwargs):
        spawned.append(list(argv))
        return proc

    def fake_exit(code):
        exit_codes.append(code)
        raise _ExitCalled(code)

    monkeypatch.setattr(updater_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(updater_mod.os, "_exit", fake_exit)
    return spawned, exit_codes


def test_windows_schtasks_restart_actively_relaunches_task(monkeypatch, tmp_path):
    """D2 回归:schtasks 路径曾 os._exit(1) 赌任务计划程序 RestartOnFailure 把
    进程拉回来。RestartOnFailure 只管"任务*启动失败*"，对"动作进程跑完再非零
    退出"多半不触发 → 老进程没了、新进程不来，用户一直掉线到下次登录(线上
    0.6.15→0.6.17"升级完再没回来")。现在必须**主动**拉起:退出前 spawn 一个
    relauncher，等老进程真退出后 schtasks /Run 起受管新实例。"""
    proc = _FakeSpawnedProc(alive=True)
    spawned, exit_codes = _windows_restart_env(
        monkeypatch, {"method": "schtasks", "task_name": "Xskill_Connect"}, proc,
        logs_dir=tmp_path)

    with pytest.raises(_ExitCalled):
        updater_mod._restart()

    assert len(spawned) == 1, "schtasks 路径必须 spawn relauncher，不许干等 RestartOnFailure"
    relauncher = spawned[0]
    assert relauncher[0] == updater_mod.sys.executable
    assert relauncher[1] == "-c"
    assert "schtasks" in relauncher[2] and "/Run" in relauncher[2], (
        "relauncher 必须自己 schtasks /Run 起新实例")
    assert "WaitForSingleObject" in relauncher[2], (
        "必须等老进程真的退出再 /Run —— MultipleInstancesPolicy=IgnoreNew "
        "会把老实例还在时的 /Run 直接忽略掉")
    assert relauncher[3] == str(updater_mod.os.getpid()), "relauncher 要等的是本进程"
    assert relauncher[4] == "Xskill_Connect"
    assert relauncher[5] == str(tmp_path / "connect-relauncher.log"), (
        "日志路径必须由还健康的父进程解析好传进去——relauncher 自己 import xskill "
        "的话，新版本装坏了它连失败都报不出来")
    assert exit_codes == [0]


def test_windows_schtasks_restart_aborts_when_relauncher_dies(monkeypatch, tmp_path):
    """D2:relauncher 没起来就退出 = 没人接班 = 永久掉线。宁可继续跑老版本。"""
    proc = _FakeSpawnedProc(alive=False, returncode=1)
    _, exit_codes = _windows_restart_env(
        monkeypatch, {"method": "schtasks", "task_name": "Xskill_Connect"}, proc,
        logs_dir=tmp_path)

    with pytest.raises(RuntimeError):
        updater_mod._restart()
    assert exit_codes == [], "拉不起接班进程时绝不能退出老进程"


def test_windows_startup_folder_restart_spawns_full_argv_with_interpreter(monkeypatch):
    """D3 回归:startup_folder 路径曾 Popen(sys.argv) —— 缺解释器。``-m xskill``
    下 sys.argv[0] 是 __main__.py 路径，CreateProcess 它 → WinError 193，新进程
    根本起不来，而老进程已经自杀 = 永久掉线。必须用 daemon state 里登记的完整
    argv（service._foreground_argv 生成，含 pythonw.exe）。"""
    registered_argv = ["C:\\Py\\pythonw.exe", "-m", "xskill", "connect", "--foreground"]
    proc = _FakeSpawnedProc(alive=True)
    spawned, exit_codes = _windows_restart_env(
        monkeypatch,
        {"method": "startup_folder", "argv": registered_argv},
        proc,
    )
    monkeypatch.setattr(updater_mod.sys, "argv",
                        ["C:\\Py\\Lib\\site-packages\\xskill\\__main__.py",
                         "connect", "--foreground"])

    with pytest.raises(_ExitCalled):
        updater_mod._restart()

    assert spawned == [registered_argv], "重启命令必须带解释器，不能是裸 sys.argv"
    assert exit_codes == [0]


def test_windows_startup_folder_restart_aborts_when_child_dies(monkeypatch):
    """D3:新进程起来就退（WinError 193 的另一种形态）时不许退老进程。"""
    proc = _FakeSpawnedProc(alive=False, returncode=193)
    _, exit_codes = _windows_restart_env(
        monkeypatch,
        {"method": "startup_folder",
         "argv": ["C:\\Py\\pythonw.exe", "-m", "xskill", "connect", "--foreground"]},
        proc,
    )

    with pytest.raises(RuntimeError):
        updater_mod._restart()
    assert exit_codes == [], "没校验到新进程活着就退出老进程 = 永久掉线"


def test_windows_restart_without_daemon_state_does_not_exit(monkeypatch):
    """D2/D3:state 读不到时曾默认走 startup_folder 分支 spawn 一个必然失败的
    命令再自杀。不知道怎么拉起接班进程，就老老实实报错、继续在线。"""
    proc = _FakeSpawnedProc(alive=True)
    spawned, exit_codes = _windows_restart_env(monkeypatch, {"running": False}, proc)

    with pytest.raises(RuntimeError):
        updater_mod._restart()
    assert spawned == [] and exit_codes == []


def test_loop_survives_check_exception(monkeypatch):
    """D4 回归:_loop 无 try/except，而线程 daemon=True —— _check_and_update 抛
    一次异常就把 updater 线程静默打死，整个进程生命周期内再也不会检查更新
    （对比 daemon._tick 有兜底）。"""
    import logging

    calls: list[int] = []
    updater = AutoUpdater(interval=0.01)

    def boom() -> None:
        calls.append(1)
        if len(calls) >= 2:
            updater.stop()          # 第二轮后收工，证明第一轮的异常没打死循环
        raise RuntimeError("PyPI 抽风")

    monkeypatch.setattr(updater, "_check_and_update", boom)

    caplog_logger = logging.getLogger("xskill.team.client.updater")
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector()
    caplog_logger.addHandler(handler)
    try:
        updater._loop()             # 不许抛出来
    finally:
        caplog_logger.removeHandler(handler)

    assert len(calls) >= 2, "一次异常不许终结 updater 线程"
    assert any(rec.levelno >= logging.WARNING for rec in records), "异常必须落日志"


def test_first_check_is_prompt_not_a_full_interval(monkeypatch):
    """D8 回归:首检曾等满一个 interval（默认 3600s），且时钟每次进程重启都从零
    开始 —— 频繁重启/每天登录的机器可能长期甚至永远到不了第一次检查。"""
    waits: list[float] = []
    updater = AutoUpdater(interval=3600)

    def fake_wait(timeout=None):
        waits.append(timeout)
        return True                 # 立刻当作已 stop，跑完首等就收工

    monkeypatch.setattr(updater._stop, "wait", fake_wait)
    monkeypatch.setattr(updater._stop, "is_set", lambda: True)
    updater._loop()

    assert waits, "首检前必须有一次等待"
    assert waits[0] <= updater._FIRST_CHECK_MAX_DELAY, (
        f"首检不许等满 interval（等了 {waits[0]}s）")
    assert waits[0] >= updater._FIRST_CHECK_MIN_DELAY, "要抖动，别全公司同一秒打 PyPI"

    # 抖动生效：多个实例的首检时刻不能全撞在一起。
    delays = set()
    for _ in range(20):
        probe = AutoUpdater(interval=3600)
        seen: list[float] = []

        def probe_wait(timeout=None, sink=seen):
            sink.append(timeout)
            return True

        monkeypatch.setattr(probe._stop, "wait", probe_wait)
        monkeypatch.setattr(probe._stop, "is_set", lambda: True)
        probe._loop()
        delays.add(seen[0])
    assert len(delays) > 1, "首检延迟必须带抖动"


def test_small_interval_is_not_stretched_by_first_check_delay(monkeypatch):
    """D8:interval 比抖动窗口还小时（测试/短周期配置），首等不得反而变长。"""
    waits: list[float] = []
    updater = AutoUpdater(interval=0.5)
    monkeypatch.setattr(updater._stop, "wait", lambda timeout=None: waits.append(timeout) or True)
    monkeypatch.setattr(updater._stop, "is_set", lambda: True)
    updater._loop()
    assert waits[0] == 0.5


def test_pip_uses_same_proxy_as_pypi_detection(monkeypatch):
    """D1 回归:检测走 urllib（默认 opener = ProxyHandler(getproxies())，Windows
    上还会读注册表里的系统/IE 代理），安装走 pip（只认环境变量，读不到系统代理）
    —— 两条通道不对称。线上就是这个形态:urllib 查到 0.6.17，pip 却对**每个**
    版本报 "No matching distribution ... (ssl)"。pip 必须拿到检测同款代理。"""
    import urllib.request

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc(returncode=0, stdout="1.2.3\n")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: {"https": "http://corp-proxy:8080"})

    AutoUpdater()._install("1.2.3")

    install_cmd = captured[0]
    assert "--proxy" in install_cmd, "pip 必须走检测通道同款代理"
    assert install_cmd[install_cmd.index("--proxy") + 1] == "http://corp-proxy:8080"


def test_pip_gets_no_proxy_flag_when_detection_has_none(monkeypatch):
    """D1:检测通道没走代理时不许凭空给 pip 塞 --proxy（直连机器会被打挂）。"""
    import urllib.request

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc(returncode=0, stdout="1.2.3\n")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

    AutoUpdater()._install("1.2.3")
    assert "--proxy" not in captured[0]


def test_pip_output_is_decoded_gbk_safely(monkeypatch):
    """D6 回归:subprocess.run(text=True) 按 cp936-strict 解 pip 输出——UTF-8
    输出变乱码（用户所见），非法字节直接 UnicodeDecodeError，被宽 except 吞成
    "pip 失败"，把一个可能已经装成功的版本丢掉。解码必须确定且不抛。"""
    seen_kwargs: list[dict] = []

    def fake_run(cmd, **kwargs):
        seen_kwargs.append(kwargs)
        return _FakeProc(returncode=0, stdout="1.2.3\n")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    AutoUpdater()._install("1.2.3")
    AutoUpdater()._install_wheel(Path("/tmp/x.whl"), "1.2.3")

    assert seen_kwargs, "没跑到 pip"
    for kwargs in seen_kwargs:
        assert kwargs.get("errors") == "replace", "解码非法字节不许抛异常"
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8", (
            "pip 是 Python 程序，钉死它 stdout 编码才能确定性地解码")


def test_install_fails_when_installed_package_cannot_be_imported(monkeypatch):
    """D7 回归:装后只读 importlib.metadata.version，不校验能否 import。同一张
    坏网络下拉不到传递依赖的版本会"核验通过"→ 重启 → 崩溃循环。"""
    def fake_run(cmd, **kwargs):
        if "-c" in cmd:      # 核验解释器:import 炸了
            return _FakeProc(returncode=1,
                             stderr="ModuleNotFoundError: No module named 'httpx'")
        return _FakeProc(returncode=0)     # pip install 自称成功

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    updater = AutoUpdater()

    assert updater._install("1.2.3") is False, "import 不起来的版本不许判成功、不许重启"
    assert "import" in updater._last_pip_failure_summary
    assert updater._install_wheel(Path("/tmp/x.whl"), "1.2.3") is False


def test_post_install_verify_actually_imports_the_package(monkeypatch):
    """D7:核验子进程必须真的 import 包本体，不能只查 metadata。"""
    verify_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if "-c" in cmd:
            verify_cmds.append(cmd)
            return _FakeProc(returncode=0, stdout="1.2.3\n")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    assert AutoUpdater()._install("1.2.3") is True

    assert verify_cmds, "装完必须另起解释器核验"
    code = verify_cmds[0][verify_cmds[0].index("-c") + 1]
    assert "import_module('xskill')" in code.replace('"', "'")
    assert "metadata.version" in code


def test_unparseable_release_does_not_poison_pypi_channel(monkeypatch):
    """D9 回归:``Version(v) for v in releases`` 一把梭，PyPI 上任何一个不可解析
    的坏 release 串都会抛异常 → 被吞成"查 PyPI 失败" → PyPI 通道对所有人失效，
    直到那个 release 被删。坏串必须逐个跳过。"""
    import json
    import urllib.request

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({
                "releases": {"1.0.0": [], "not-a-version": [], "1.2.0": [],
                             "0.6.17.dev1": []},
            }).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse())

    assert updater_mod._latest_pypi_version("xskill") == "1.2.0"


# ─────────────────────────────────────────────────────────────
# relauncher 脚本本体（_WINDOWS_RELAUNCHER_SOURCE）
#
# 它是老进程退出前的最后一棒:跑在一个 detach 的裸解释器里、stdout/stderr 全
# DEVNULL。这里把 ctypes/subprocess/threading 换成替身后真的 exec 它，逐条验证
# 重试与日志——它静默失败一次，用户就掉线到下次登录。
# ─────────────────────────────────────────────────────────────


class _FakeRunResult:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _exec_relauncher(monkeypatch, tmp_path, run_results, handle=1,
                     console_encoding="utf-8"):
    """用替身模块 exec relauncher 脚本，返回 (log_text, run_calls, waits)。"""
    import sys as real_sys
    import types

    log_path = tmp_path / "connect-relauncher.log"
    run_calls: list[list[str]] = []
    waits: list[float] = []
    pending = list(run_results)

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return pending.pop(0)

    class _FakeEvent:
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

    class _FakeKernel32:
        def OpenProcess(self, access, inherit, pid):
            return handle

        def WaitForSingleObject(self, target_handle, timeout_ms):
            return 0

        def CloseHandle(self, target_handle):
            return 1

    monkeypatch.setitem(real_sys.modules, "ctypes", types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=_FakeKernel32())))
    monkeypatch.setitem(real_sys.modules, "subprocess",
                        types.SimpleNamespace(run=fake_run))
    monkeypatch.setitem(real_sys.modules, "threading",
                        types.SimpleNamespace(Event=_FakeEvent))
    monkeypatch.setitem(real_sys.modules, "locale", types.SimpleNamespace(
        getpreferredencoding=lambda do_setlocale=True: console_encoding))
    monkeypatch.setattr(real_sys, "argv",
                        ["-c", "4242", "Xskill_Connect", str(log_path)])

    exec(compile(updater_mod._WINDOWS_RELAUNCHER_SOURCE, "<relauncher>", "exec"), {})

    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    return log_text, run_calls, waits


def test_relauncher_retries_when_schtasks_run_fails_transiently(monkeypatch, tmp_path):
    """老进程刚倒下时 /Run 可能瞬时失败（实例还没清干净、调度器抽风）。只试一次
    就放弃 = 没人接班 = 永久掉线，必须重试。"""
    log_text, run_calls, waits = _exec_relauncher(
        monkeypatch, tmp_path,
        [_FakeRunResult(1, stderr=b"ERROR: transient"), _FakeRunResult(0, b"SUCCESS")],
    )

    assert len(run_calls) == 2, "第一次 /Run 失败后必须重试"
    assert run_calls[0] == ["schtasks", "/Run", "/TN", "Xskill_Connect"]
    assert waits == [5.0], "重试之间要真的等一下（Event.wait，不是 time.sleep）"
    assert "rc=1" in log_text and "transient" in log_text
    assert "成功（第 2 次尝试）" in log_text


def test_relauncher_logs_loudly_when_schtasks_run_never_succeeds(monkeypatch, tmp_path):
    """核心回归:曾经 check=False 丢弃 /Run 结果，且 stdout/stderr 全 DEVNULL ——
    /Run 挂了就是**零诊断的永久掉线**（退出码 0，RestartOnFailure 也不再是后手）。
    失败必须留下时间戳 + 任务名 + rc + schtasks 原文。"""
    failures = [_FakeRunResult(1, stderr=b"ERROR: The system cannot find the task")] * 3
    log_text, run_calls, waits = _exec_relauncher(monkeypatch, tmp_path, failures)

    assert len(run_calls) == 3, "放弃前必须把重试次数用满"
    assert waits == [5.0, 5.0]
    assert "连续 3 次失败" in log_text, "彻底失败必须有一条结论性日志"
    assert "Xskill_Connect" in log_text and "rc=1" in log_text
    assert "cannot find the task" in log_text, "要留下 schtasks 的原文供排查"
    assert "LogonTrigger" in log_text, "要写清后果（掉线到下次登录），别让人猜"
    for line in log_text.strip().splitlines():
        assert line[:4].isdigit(), f"每行都要带时间戳: {line!r}"


def test_relauncher_does_not_wait_after_first_try_succeeds(monkeypatch, tmp_path):
    log_text, run_calls, waits = _exec_relauncher(
        monkeypatch, tmp_path, [_FakeRunResult(0, b"SUCCESS: Attempted to run")])

    assert len(run_calls) == 1 and waits == []
    assert "成功（第 1 次尝试）" in log_text


def test_relauncher_decodes_gbk_schtasks_output(monkeypatch, tmp_path):
    """schtasks 是 Windows 原生工具，输出是 ANSI(中文机器 cp936)。硬按 UTF-8 解
    会 UnicodeDecodeError 打死 relauncher —— 最后一棒当场没了，用户永久掉线。"""
    native_error = "错误: 系统找不到指定的任务。".encode("gbk")
    log_text, run_calls, _ = _exec_relauncher(
        monkeypatch, tmp_path, [_FakeRunResult(1, stderr=native_error)] * 3,
        console_encoding="gbk",
    )

    assert len(run_calls) == 3, "解码不许打断重试"
    assert "错误: 系统找不到指定的任务。" in log_text


def test_relauncher_still_runs_task_when_old_process_handle_is_gone(monkeypatch, tmp_path):
    """OpenProcess 拿不到句柄 = 老进程已经退出（正是我们等的结果），此时更要
    /Run，不能就此收工。"""
    log_text, run_calls, _ = _exec_relauncher(
        monkeypatch, tmp_path, [_FakeRunResult(0, b"SUCCESS")], handle=0)

    assert len(run_calls) == 1
    assert "OpenProcess" in log_text and "直接尝试 /Run" in log_text


def test_relauncher_never_falls_back_to_spawning_the_daemon_itself():
    """relauncher 只准通过任务计划程序拉起（受管实例）。自己 spawn argv 会留下
    脱管孤儿，下次登录 LogonTrigger 再起一个 = 双 daemon。"""
    source = updater_mod._WINDOWS_RELAUNCHER_SOURCE

    assert "check=False" not in source, "/Run 的结果不许被丢弃"
    assert "result.returncode" in source, "必须查 /Run 的返回码"
    assert "Popen" not in source and "--foreground" not in source, (
        "不许绕过任务计划程序直接起 daemon")
    assert "time.sleep" not in source
    compile(source, "<relauncher>", "exec")   # 语法错 = 最后一棒当场猝死


def test_detection_proxy_respects_no_proxy(monkeypatch):
    """回归:urllib 的 ProxyHandler 尊重 no_proxy 绕过(直连),而 pip --proxy 是
    强制走代理。若不查 no_proxy 就把代理喂给 pip,会在"PyPI 在 no_proxy 里、
    同时设了代理"的机器上,把本来直连正常的升级强推进代理 → 反向搞挂。"""
    import urllib.request

    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: {"https": "http://corp-proxy:8080"})
    updater = AutoUpdater(server_url="http://server", client_id="cid", join_token="tok")

    # PyPI 不在绕过名单 → 与检测同源,喂给 pip
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    assert updater.detection_proxy == "http://corp-proxy:8080"
    assert "--proxy" in updater.pip_network_args

    # PyPI 在 no_proxy 里 → urllib 会直连,pip 也必须直连
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: True)
    assert updater.detection_proxy == ""
    assert "--proxy" not in updater.pip_network_args
