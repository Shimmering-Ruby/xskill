"""test_event_loop_safety.py —— 事件循环阻塞 / 优雅退出回归

三类历史事故的防回归：

1. **async 路由里的同步网络 I/O**：embed/LLM 是同步 httpx（最长 60s），
   写成 ``async def`` 会把整个事件循环冻住——embedding 后端一慢，所有
   端点集体"连不上"（含健康检查）。这类端点必须是同步 ``def``（FastAPI
   自动丢 anyio 线程池）。
2. **跨线程裸捅 asyncio.Queue**：worker 线程 put_nowait 不写 self-pipe，
   selector 睡着时消费者不被唤醒。必须经 call_soon_threadsafe。
3. **不可中断的重试退避**：最坏 8×60s 超时 + 182s 退避 ≈ 11 分钟,
   ThreadPoolExecutor 非 daemon 线程 atexit join → supervisor 10s 后
   SIGKILL。SHUTTING_DOWN 竖旗后重试/等桶必须立即放弃。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from xskill.utils.shutdown import SHUTTING_DOWN, request_shutdown


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    """SHUTTING_DOWN 是进程级全局旗,测试间必须复位。"""
    SHUTTING_DOWN.clear()
    yield
    SHUTTING_DOWN.clear()


# ---------------------------------------------------------------------------
# 1. 网络阻塞端点必须是同步 def
# ---------------------------------------------------------------------------

def test_embed_bound_endpoints_are_sync():
    from xskill.api import app as app_mod

    for fn_name in ("api_search_trajectories", "api_search_skills",
                    "api_resolve_skill", "api_reindex"):
        fn = getattr(app_mod, fn_name, None)
        if fn is None:
            continue  # 端点定义在 create_app 闭包里时无法直查,跳过
        assert not inspect.iscoroutinefunction(fn), (
            f"{fn_name} 内部走同步 httpx,必须是 def 而非 async def,"
            f"否则 embedding 后端一慢就冻住整个事件循环")


def test_team_sync_is_sync():
    from xskill.team.server import api as team_api

    assert not inspect.iscoroutinefunction(team_api.team_sync), (
        "team_sync → update_user_interest → encode_batch 是同步网络调用,"
        "必须是 def:每个 client 周期性打它,async 会冻住事件循环")


# ---------------------------------------------------------------------------
# 2. SSE 队列跨线程投递
# ---------------------------------------------------------------------------

def test_thread_safe_queue_wakes_consumer_from_worker_thread():
    """裸 put_nowait 不唤醒 selector;经 call_soon_threadsafe 必须立即唤醒。

    不设兜底定时器(SSE ping),消费者若 1s 内拿到数据即证明唤醒路径正确。
    """
    from xskill.api.sse import ThreadSafeQueue

    async def main():
        q = ThreadSafeQueue()
        threading.Thread(
            target=lambda: (time.sleep(0.1), q.put_nowait("hi")),
            daemon=True,
        ).start()
        return await asyncio.wait_for(q.get(), timeout=1.0)

    assert asyncio.run(main()) == "hi"


def test_thread_safe_queue_drops_after_loop_closed():
    """loop 关闭后投递只应静默丢弃,不允许在 worker 线程里炸栈。"""
    from xskill.api.sse import ThreadSafeQueue

    async def make():
        return ThreadSafeQueue()

    q = asyncio.run(make())  # asyncio.run 返回时 loop 已关闭
    q.put_nowait("late")  # 不抛即通过


# ---------------------------------------------------------------------------
# 3. SHUTTING_DOWN 竖旗后,重试与等桶立即放弃
# ---------------------------------------------------------------------------

def test_retry_aborts_immediately_on_shutdown():
    from xskill.agents.agno_factory import _wrap_with_retry

    class FailingModel:
        calls = 0

        def invoke(self, messages, **kwargs):
            FailingModel.calls += 1
            raise TimeoutError("request timed out")

    model = _wrap_with_retry(FailingModel(), {"retry_base_delay": 30.0})
    request_shutdown()
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        model.invoke([])
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, (
        f"竖旗后首个退避 (30s) 应立即中断,实测 {elapsed:.1f}s")
    assert FailingModel.calls == 1, "竖旗后不允许再发起新尝试"


def test_retry_still_retries_without_shutdown():
    """确认竖旗逻辑没有误伤正常重试路径。"""
    from xskill.agents.agno_factory import _wrap_with_retry

    class FlakyModel:
        calls = 0

        def invoke(self, messages, **kwargs):
            FlakyModel.calls += 1
            if FlakyModel.calls < 3:
                raise TimeoutError("request timed out")
            return "ok"

    model = _wrap_with_retry(FlakyModel(), {"retry_base_delay": 0.01})
    assert model.invoke([]) == "ok"
    assert FlakyModel.calls == 3


def test_rate_bucket_wait_aborts_on_shutdown():
    from xskill.utils.rate_limit import TokenBucket

    bucket = TokenBucket(rpm=1)
    assert bucket.acquire_rpm(timeout=60) == 0.0  # 耗尽唯一 token
    request_shutdown()
    t0 = time.monotonic()
    wait = bucket.acquire_rpm(timeout=60)
    elapsed = time.monotonic() - t0
    assert wait > 0, "竖旗后应按桶耗尽路径返回剩余等待秒数"
    assert elapsed < 1.0, f"竖旗后等桶应立即返回,实测 {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# 5. anyio 默认线程池容量(2026-07-10 二次事故回归)
# ---------------------------------------------------------------------------

def test_startup_raises_thread_pool_capacity():
    """所有 def 路由(search/resolve/team_sync/整个 dashboard)共享 anyio
    默认线程池(40)。embedding 后端慢时 /sync 单请求占线程数分钟,40 被
    打满 → 网页整体失联。startup 必须把容量设成 server.thread_pool_tokens
    (默认 80，显式旧配置仍可设为 300)。只能在 startup 里设——create_app
    时还没有事件循环，
    current_default_thread_limiter 会抛 AsyncLibraryNotFoundError。"""
    from xskill.api import app as app_mod

    src = inspect.getsource(app_mod.create_app)
    assert "current_default_thread_limiter" in src, (
        "startup 里的线程池扩容被删了——慢 embedding 后端会再次打满 40 "
        "线程导致网页失联")
    assert "thread_pool_tokens" in src, "线程池容量必须可配,不许写死"


def test_thread_pool_limiter_settable_in_loop():
    """守住 anyio 版本兼容:total_tokens setter 必须在事件循环内可用。"""
    import anyio
    import anyio.to_thread

    async def main():
        limiter = anyio.to_thread.current_default_thread_limiter()
        before = limiter.total_tokens
        limiter.total_tokens = 123
        assert anyio.to_thread.current_default_thread_limiter().total_tokens == 123
        limiter.total_tokens = before

    anyio.run(main)
