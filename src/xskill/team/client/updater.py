"""updater.py — xskill client 自动更新

TeamClient 跑起来后每隔一段时间（默认 1 小时）查 PyPI，发现新版就升级并重启。
如果 PyPI 查询或安装失败，且 client 已连接 team server，则读取 server 版本；
server 版本高于本地版本时，下载 server 暴露的 wheel 并安装。

重启机制
────────
- Linux / macOS：``os.execv`` 原地替换进程（同 PID，守护进程/systemd 不感知）
- Windows schtasks：spawn 一个脱离的短命 relauncher，等老进程真的退出后
  ``schtasks /Run`` 起一个**受管**的新实例，再退出老进程
- Windows startup_folder：spawn 新 detach 进程（用 daemon state 里登记的完整
  argv，含解释器）+ 校验它确实活着，再退出老进程

两条 Windows 路径都不再赌 Task Scheduler 的 ``RestartOnFailure``——它的语义是
"任务*启动失败*时重试"，对"动作进程跑完后非零退出"多半不触发，退出即永久掉线
到下次登录。拉不起新进程时一律抛错并**保持老进程在线跑老版本**，不留静默掉线。

版本策略
────────
- 包含预发版（a/b/rc），因为内部用 alpha 版本
- 严格大于当前版本才升级，不降级
- 网络/PyPI/server 故障不会打断主循环
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
from typing import Any, Optional

from xskill.utils.proc import windowless_subprocess_kwargs

logger = logging.getLogger("xskill.team.client.updater")

_PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"


def _team_api_url(server_url: str, path: str) -> str:
    return f"{server_url.rstrip('/')}/api/v1/team{path}"


def _team_headers(join_token: str, client_id: str) -> dict[str, str]:
    return {
        "X-Xskill-Token": join_token,
        "X-Xskill-Client": client_id,
    }


def _current_version(package: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def _latest_pypi_version(package: str) -> Optional[str]:
    """查 PyPI JSON API 取最新版本（含预发版）。超时/网络错误返回 None。"""
    import json
    import urllib.request
    url_template = os.environ.get("XSKILL_PYPI_JSON_URL", _PYPI_JSON_URL)
    url = url_template.format(package=package)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        # info.version 是 PyPI 判定的「最新稳定版」；
        # 要包含预发版，需扫 releases 键取最大版本。
        from packaging.version import InvalidVersion, Version
        # 逐个 try：曾经 `Version(v) for v in releases` 一把梭，PyPI 上**任何**
        # 一个不可解析的历史 release 串都会抛出来，被下面的 except 吞成
        # "查 PyPI 失败" —— 一个坏 release 毒死所有人的 PyPI 通道，直到它被删。
        all_versions = []
        for raw_version in data.get("releases", {}):
            try:
                parsed_version = Version(raw_version)
            except InvalidVersion:
                logger.debug("updater: 跳过不可解析的 PyPI release: %s", raw_version)
                continue
            if not parsed_version.is_devrelease:  # 排除 dev 版，保留 a/b/rc
                all_versions.append(parsed_version)
        if not all_versions:
            return None
        return str(max(all_versions))
    except Exception:
        logger.debug("updater: 查 PyPI 失败", exc_info=True)
        return None


def _server_version(
    server_url: str,
    join_token: str,
    client_id: str,
    use_proxy: bool = False,
) -> dict[str, Any] | None:
    """从 team server 读取版本信息。网络/鉴权失败返回 None。"""
    import json
    import urllib.request

    req = urllib.request.Request(
        _team_api_url(server_url, "/version"),
        headers=_team_headers(join_token, client_id),
    )
    # server 方向默认直连：ProxyHandler({}) 绕开环境代理，与 daemon httpx trust_env=False 对齐。
    opener = (urllib.request.build_opener() if use_proxy
              else urllib.request.build_opener(urllib.request.ProxyHandler({})))
    try:
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        logger.debug("updater: 查询 server 版本失败", exc_info=True)
        return None


def _download_server_wheel(
    server_url: str,
    join_token: str,
    client_id: str,
    dest_dir: Path,
    filename: str | None,
    use_proxy: bool = False,
) -> Path | None:
    """从 team server 下载 wheel 到临时目录。失败返回 None。"""
    import urllib.request

    safe_name = Path(filename or "xskill-server.whl").name
    if not safe_name.endswith(".whl"):
        safe_name = "xskill-server.whl"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    req = urllib.request.Request(
        _team_api_url(server_url, "/wheel"),
        headers=_team_headers(join_token, client_id),
    )
    # server 方向默认直连：ProxyHandler({}) 绕开环境代理，与 daemon httpx trust_env=False 对齐。
    opener = (urllib.request.build_opener() if use_proxy
              else urllib.request.build_opener(urllib.request.ProxyHandler({})))
    try:
        with opener.open(req, timeout=60) as r:
            data = r.read()
        if not data:
            logger.warning("updater: server wheel 为空")
            return None
        dest.write_bytes(data)
        return dest
    except Exception:
        logger.debug("updater: 下载 server wheel 失败", exc_info=True)
        return None


# 老进程退出前 spawn 的短命 relauncher 源码（``python -c`` 执行）。
#
# 为什么需要它：schtasks 路径原本 ``os._exit(1)`` 后赌任务计划程序的
# RestartOnFailure 把新版本拉起来。RestartOnFailure 的语义是"任务*启动失败*
# 时重试"，对"动作进程正常跑完再以非零码退出"多半**不触发** → 老进程没了、新
# 进程没来，用户一直掉线到下次 LogonTrigger（下次登录）。这就是线上"升级完就
# 再也没回来"的根因。
#
# relauncher 用内核对象等待（OpenProcess + WaitForSingleObject），不轮询、不
# sleep：等老进程真的消失后才 ``schtasks /Run``，此时任务的
# MultipleInstancesPolicy=IgnoreNew 不会拦掉我们，起来的新实例仍由任务计划
# 程序托管（不是脱管孤儿）。
#
# 三条硬约束（都在这段脚本里，别退化）：
# 1. **必须查 /Run 的 returncode 并重试**。老进程刚倒下时 /Run 可能还撞上没
#    清干净的实例而瞬时失败；曾经 check=False 一把丢弃结果——/Run 挂了老进程
#    也已经走了，没人接班 = 永久掉线，而且退出码 0 连 RestartOnFailure 都不
#    再是后手。
# 2. **失败必须落盘**。本进程是 detach 的、stdout/stderr 全 DEVNULL，不写日志
#    就是彻底静默；日志路径由父进程解析好后从 argv 传进来（父进程此刻还是健康
#    的老版本；relauncher 绝不 import xskill——刚装上的新版本可能根本 import
#    不起来，那正是我们要防的崩溃形态）。
# 3. **绝不退化成直接 spawn argv**。那会起一个脱管孤儿，下次登录 LogonTrigger
#    再起一个 = 双 daemon。重试 + 大声报错才是正确形状。
#
# 等待用 threading.Event().wait()（stdlib，裸解释器可用），不用 time.sleep。
_WINDOWS_RELAUNCHER_SOURCE = """\
import ctypes
import datetime
import locale
import subprocess
import sys
import threading

target_pid = int(sys.argv[1])
task_name = sys.argv[2]
log_path = sys.argv[3]

SYNCHRONIZE = 0x00100000
CREATE_NO_WINDOW = 0x08000000
OLD_PROCESS_WAIT_MS = 120000
RUN_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5.0


def log_line(message):
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(log_path, "a", encoding="utf-8", errors="replace") as log_file:
            log_file.write("%s xskill-relauncher [%s] %s\\n"
                           % (stamp, task_name, message))
    except OSError:
        pass   # 日志都写不下去时无处可报；不能让 relauncher 死在这一步


kernel32 = ctypes.windll.kernel32
handle = kernel32.OpenProcess(SYNCHRONIZE, False, target_pid)
if handle:
    kernel32.WaitForSingleObject(handle, OLD_PROCESS_WAIT_MS)
    kernel32.CloseHandle(handle)
else:
    log_line("OpenProcess(%d) 拿不到句柄，老进程多半已退出，直接尝试 /Run"
             % target_pid)

# schtasks 是 Windows 原生工具，输出是 ANSI(中文机器 cp936)，不是 UTF-8。
console_encoding = locale.getpreferredencoding(False)
for attempt in range(1, RUN_ATTEMPTS + 1):
    result = subprocess.run(["schtasks", "/Run", "/TN", task_name],
                            capture_output=True,
                            creationflags=CREATE_NO_WINDOW)
    output = (result.stdout + result.stderr).decode(
        console_encoding, errors="replace").strip()
    if result.returncode == 0:
        log_line("schtasks /Run 成功（第 %d 次尝试）: %s" % (attempt, output))
        break
    log_line("schtasks /Run 失败（第 %d/%d 次）rc=%s: %s"
             % (attempt, RUN_ATTEMPTS, result.returncode, output))
    if attempt < RUN_ATTEMPTS:
        threading.Event().wait(RETRY_WAIT_SECONDS)
else:
    log_line("schtasks /Run 连续 %d 次失败，任务 %s 没能拉起：客户端将一直掉线到"
             "下次登录(LogonTrigger)。请检查该计划任务是否被删除/改名/禁用。"
             % (RUN_ATTEMPTS, task_name))
"""

# Windows detach 语义：无窗 flag 之外再叠加 DETACHED_PROCESS，
# 新进程不随老进程/控制台一起走。
_DETACHED_PROCESS = 0x00000008

# spawn 出来的进程要是活过这个秒数，就认为它真的起来了（起不来的典型形态是
# CreateProcess 直接抛 OSError，或解释器立刻以非零码退出）。
_SPAWN_LIVENESS_TIMEOUT = 3.0


def _spawn_detached(argv: list[str]) -> subprocess.Popen:
    """脱离式 spawn 一个进程，并确认它没有立刻死掉。

    起不来（OSError）或起来就退（非零/零都算）一律抛错：调用方据此**放弃重启、
    保持老进程在线**。宁可继续跑老版本，也不能退出后没人接班 = 永久掉线。
    """
    no_window = windowless_subprocess_kwargs()
    proc = subprocess.Popen(
        argv,
        creationflags=no_window.get("creationflags", 0) | _DETACHED_PROCESS,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.wait(timeout=_SPAWN_LIVENESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return proc   # 还在跑 = 起来了，这是唯一的成功路径
    raise RuntimeError(
        f"新进程 {argv[0]} (pid={proc.pid}) 起来就以 {proc.returncode} 退出，"
        f"放弃重启：老进程继续在线跑老版本"
    )


def _restart() -> None:
    """升级成功后重启进程，加载新版本代码。

    - Linux/macOS：``os.execv`` 原地替换，PID 不变，对 systemd 透明
    - Windows schtasks：spawn relauncher（等老进程退出 → ``schtasks /Run``）后退出
    - Windows startup_folder：spawn 新进程（daemon state 里登记的完整 argv）后退出

    拉不起接班进程时抛错，绝不退出——退出后没人接班就是永久掉线。
    """
    logger.info("updater: 升级完成，即将重启...")
    if sys.platform != "win32":
        # os.execv 替换当前进程镜像，不产生新 PID
        os.execv(sys.executable, [sys.executable] + sys.argv)
        return

    from xskill.team.client.service import WINDOWS_TASK_NAME, read_daemon_state
    state = read_daemon_state()
    method = str(state.get("method") or "")

    if method == "schtasks":
        task_name = str(state.get("task_name") or WINDOWS_TASK_NAME)
        # 日志路径在这里解析：本进程还是健康的老版本。relauncher 自己 import
        # xskill 去问路径的话，刚装上的新版本一旦 import 不起来，relauncher 就
        # 连"我失败了"都写不出来。
        from xskill.config import get_logs_dir
        relauncher_log = get_logs_dir() / "connect-relauncher.log"
        logger.info("updater: schtasks 路径 — spawn relauncher 等本进程退出后 /Run %s"
                    "（relauncher 日志: %s）", task_name, relauncher_log)
        _spawn_detached([sys.executable, "-c", _WINDOWS_RELAUNCHER_SOURCE,
                         str(os.getpid()), task_name, str(relauncher_log)])
        os._exit(0)

    if method == "startup_folder":
        # .vbs 无重启能力，必须自己起新进程才能立即用上新版本。
        # argv 用 daemon state 里登记的那份（service._foreground_argv 生成，
        # 形如 [pythonw.exe, -m, xskill, connect, --foreground]）。曾经这里
        # 直接 Popen(sys.argv) —— 缺解释器！``-m xskill`` 下 sys.argv[0] 是
        # __main__.py 的路径，CreateProcess 它直接 WinError 193，而老进程已经
        # 自杀 = 永久掉线。
        argv = state.get("argv")
        if not isinstance(argv, list) or not argv:
            raise RuntimeError(
                "startup_folder 路径重启缺少 daemon state 里的 argv，"
                "无法确定重启命令：放弃重启，老进程继续在线跑老版本"
            )
        logger.info("updater: startup_folder 路径 — spawn 新进程后退出: %s", argv)
        _spawn_detached([str(part) for part in argv])
        os._exit(0)

    raise RuntimeError(
        f"未知的 Windows 持久化方式 {method!r}（daemon state 缺失或损坏），"
        f"无从拉起接班进程：放弃重启，老进程继续在线跑老版本"
    )


class AutoUpdater:
    """后台线程：每隔 ``interval`` 秒检查 PyPI，有新版则升级并重启。

    用法::

        updater = AutoUpdater()
        updater.start()
        # ... 主循环 ...
        updater.stop()
    """

    def __init__(
        self,
        package: str = "xskill",
        interval: float = 3600,       # 默认 1 小时
        pypi_url: str | None = None,
        server_url: str | None = None,
        client_id: str | None = None,
        join_token: str | None = None,
        use_proxy: bool = False,
    ):
        # pypi_url 缺省 None = 不传 -i，尊重用户机器的 pip 配置（pip.ini /
        # pip.conf 的 index-url，内网通常配了企业镜像）。曾写死
        # https://pypi.org/simple/ ——强行绕过企业镜像直连公网，代理环境下
        # 必超时，用户配好的镜像形同虚设。显式传入时才覆盖。
        self.package = package
        self.interval = interval
        self.pypi_url = pypi_url
        self.server_url = server_url
        self.client_id = client_id
        self.join_token = join_token
        # server 方向请求默认直连；True 时走系统/环境代理（跟随 connect 的 --use-proxy）。
        self.use_proxy = use_proxy
        self._last_pip_failure_summary = ""
        self._last_server_fallback_failure_summary = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动后台检查线程（daemon=True，主进程退出时自动终止）。"""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="xskill-auto-updater",
        )
        self._thread.start()
        logger.info("updater: 自动更新已启用（每 %.0f 分钟检查一次）",
                    self.interval / 60)

    def stop(self) -> None:
        self._stop.set()

    # ─────────────────────────────────────────────────────────────

    # 首检延迟窗口（秒）。曾经等满一个 interval（默认 3600s）才首检，而这个
    # 时钟每次进程重启都从零开始——频繁重启/每天登录的机器可能长期、甚至永远
    # 到不了第一次检查。改成起来就检；30~120s 的均匀抖动只为错开"全公司同一
    # 分钟一起打 PyPI"，不是为了拖延。
    _FIRST_CHECK_MIN_DELAY = 30.0
    _FIRST_CHECK_MAX_DELAY = 120.0

    def _loop(self) -> None:
        first_delay = min(
            self.interval,
            random.uniform(self._FIRST_CHECK_MIN_DELAY, self._FIRST_CHECK_MAX_DELAY),
        )
        self._stop.wait(first_delay)
        while not self._stop.is_set():
            try:
                self._check_and_update()
            except Exception:
                # 本线程 daemon=True：异常逃到这里之外 = 线程死掉 = 自动更新在整个
                # 进程生命周期内静默消失（照 daemon._tick 的兜底写法）。
                logger.warning("updater: 本轮检查异常，跳过，等下一轮", exc_info=True)
            self._stop.wait(self.interval)

    def _check_and_update(self) -> None:
        current_str = _current_version(self.package)
        if not current_str:
            logger.debug("updater: 无法读取当前版本，跳过本次检查")
            return
        try:
            from packaging.version import Version
            current = Version(current_str)
        except Exception:
            logger.debug("updater: 当前版本不可解析: %s", current_str, exc_info=True)
            return

        latest_str = _latest_pypi_version(self.package)
        if not latest_str:
            self._check_server_fallback(current_str, current, reason="pypi_query_failed")
            return

        try:
            latest = Version(latest_str)
        except Exception:
            return

        if latest <= current:
            logger.debug("updater: 当前版本 %s 不低于 PyPI 最新 %s",
                         current_str, latest_str)
            # 内网场景 server 预置的 wheel 常常领先公网 PyPI（先内部分发、
            # 后补发 PyPI）。这里曾直接 return——只要 pypi.org 的 JSON API
            # 可达且没有新版，server 渠道就永远不会被查询，内网更新静默
            # 失效。PyPI 无新版 ≠ 没有更新。
            self._check_server_fallback(current_str, current,
                                        reason="pypi_not_ahead")
            return

        logger.info("updater: 发现新版本 %s（当前 %s），开始升级...",
                    latest_str, current_str)
        if self._install(latest_str):
            _restart()   # 升级成功后重启，不会走到这行之后的代码
            # （_restart 在 Windows 上 os._exit；Linux 上 execv）
            return
        if not self._check_server_fallback(current_str, current,
                                           reason="pypi_install_failed"):
            logger.warning(
                "updater: 升级到 %s 失败——pip 原因: %s；server 回退原因: %s",
                latest_str,
                self._last_pip_failure_summary or "未知",
                self._last_server_fallback_failure_summary or "未知")

    # pip 卡死的硬上限。pip 自带的 socket timeout 只覆盖"完全无数据"，
    # 代理黑洞式的涓涓细流永远不触发；而 updater 是单线程循环，一次
    # subprocess.run 挂死 = 之后每小时的检查全部消失，自动更新静默死亡。
    _PIP_TIMEOUT = 600

    @property
    def detection_proxy(self) -> str:
        """检测通道（urllib）实际会用的代理；没有则空串。

        安装通道必须和检测通道同源，否则就是线上那条日志：``_latest_pypi_version``
        用 urllib 查到了 0.6.17，pip 却 "No matching distribution ... (ssl)"，
        **每个版本**都升不上去。不对称出在代理来源上——``urlopen`` 的默认 opener
        是 ``ProxyHandler(getproxies())``，而 ``getproxies()`` 在 Windows 上除了
        环境变量还读注册表里的系统/IE 代理设置；pip 只认环境变量，读不到系统代理，
        于是在公司代理/MITM 机器上直连必挂。这里把检测用的同一个代理显式喂给 pip。
        """
        import urllib.parse
        import urllib.request
        target = self.pypi_url or os.environ.get("XSKILL_PYPI_JSON_URL", _PYPI_JSON_URL)
        split = urllib.parse.urlsplit(target)
        scheme = split.scheme or "https"
        host = split.hostname or ""
        # 必须先问 no_proxy：urllib 的 ProxyHandler 会**尊重**绕过名单直连，而
        # pip 的 --proxy 是**强制**走代理。不查就喂，会在"PyPI 在 no_proxy 里、
        # 同时又设了代理"的机器上，把本来直连正常的升级强推进代理——反方向的
        # 新不对称，等于把能升级的机器搞挂。
        if host and urllib.request.proxy_bypass(host):
            return ""
        return urllib.request.getproxies().get(scheme, "")

    @property
    def pip_network_args(self) -> list[str]:
        """pip 的 index / 代理参数，与检测通道同源。

        不显式配置 ``pypi_url`` 时不传 ``-i``：尊重机器上 pip.ini/pip.conf 里的
        企业镜像（曾写死 ``-i https://pypi.org/simple/`` 绕过镜像，代理环境必超时）。
        """
        args: list[str] = []
        if self.pypi_url:
            args += ["-i", self.pypi_url]
        proxy = self.detection_proxy
        if proxy:
            args += ["--proxy", proxy]
        return args

    @property
    def subprocess_text_kwargs(self) -> dict[str, Any]:
        """跑 pip / 核验解释器的解码与环境参数。

        pip 是 Python 程序，``PYTHONIOENCODING=utf-8`` 能钉死它 stdout 的编码，
        于是我们可以确定性地按 utf-8 解；``errors="replace"`` 兜住 pip 转发的
        Windows 原生 GBK 报错。曾经只写 ``text=True``：按 cp936-strict 解码，
        UTF-8 输出变乱码，非法字节直接 UnicodeDecodeError——还会被下面宽 except
        误报成"pip 失败"，把一个**可能已经装成功**的版本丢掉。
        """
        return {
            "encoding": "utf-8",
            "errors": "replace",
            "env": {**os.environ, "PYTHONIOENCODING": "utf-8"},
        }

    def _verify_installed(self, target_version: str) -> str:
        """另起解释器核验装后结果：包能 import + 版本已达 target。

        返回失败原因；空串 = 通过。装完当前进程的 metadata 不刷新，必须新起进程。
        """
        module_name = self.package.replace("-", "_")
        # 只读 importlib.metadata.version 不够：装上了但 import 不起来（同一张
        # 坏网络拉不到传递依赖是常态）也会"核验通过"→ 重启 → 崩溃循环。
        verify_code = (
            "import importlib, importlib.metadata as metadata; "
            f"importlib.import_module({module_name!r}); "
            f"print(metadata.version({self.package!r}))"
        )
        try:
            verify = subprocess.run(
                [sys.executable, "-c", verify_code],
                capture_output=True, timeout=30,
                **self.subprocess_text_kwargs,
                **windowless_subprocess_kwargs())
        except Exception:
            logger.debug("updater: 装后核验子进程未能执行，按 pip 退出码为准",
                         exc_info=True)
            return ""
        if verify.returncode != 0:
            detail = ((verify.stderr or "").strip() or (verify.stdout or "").strip()
                      or f"核验进程以 {verify.returncode} 退出")
            return f"装后 import {module_name} 失败（装坏了/缺传递依赖）: {detail.splitlines()[-1]}"
        installed_version = (verify.stdout or "").strip()
        # 核验取不到版本则信任 pip 退出码；拿到一个可解析且明确不同的版本才判失败。
        if not installed_version:
            return ""
        from packaging.version import InvalidVersion, Version
        try:
            reached_target = Version(installed_version) == Version(target_version)
        except InvalidVersion:
            return ""
        if not reached_target:
            return f"装完核验版本 {installed_version} 未达目标 {target_version}"
        return ""

    def _install(self, target_version: str) -> bool:
        """用 pip 升级到指定版本。返回是否成功。"""
        cmd = [
            sys.executable, "-m", "pip", "install", "--upgrade",
            f"{self.package}=={target_version}",
            "--timeout", "15", "--retries", "2",
            "-q",     # quiet：只打印错误
        ] + self.pip_network_args
        try:
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=self._PIP_TIMEOUT,
                                    **self.subprocess_text_kwargs,
                                    **windowless_subprocess_kwargs())
        except subprocess.TimeoutExpired:
            self._last_pip_failure_summary = f"pip 超过 {self._PIP_TIMEOUT}s 未退出"
            logger.warning("updater: %s，放弃本次升级", self._last_pip_failure_summary)
            return False
        except Exception as pip_error:
            self._last_pip_failure_summary = f"执行 pip 异常: {pip_error}"
            logger.warning("updater: 执行 pip 失败", exc_info=True)
            return False
        if result.returncode != 0:
            self._last_pip_failure_summary = (
                (result.stderr.strip() or result.stdout.strip()
                 or "pip 返回非零退出码").splitlines()[-1])
            logger.warning("updater: pip 升级失败:\n%s",
                           result.stderr.strip() or result.stdout.strip())
            return False
        verify_failure = self._verify_installed(target_version)
        if verify_failure:
            self._last_pip_failure_summary = verify_failure
            logger.warning("updater: %s，改走 server 回退", verify_failure)
            return False
        logger.info("updater: 升级到 %s 成功", target_version)
        return True

    def _check_server_fallback(
        self, current_str: str, current, *, reason: str, restart: bool = True,
    ) -> bool:
        """PyPI 不可用时从 server 下载 wheel 回退升级，返回是否成功。

        ``restart=False`` 供一次性 CLI 用：装完直接返回，不 ``_restart``——
        CLI 进程重启只会重跑 update 命令本身，还会以报错收场。
        """
        if not (self.server_url and self.client_id and self.join_token):
            self._last_server_fallback_failure_summary = "无 server 回退配置"
            logger.debug("updater: 无 server 回退配置，跳过（%s）", reason)
            return False

        info = _server_version(self.server_url, self.join_token, self.client_id,
                               self.use_proxy)
        if not info:
            self._last_server_fallback_failure_summary = "查询 server 版本失败"
            return False

        server_version_str = str(info.get("version") or "")
        try:
            from packaging.version import Version
            server_version = Version(server_version_str)
        except Exception:
            self._last_server_fallback_failure_summary = (
                f"server 版本不可解析: {server_version_str}")
            logger.debug("updater: server 版本不可解析: %s",
                         server_version_str, exc_info=True)
            return False

        if server_version <= current:
            self._last_server_fallback_failure_summary = (
                f"server 版本 {server_version_str} 不高于当前 {current_str}")
            logger.debug("updater: server 版本 %s 不高于当前版本 %s",
                         server_version_str, current_str)
            return False
        if not info.get("wheel_available"):
            self._last_server_fallback_failure_summary = (
                f"server 版本 {server_version_str} 未提供 wheel")
            logger.warning("updater: server 版本 %s 可用，但未提供 wheel",
                           server_version_str)
            return False

        with tempfile.TemporaryDirectory(prefix="xskill-update-") as td:
            wheel = _download_server_wheel(
                self.server_url,
                self.join_token,
                self.client_id,
                Path(td),
                str(info.get("wheel_filename") or ""),
                self.use_proxy,
            )
            if wheel is None:
                self._last_server_fallback_failure_summary = "下载 server wheel 失败"
                return False
            logger.info("updater: PyPI 不可用（%s），改用 server wheel 升级到 %s",
                        reason, server_version_str)
            if self._install_wheel(wheel, server_version_str):
                if restart:
                    _restart()
                return True
            self._last_server_fallback_failure_summary = "安装 server wheel 失败"
            return False

    def _install_wheel(self, wheel_path: Path, target_version: str) -> bool:
        """用 pip 安装 server 下载的 wheel。返回是否成功**且版本确已推进到 target**。"""
        # wheel 本身是本地文件，但它的传递依赖仍要从 index 拉——index/代理同样
        # 跟检测通道同源，否则 wheel 装上了、依赖没装上（D7 的核验会拦下来）。
        cmd = [
            sys.executable, "-m", "pip", "install", "--upgrade",
            str(wheel_path),
            "-q",
        ] + self.pip_network_args
        try:
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=self._PIP_TIMEOUT,
                                    **self.subprocess_text_kwargs,
                                    **windowless_subprocess_kwargs())
        except subprocess.TimeoutExpired:
            logger.warning("updater: pip 装 server wheel 超过 %ds 未退出，放弃",
                           self._PIP_TIMEOUT)
            return False
        except Exception:
            logger.warning("updater: 执行 pip 安装 server wheel 失败", exc_info=True)
            return False
        if result.returncode != 0:
            logger.warning("updater: server wheel 安装失败:\n%s",
                           result.stderr.strip() or result.stdout.strip())
            return False
        # 装完核验能 import + 版本确已推进，未达标则判失败——否则装了个没把版本
        # 推上去（或根本 import 不起来）的 wheel 仍触发 _restart，客户端回来还是
        # 老版本、下轮又升又重启，把用户反复打掉线。
        verify_failure = self._verify_installed(target_version)
        if verify_failure:
            logger.warning("updater: server wheel %s，判失败不重启", verify_failure)
            return False
        logger.info("updater: 安装 server wheel 成功: %s", wheel_path.name)
        return True
