"""定时短命子进程调度器:daemon 线程周期性 spawn 一个短命子进程跑重活(sweep /
画像 batch),算完即退。子进程在独立解释器进程里跑,GIL 与 web 事件循环彻底隔离。

照抄 ``team/client/updater.py`` AutoUpdater 的"daemon 线程 + Event.wait + subprocess.run
(带 timeout 硬上限)"范式:

- ``subprocess.run`` **阻塞**本调度线程直到子进程退出,故同一任务天然串行、不可能自
  重叠,无需额外锁或"上一个没跑完就跳过"的判断;
- 调度线程只等子进程(I/O 阻塞、释放 GIL),不做任何重计算,不占 web 事件循环;
- 用 ``Event.wait(interval)`` 定时(禁 time.sleep),``stop()`` 竖旗即时中断等待。
"""
from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger("xskill.pipeline.scheduler")


class IntervalSubprocessScheduler:
    """每隔 ``interval`` 秒 spawn 一次 ``command`` 短命子进程,算完即退。"""

    def __init__(self, name: str, command: list[str], *, interval: float,
                 timeout: float):
        if interval <= 0:
            raise ValueError("interval 必须 > 0")
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        self._name = name
        self._command = list(command)
        self._interval = float(interval)
        self._timeout = float(timeout)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """幂等启动调度 daemon 线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"xskill-sched-{self._name}", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """竖停机旗、中断 wait,并有界 join 调度线程。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        # 先等一个周期再首跑:避免 startup 瞬间与其它初始化抢资源(照 AutoUpdater)。
        # Event.wait 返回 True 表示被 stop 竖旗中断 → 退出循环。
        while not self._stop.wait(self._interval):
            self._run_once()

    def _run_once(self) -> None:
        try:
            result = subprocess.run(
                self._command, capture_output=True, text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("调度任务 %s 超过 %.0fs 上限被杀", self._name, self._timeout)
            return
        except OSError:
            logger.warning("调度任务 %s 启动子进程失败", self._name, exc_info=True)
            return
        if result.returncode != 0:
            logger.warning(
                "调度任务 %s 退出码=%d stderr=%s", self._name, result.returncode,
                (result.stderr or result.stdout or "")[:500],
            )
