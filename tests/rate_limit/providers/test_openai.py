"""OpenAI 官方 API 在限流改造下的兼容性测试。

OpenAI 标准形态(也是大多数 OpenAI 兼容 endpoint 的基线):
- response.choices[0].message.content
- response.usage = {prompt_tokens, completion_tokens, total_tokens}
- 429 响应: HTTP 429 status + Retry-After header + JSON body
"""
from __future__ import annotations

import pytest

from tests._fake_llm_server import RateLimitResponder, Responder
from tests.rate_limit.providers._shared import make_responder
from xskill.utils.llm import LLMClient
from xskill.utils.rate_limit import get_or_create_bucket


# ════════════════════════════════════════════════════════════
# Fixture data —— OpenAI 真实抓包形态
# ════════════════════════════════════════════════════════════

CHAT_WITH_USAGE = {
    "id": "chatcmpl-openai-001",
    "object": "chat.completion",
    "model": "gpt-4o-mini",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "Hello, world."},
        "finish_reason": "stop",
    }],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    },
}

CHAT_NO_USAGE = {
    "id": "chatcmpl-openai-002",
    "object": "chat.completion",
    "model": "gpt-4o-mini",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "ok"},
        "finish_reason": "stop",
    }],
    # 显式不带 usage —— 模拟代理层 strip 字段
}


# ════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════

def test_openai_usage_total_tokens_reconciles_bucket(fake_server):
    """response.usage.total_tokens=17,桶应被准确扣 17(而非估算的 ~3)。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_WITH_USAGE)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "gpt-4o-mini",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    client.chat("hi")

    bucket = get_or_create_bucket(fake_server.base_url)
    # burst=500 起步,扣 17 后应 ≈ 483
    assert 480 <= bucket._tpm_tokens <= 485


def test_openai_no_usage_falls_back_to_estimate(fake_server):
    """response 无 usage 字段时,桶按估算扣量不抛错。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_NO_USAGE)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "gpt-4o-mini",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    result = client.chat("hello world this is a test prompt")
    assert result == "ok"

    bucket = get_or_create_bucket(fake_server.base_url)
    # 桶被估算扣减(< 500),没崩
    assert bucket._tpm_tokens < 500


def test_openai_429_response_propagates_as_error(fake_server):
    """server 返 429 时,openai SDK 把它包成 RateLimitError 抛出来。"""
    fake_server.add_responder(
        "/chat/completions",
        RateLimitResponder(
            name="rl",
            inner=Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_WITH_USAGE)),
            rpm=1, over_limit_mode="http_429",
        ),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "gpt-4o-mini",
        "api_key": "test",
        # 不配 rate_limit —— 本测试看的是 server 端 429 透传
    }
    client = LLMClient.from_config(cfg)
    client.chat("hi")  # 第 1 个 OK
    with pytest.raises(Exception) as exc_info:
        client.chat("hi")  # 第 2 个超过 server 限额 → 429
    # openai SDK 把 429 包成 RateLimitError
    msg = str(exc_info.value).lower()
    assert "429" in str(exc_info.value) or "rate" in msg or "limit" in msg
