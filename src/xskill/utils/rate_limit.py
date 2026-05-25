"""utils/rate_limit.py —— LLM 请求限流(RPM + TPM 双桶)
═══════════════════════════════════════════════════════════════
DIY 实现,零额外依赖。设计基线:
- 永不 import tiktoken / tokenizers / litellm(详见 docs/adr/0001)
- 字符粗估 token 数,response.usage 存在则自校准,缺失则保留估算
- 线程安全(threading.Lock),用 time.monotonic 防系统时钟回拨
- 配置缺省 = 不限流(快路径)
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class TokenBucket:
    """RPM + TPM 双桶。

    acquire_rpm / acquire_tpm 分别管两个独立桶,wrapper 调用时两个都要 acquire。
    clock 参数可注入(测试用 FakeClock),生产默认 time.monotonic。
    """

    def __init__(
        self,
        *,
        rpm: Optional[int] = None,
        tpm: Optional[int] = None,
        burst: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.rpm = rpm
        self.tpm = tpm
        # burst 默认 = ceil(rate/6),约 10 秒预算的瞬时突发
        self._rpm_burst = burst if burst is not None else (max(1, rpm // 6) if rpm else 0)
        self._tpm_burst = burst if burst is not None else (max(1, tpm // 6) if tpm else 0)
        self._clock = clock or time.monotonic

        self._rpm_tokens = float(self._rpm_burst)
        self._tpm_tokens = float(self._tpm_burst)
        self._last_rpm_refill = self._clock()
        self._last_tpm_refill = self._clock()
        self._lock = threading.Lock()

    # ─── RPM ─────────────────────────────────────────────────

    def _refill_rpm(self) -> None:
        if not self.rpm:
            return
        now = self._clock()
        elapsed = now - self._last_rpm_refill
        self._rpm_tokens = min(
            float(self._rpm_burst),
            self._rpm_tokens + elapsed * (self.rpm / 60.0),
        )
        self._last_rpm_refill = now

    def acquire_rpm(self, *, timeout: float = 0.0) -> float:
        """尝试取 1 个 RPM token。返回值:
        - 0.0  → 已扣减,可立刻发请求
        - > 0  → 还需等待这么多秒
        timeout=0 表示纯查询不阻塞;> 0 时本方法内自旋等待至 timeout 上限。
        """
        if not self.rpm:
            return 0.0
        deadline = self._clock() + timeout
        while True:
            with self._lock:
                self._refill_rpm()
                if self._rpm_tokens >= 1:
                    self._rpm_tokens -= 1
                    return 0.0
                shortfall = 1 - self._rpm_tokens
                wait = shortfall / (self.rpm / 60.0)
            if timeout <= 0 or self._clock() + wait > deadline:
                return wait
            time.sleep(min(wait, max(0.01, deadline - self._clock())))

    # ─── TPM ─────────────────────────────────────────────────

    def _refill_tpm(self) -> None:
        if not self.tpm:
            return
        now = self._clock()
        elapsed = now - self._last_tpm_refill
        self._tpm_tokens = min(
            float(self._tpm_burst),
            self._tpm_tokens + elapsed * (self.tpm / 60.0),
        )
        self._last_tpm_refill = now

    def acquire_tpm(self, n: int, *, timeout: float = 0.0) -> float:
        """扣 n 个 TPM token。语义同 acquire_rpm。"""
        if not self.tpm or n <= 0:
            return 0.0
        deadline = self._clock() + timeout
        while True:
            with self._lock:
                self._refill_tpm()
                if self._tpm_tokens >= n:
                    self._tpm_tokens -= n
                    return 0.0
                shortfall = n - self._tpm_tokens
                wait = shortfall / (self.tpm / 60.0)
            if timeout <= 0 or self._clock() + wait > deadline:
                return wait
            time.sleep(min(wait, max(0.01, deadline - self._clock())))

    def reconcile_tpm(self, *, estimated: int, actual: int) -> None:
        """请求完成后,按真实 token 数调整桶。
        actual < estimated → 退还; actual > estimated → 补扣(可能让桶变负)。
        """
        if not self.tpm:
            return
        delta = estimated - actual  # >0 表示多扣了应退还
        with self._lock:
            self._tpm_tokens = min(
                float(self._tpm_burst),
                self._tpm_tokens + delta,
            )
