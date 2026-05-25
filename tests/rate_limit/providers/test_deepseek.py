"""DeepSeek 直连 API 在限流改造下的兼容性 + 回归测试。

DeepSeek 直连相对 OpenAI 标准多三件事:
  1. response.choices[].message.reasoning_content 字段(thinking 模型独有)
  2. usage.completion_tokens_details.reasoning_tokens 单独统计
  3. multi-turn 必须把 prior reasoning_content 原样回传,否则 400

本文件验证:
  - 限流 wrapper 正确把 total_tokens(含 reasoning)reconcile 进桶
  - 限流 wrapper 没破坏 reasoning_content 回传 —— 这是 build_chat_model
    monkey-patch 容易踩到的回归点
"""
from __future__ import annotations

import pytest

from tests._fake_llm_server import Responder
from tests.rate_limit.providers._shared import make_responder
from xskill.utils.llm import LLMClient
from xskill.utils.rate_limit import get_or_create_bucket


# ════════════════════════════════════════════════════════════
# Fixture data —— DeepSeek 真实抓包形态
# ════════════════════════════════════════════════════════════

CHAT_WITH_REASONING = {
    "id": "chatcmpl-deepseek-001",
    "object": "chat.completion",
    "model": "deepseek-v4-flash",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "答案是 42",
            "reasoning_content": "根据银河系漫游指南,42 是终极答案。",
        },
        "finish_reason": "stop",
    }],
    "usage": {
        "prompt_tokens": 120,
        "completion_tokens": 80,
        "total_tokens": 200,
        "completion_tokens_details": {"reasoning_tokens": 60},
    },
}

# multi-turn 第二轮的 fixed response —— 用来观察 client 发的请求 body
SECOND_TURN_RESPONSE = {
    "id": "chatcmpl-deepseek-002",
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "因为 43 不是答案。",
            "reasoning_content": "对照原书第 27 章...",
        },
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 220, "completion_tokens": 30, "total_tokens": 250},
}


# ════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════

def test_deepseek_total_tokens_includes_reasoning_in_reconcile(fake_server):
    """reconcile 必须按 total_tokens=200(含 reasoning),不能只按 completion=80。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_WITH_REASONING)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "deepseek-v4-flash",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    client.chat("什么是终极答案")

    bucket = get_or_create_bucket(fake_server.base_url)
    # 桶 burst=500,扣 200(含 reasoning) → 应 ≈ 300
    assert 295 <= bucket._tpm_tokens <= 305


def test_deepseek_wrapped_model_preserves_deepseek_class(fake_server):
    """build_chat_model 加 rate_limit 包装后,DeepSeek 直连仍然走 DeepSeek 子类。

    DeepSeek 子类的存在意义就是处理 reasoning_content 多轮回传 —— 如果 wrap
    破坏了类型,multi-turn 一定崩。锁住这条不变式。
    """
    pytest.importorskip("agno")
    from agno.models.deepseek import DeepSeek
    from xskill.agents.agno_factory import build_chat_model

    llm_cfg = {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 10_000, "burst": 1000},
    }
    model = build_chat_model(llm_cfg)
    # 仍是 DeepSeek 子类实例(只是 invoke 被 monkey-patch)
    assert isinstance(model, DeepSeek), (
        f"rate_limit wrap 破坏了类型,变成 {type(model).__name__},"
        f"DeepSeek 子类的 _format_message 不再生效"
    )


def test_deepseek_wrap_does_not_mutate_messages_passed_to_invoke(fake_server):
    """限流 wrapper 不能改 messages 列表 —— 任何对 reasoning_content 的修改
    都会让 DeepSeek 多轮 400。用 mock model 隔离 agno,只看 wrap 本身行为。
    """
    from unittest.mock import MagicMock

    from xskill.agents.agno_factory import _wrap_with_rate_limit

    captured_messages = []
    inner_model = MagicMock()
    inner_model.invoke = MagicMock(
        side_effect=lambda messages, **kw: captured_messages.append(messages) or {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 50},
        }
    )

    wrapped = _wrap_with_rate_limit(
        inner_model,
        {"base_url": "https://test.example.com", "rate_limit": {"rpm": 60, "tpm": 1000}},
    )

    # 构造一组 messages,assistant 那条带 reasoning_content
    original_msgs = [
        type("Msg", (), {"content": "q1", "role": "user"})(),
        type("Msg", (), {
            "content": "a1", "role": "assistant", "reasoning_content": "think1"
        })(),
        type("Msg", (), {"content": "q2", "role": "user"})(),
    ]
    wrapped.invoke(original_msgs)

    # inner.invoke 收到的 messages 必须和传入对象身份相同(未被 wrapper 拷贝/重构)
    assert captured_messages, "inner.invoke 没被调用"
    assert captured_messages[0] is original_msgs, (
        "rate_limit wrap 把 messages 复制/重构了,会丢失 reasoning_content"
    )
    # 每条 message 对象身份不变
    for orig, passed in zip(original_msgs, captured_messages[0]):
        assert orig is passed, "messages 列表元素被 wrap 替换"
