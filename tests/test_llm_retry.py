"""tests/test_llm_retry.py — 脆弱 API 强壮持续重试（_wrap_with_retry）"""
from __future__ import annotations

import pytest

from xskill.agents.agno_factory import _wrap_with_retry, _is_transient_error


class _FlakyModel:
    """前 fail_n 次抛 exc,之后返回 ok。记录调用次数。"""

    def __init__(self, fail_n, exc):
        self.calls = 0
        self.fail_n = fail_n
        self.exc = exc

    def invoke(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_n:
            raise self.exc
        return "ok"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # 别真睡——把退避 sleep 打掉,测试秒过
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    from xskill.utils.shutdown import SHUTTING_DOWN
    monkeypatch.setattr(SHUTTING_DOWN, "wait", lambda *_args: False)


def test_transient_classification():
    from agno.exceptions import ModelProviderError

    assert _is_transient_error(Exception("Error code: 429 - rate limit"))
    assert _is_transient_error(Exception("Connection reset by peer"))
    assert _is_transient_error(Exception("503 Service Unavailable"))
    # agno ModelProviderError: status_code 优先于 message 关键词
    assert _is_transient_error(
        ModelProviderError("server error", status_code=500)
    )
    assert _is_transient_error(
        ModelProviderError("当前并发请求过多，请稍后重试", status_code=429)
    )
    # 不可重试:上下文超长 / 400 invalid
    assert not _is_transient_error(Exception("maximum input length is 131071"))
    assert not _is_transient_error(Exception("invalid_request_error"))


def test_local_rate_limit_exhausted_is_transient():
    """本地限流桶耗尽（RateLimitExhausted）必须判瞬时并重试恢复。

    回归：消息 "RPM bucket exhausted" 与 hint "rpm exhausted" 子串对不上，
    曾被误判非瞬时 → 1/8 直接放弃，高并发下 cluster batch 全部 0 落地。
    """
    from xskill.utils.rate_limit import RateLimitExhausted
    exhausted = RateLimitExhausted("RPM bucket exhausted, need wait 8.6s")
    assert _is_transient_error(exhausted)
    assert _is_transient_error(
        RateLimitExhausted("TPM bucket exhausted, need wait 2.0s"))
    model = _FlakyModel(fail_n=3, exc=exhausted)
    _wrap_with_retry(model, {"max_retries": 8})
    assert model.invoke([]) == "ok"
    assert model.calls == 4  # 3 次桶耗尽重试 + 1 次成功


def test_retry_recovers_after_transient_failures():
    m = _FlakyModel(fail_n=3, exc=Exception("429 too many requests"))
    _wrap_with_retry(m, {"max_retries": 8})
    assert m.invoke([]) == "ok"
    assert m.calls == 4  # 3 失败 + 1 成功


def test_retry_gives_up_after_max_and_does_not_hang():
    """一直 429 → 到 max_retries 就抛,绝不无限循环挂死线程。"""
    m = _FlakyModel(fail_n=999, exc=Exception("429 rate limit"))
    _wrap_with_retry(m, {"max_retries": 5})
    with pytest.raises(Exception):
        m.invoke([])
    assert m.calls == 5  # 有界:正好试 5 次


def test_non_transient_raises_immediately_no_retry():
    """400/上下文超长 → 立即抛,不浪费重试。"""
    m = _FlakyModel(fail_n=999, exc=Exception("maximum input length is 131071 tokens"))
    _wrap_with_retry(m, {"max_retries": 8})
    with pytest.raises(Exception):
        m.invoke([])
    assert m.calls == 1  # 只试一次


def test_retry_and_exhaustion_are_written_to_active_trace(tmp_path):
    from xskill.agents.agent_trace import trace_to

    model = _FlakyModel(fail_n=999, exc=Exception("429 rate limit"))
    _wrap_with_retry(model, {"max_retries": 3})
    sink = tmp_path / "skill.log"
    with trace_to(sink):
        with pytest.raises(Exception):
            model.invoke([])

    trace = sink.read_text(encoding="utf-8")
    assert "LLM returned 429; retrying (1/3)" in trace
    assert "LLM returned 429; retrying (2/3)" in trace
    assert "LLM returned 429; retries exhausted (3/3): 429 rate limit" in trace
