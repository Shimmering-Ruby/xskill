"""有界、固定线程数的用户画像后台刷新服务。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from xskill.recommend.client_interest import ClientInterest

logger = logging.getLogger("xskill.team.server.profile_refresh")

_STOP = object()


class ProfileRefreshService:
    """把慢 embedding 从请求线程移到固定数量的后台 daemon 线程。

    同一个 client 排队时的重复请求直接合并；执行期间有新请求时最多追加一次
    重算。队列满只返回 ``False``，不阻塞调用方。
    """

    def __init__(
        self,
        engine,
        *,
        workers: int = 4,
        queue_size: int = 1024,
        interest_factory: Callable[[str], ClientInterest] = ClientInterest,
        autostart: bool = True,
    ):
        if workers < 1:
            raise ValueError("workers 必须 >= 1")
        if queue_size < 1:
            raise ValueError("queue_size 必须 >= 1")
        self.engine = engine
        self.worker_count = workers
        self.interest_factory = interest_factory
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._condition = threading.Condition()
        self._states: dict[str, dict[str, bool | str]] = {}
        self._threads: list[threading.Thread] = []
        self._started = False
        self._accepting = True
        self._stopping = False
        self._metrics = {
            "queued": 0,
            "running": 0,
            "requested": 0,
            "enqueued": 0,
            "coalesced": 0,
            "queue_full": 0,
            "completed": 0,
            "unchanged": 0,
            "cancelled": 0,
            "failed": 0,
            "rerun": 0,
            "embed_batches": 0,
            "embed_items": 0,
            "reused_vector_items": 0,
        }
        if autostart:
            self.start()

    def start(self) -> None:
        """幂等启动固定数量的后台线程。"""
        with self._condition:
            if self._started:
                return
            if self._stopping:
                raise RuntimeError("画像刷新服务已停止")
            self._started = True
            for index in range(self.worker_count):
                thread = threading.Thread(
                    target=self._worker,
                    name=f"xskill-profile-refresh-{index + 1}",
                    daemon=True,
                )
                self._threads.append(thread)
                thread.start()

    def request(self, client_id: str) -> bool:
        """请求刷新；入队返回 True，合并也返回 True，队列满/已停止返回 False。"""
        with self._condition:
            if not self._accepting:
                return False
            self._metrics["requested"] += 1
            state = self._states.get(client_id)
            if state is not None:
                self._metrics["coalesced"] += 1
                if state["phase"] == "running" and not state["is_rerun"]:
                    state["rerun_requested"] = True
                return True
            try:
                self._queue.put_nowait(client_id)
            except queue.Full:
                self._metrics["queue_full"] += 1
                logger.warning("profile refresh queue full; skip client %s", client_id)
                return False
            self._states[client_id] = {
                "phase": "queued",
                "rerun_requested": False,
                "is_rerun": False,
            }
            self._metrics["queued"] += 1
            self._metrics["enqueued"] += 1
            self._condition.notify_all()
            return True

    submit = request

    @property
    def metrics(self) -> dict[str, int]:
        """返回一致的指标快照，调用方修改不影响服务内部状态。"""
        with self._condition:
            return dict(self._metrics)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """等待排队和执行任务清空；仅用于测试和有界停机。"""
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._states,
                timeout=timeout,
            )

    def stop(self, timeout: float = 5.0) -> bool:
        """停止接收、取消尚未执行的任务并有限等待；返回是否全部退出。"""
        with self._condition:
            self._accepting = False
            self._stopping = True
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not _STOP:
                    state = self._states.pop(item, None)
                    if state is not None and state["phase"] == "queued":
                        self._metrics["queued"] -= 1
                self._queue.task_done()
            self._condition.notify_all()
            threads = list(self._threads)
            if self._started and any(thread.is_alive() for thread in threads):
                try:
                    self._queue.put_nowait(_STOP)
                except queue.Full:  # 上面已清空；只作并发保护
                    pass

        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            if thread.ident is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        return all(not thread.is_alive() for thread in threads)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                # 一个停止标记依次传给所有线程，避免小队列在 stop 中阻塞。
                with self._condition:
                    others_alive = sum(t.is_alive() for t in self._threads) > 1
                if others_alive:
                    try:
                        self._queue.put_nowait(_STOP)
                    except queue.Full:
                        pass
                return

            client_id = item
            with self._condition:
                state = self._states.get(client_id)
                if state is None:
                    self._queue.task_done()
                    continue
                if not self._accepting:
                    self._states.pop(client_id, None)
                    self._metrics["queued"] -= 1
                    self._queue.task_done()
                    self._condition.notify_all()
                    continue
                state["phase"] = "running"
                self._metrics["queued"] -= 1
                self._metrics["running"] += 1

            self._run_once(client_id)

            run_again = False
            with self._condition:
                state = self._states.get(client_id)
                if (state is not None and self._accepting
                        and state["rerun_requested"] and not state["is_rerun"]):
                    state["rerun_requested"] = False
                    state["is_rerun"] = True
                    self._metrics["rerun"] += 1
                    run_again = True
            if run_again:
                self._run_once(client_id)

            with self._condition:
                self._states.pop(client_id, None)
                self._metrics["running"] -= 1
                self._condition.notify_all()
            self._queue.task_done()

    def _run_once(self, client_id: str) -> None:
        try:
            result = self.engine.update_user_interest(
                self.interest_factory(client_id),
                should_commit=self._should_commit,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            with self._condition:
                self._metrics["failed"] += 1
            logger.exception("profile refresh failed for client %s", client_id)
            return
        with self._condition:
            if getattr(result, "cancelled", False):
                self._metrics["cancelled"] += 1
            elif getattr(result, "changed", True):
                self._metrics["completed"] += 1
            else:
                self._metrics["unchanged"] += 1
            self._metrics["embed_batches"] += int(
                getattr(result, "embed_batches", 0),
            )
            self._metrics["embed_items"] += int(
                getattr(result, "embed_items", 0),
            )
            self._metrics["reused_vector_items"] += int(
                getattr(result, "reused_vector_items", 0),
            )

    def _should_commit(self) -> bool:
        """供引擎在最终画像 upsert 前检查停机状态。"""
        with self._condition:
            return self._accepting
