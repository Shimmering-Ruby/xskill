"""进程退出协调：让长重试循环在优雅退出时尽快放弃。

背景（长期 SIGKILL 病根）：agno_factory 的 LLM 重试按默认配置最坏
8×60s 超时 + 182s 退避 ≈ 11 分钟；ThreadPoolExecutor worker 是非
daemon 线程，解释器退出要 join 它们。supervisor 默认只等 10s →
SIGTERM 必然升级 SIGKILL。

解法：全局一个 Event。app shutdown（uvicorn 收到 SIGTERM 后触发）
set 它；重试循环用 ``SHUTTING_DOWN.wait(delay)`` 代替 ``time.sleep``，
事件一到立即放弃重试抛出原异常，worker 迅速归还，join 秒回。
残余最坏等待 = 一次在途 httpx 读（request_timeout，默认 60s）。
"""

from __future__ import annotations

import threading

SHUTTING_DOWN = threading.Event()


def request_shutdown() -> None:
    """标记进程正在退出。幂等，可重复调用。"""
    SHUTTING_DOWN.set()
