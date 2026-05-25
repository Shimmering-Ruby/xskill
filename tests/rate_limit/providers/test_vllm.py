"""vLLM 自部署 API 在限流改造下的兼容性测试。

vLLM 特征(相对 OpenAI 标准):
  - usage 字段经常缺失或为 null —— 取决于版本和 --disable-log-stats 等启动 flag
  - 不会主动 429(自部署无限流),但可能 503/500(OOM、模型加载失败等)
  - chat template 偏离标准时,content 可能是 list 而非 string

本文件验证: 限流 wrapper 在 usage 缺失/null/partial 时正常工作不抛错。
这是限流器对自部署 vLLM 用户体验的关键保证 —— xskill 不能因为 usage
字段不规范就崩。
"""
from __future__ import annotations

from tests._fake_llm_server import Responder
from tests.rate_limit.providers._shared import make_responder
from xskill.utils.llm import LLMClient
from xskill.utils.rate_limit import get_or_create_bucket


# ════════════════════════════════════════════════════════════
# Fixture data —— vLLM 常见形态
# ════════════════════════════════════════════════════════════

CHAT_NO_USAGE = {
    "id": "cmpl-vllm-001",
    "object": "chat.completion",
    "model": "Qwen2.5-7B-Instruct",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "你好。"},
        "finish_reason": "stop",
    }],
    # vLLM 老版本 / 部分配置下不返 usage
}

CHAT_USAGE_NULL = {
    "id": "cmpl-vllm-002",
    "object": "chat.completion",
    "model": "Qwen2.5-7B-Instruct",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "ok"},
        "finish_reason": "stop",
    }],
    "usage": None,  # 部分代理把 usage 设为 None
}

CHAT_USAGE_PARTIAL = {
    "id": "cmpl-vllm-003",
    "model": "Qwen2.5-7B-Instruct",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "好的"},
        "finish_reason": "stop",
    }],
    "usage": {
        # 缺 total_tokens 字段,只有 prompt
        "prompt_tokens": 50,
    },
}


# ════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════

def test_vllm_no_usage_field_falls_back_gracefully(fake_server):
    """usage 完全不在 response,桶仅按估算扣量,不抛错。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_NO_USAGE)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "Qwen2.5-7B-Instruct",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    result = client.chat("你好世界")
    assert result == "你好。"

    bucket = get_or_create_bucket(fake_server.base_url)
    # 估算扣了一些(< 500),没崩
    assert bucket._tpm_tokens < 500


def test_vllm_usage_null_treated_as_missing(fake_server):
    """usage: null 应当作字段缺失,fallback 到估算。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_USAGE_NULL)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "Qwen2.5-7B-Instruct",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    client.chat("hi")  # 不应抛错


def test_vllm_partial_usage_treated_as_missing(fake_server):
    """usage 只有 prompt_tokens 没 total_tokens —— total 缺失即视为不可校准。"""
    fake_server.add_responder(
        "/chat/completions",
        Responder(name="ok", match=lambda b: True, build=make_responder(CHAT_USAGE_PARTIAL)),
    )
    cfg = {
        "base_url": fake_server.base_url,
        "model": "Qwen2.5-7B-Instruct",
        "api_key": "test",
        "rate_limit": {"rpm": 60, "tpm": 1000, "burst": 500},
    }
    client = LLMClient.from_config(cfg)
    client.chat("hi")  # 不应抛错
    # 桶被估算扣了一些
    bucket = get_or_create_bucket(fake_server.base_url)
    assert bucket._tpm_tokens < 500
