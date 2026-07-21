"""Agent worker concurrency primitives.

The watcher owns four independent bounded executors.  Each executor accepts at
most ``workers`` running calls plus ``workers * 2`` waiting calls and rejects
additional submissions without blocking the watcher thread.

Cluster agents perform model inference concurrently, but their filesystem
mutations are funneled through :class:`ClusterWriteQueue` so candidate files and
new skill repositories are changed in a deterministic order.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable


def _install_worker_context() -> None:
    """Install the asyncio loop required by agno on Python 3.9 workers."""
    import asyncio

    asyncio.set_event_loop(asyncio.new_event_loop())


@dataclass
class _SubmissionState:
    started: bool = False


class BoundedExecutor:
    """A non-blocking, observable ``ThreadPoolExecutor``.

    ``ThreadPoolExecutor`` itself has an unbounded waiting queue.  A semaphore
    reserves one slot before submission, giving this wrapper a hard total
    capacity of ``workers * 3``.  Rejected work is never submitted and the
    caller can leave its durable DB/file state untouched for the next scan.
    """

    def __init__(self, name: str, workers: int):
        if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
            raise ValueError(f"{name}.workers 必须是正整数")
        self.name = name
        self.workers = workers
        self.queue_capacity = workers * 2
        self.total_capacity = workers * 3
        self._slots = threading.BoundedSemaphore(self.total_capacity)
        self._lock = threading.Lock()
        self._running = 0
        self._queued = 0
        self._completed = 0
        self._failed = 0
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"xskill-{name}",
            initializer=_install_worker_context,
        )

    def submit(self, function: Callable[..., Any], /, *args, **kwargs) -> Future | None:
        """Submit immediately or return ``None`` when this pool is full."""
        if not self._slots.acquire(blocking=False):
            return None
        state = _SubmissionState()
        state_lock = threading.Lock()
        with self._lock:
            self._queued += 1

        def run():
            with state_lock:
                state.started = True
            with self._lock:
                self._queued -= 1
                self._running += 1
            try:
                from xskill.utils.rate_limit import request_source

                with request_source(self.name):
                    result = function(*args, **kwargs)
            except BaseException:
                with self._lock:
                    self._failed += 1
                raise
            else:
                with self._lock:
                    self._completed += 1
                return result
            finally:
                with self._lock:
                    self._running -= 1
                self._slots.release()

        try:
            future = self._executor.submit(run)
        except BaseException:
            with self._lock:
                self._queued -= 1
            self._slots.release()
            raise

        def release_cancelled(_future: Future) -> None:
            if not _future.cancelled():
                return
            with state_lock:
                if state.started:
                    return
                state.started = True
            with self._lock:
                self._queued -= 1
            self._slots.release()

        future.add_done_callback(release_cancelled)
        return future

    @property
    def available_capacity(self) -> int:
        with self._lock:
            return self.total_capacity - self._running - self._queued

    @property
    def status(self) -> dict[str, int | float]:
        with self._lock:
            occupied = self._running + self._queued
            return {
                "workers": self.workers,
                "queue_capacity": self.queue_capacity,
                "total_capacity": self.total_capacity,
                "running": self._running,
                "queued": self._queued,
                "completed": self._completed,
                "failed": self._failed,
                "occupancy": occupied / self.total_capacity,
            }

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


class ClusterResultRecorder:
    """Thread-safe record of successful writes made for one ClusterAgent call."""

    def __init__(self):
        self._lock = threading.Lock()
        self._results: dict[str, tuple[str, int]] = {}

    def record(self, atom_id: str, skill_name: str, weightscore: int) -> None:
        with self._lock:
            self._results[atom_id] = (skill_name, int(weightscore))

    def get(self, atom_id: str) -> tuple[str, int] | None:
        with self._lock:
            return self._results.get(atom_id)

    def snapshot(self) -> dict[str, tuple[str, int]]:
        with self._lock:
            return dict(self._results)


class ClusterWriteQueue:
    """Single-thread queue for ClusterAgent filesystem mutations."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queued = 0
        self._running = 0
        self._completed = 0
        self._failed = 0
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="xskill-cluster-write",
            initializer=_install_worker_context,
        )

    def call(self, function: Callable[[], Any]) -> Any:
        with self._lock:
            self._queued += 1

        def run():
            with self._lock:
                self._queued -= 1
                self._running += 1
            try:
                result = function()
            except BaseException:
                with self._lock:
                    self._failed += 1
                raise
            else:
                with self._lock:
                    self._completed += 1
                return result
            finally:
                with self._lock:
                    self._running -= 1

        return self._executor.submit(run).result()

    @property
    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": self._queued,
                "running": self._running,
                "completed": self._completed,
                "failed": self._failed,
            }

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
