"""test_usage.py —— LLM token/成本统计核心逻辑(Issue #43)"""
from __future__ import annotations

import pytest

from xskill import usage as U
from xskill.usage import (
    ModelPrice, PriceTable, Usage, UsageLedger, cost_usd, extract_usage,
    load_price_table,
)


# ── extract_usage ────────────────────────────────────────────────
class _Obj:
    def __init__(self, **kw):
        self.usage = type("u", (), kw)()


def test_extract_usage_dict_with_cache():
    resp = {"usage": {"prompt_tokens": 1100, "completion_tokens": 140,
                      "total_tokens": 1240, "prompt_cache_hit_tokens": 900,
                      "prompt_cache_miss_tokens": 200}}
    u = extract_usage(resp)
    assert (u.prompt, u.completion, u.total, u.cache_hit, u.cache_miss) == (1100, 140, 1240, 900, 200)


def test_extract_usage_sdk_object_total_derived():
    u = extract_usage(_Obj(prompt_tokens=10, completion_tokens=5))
    assert u.total == 15  # total_tokens 缺失 → prompt+completion


def test_extract_usage_missing_usage():
    assert extract_usage({"choices": []}) == Usage()
    assert extract_usage(None) == Usage()


# ── PriceTable 解析优先级 ────────────────────────────────────────
def _table():
    vendored = {"deepseek-v4-flash": {"input_per_1m": 0.14, "output_per_1m": 0.28,
                                      "cache_hit_per_1m": 0.014},
                "text-embedding-v4": {"embed_per_1m": 0.02}}
    cfg = {"default": {"input_per_1m": 2.0, "output_per_1m": 6.0},
           "deepseek-v4-flash": {"input_per_1m": 0.10, "output_per_1m": 0.20}}
    return PriceTable(vendored, cfg)


def test_resolve_config_overrides_static():
    p, src = _table().resolve("deepseek-v4-flash")
    assert src == "config" and p.input_per_1m == 0.10


def test_resolve_static_then_default():
    t = PriceTable({"text-embedding-v4": {"embed_per_1m": 0.02}}, None)
    p, src = t.resolve("text-embedding-v4")
    assert src == "static" and p.embed_per_1m == 0.02
    p2, src2 = t.resolve("never-heard-of-it")
    assert src2 == "default" and p2.input_per_1m == U._FALLBACK_DEFAULT["input_per_1m"]


# ── cost_usd ─────────────────────────────────────────────────────
def test_cost_plain_prompt_completion():
    price = ModelPrice(input_per_1m=1.0, output_per_1m=2.0)
    c = cost_usd(Usage(prompt=1_000_000, completion=500_000), price)
    assert c == pytest.approx(1.0 + 1.0)  # 1M×$1 + 0.5M×$2


def test_cost_cache_split_pricing():
    price = ModelPrice(input_per_1m=1.0, output_per_1m=0.0,
                       cache_hit_per_1m=0.1, cache_miss_per_1m=1.0)
    # 全是 cache hit → 便宜十倍
    c = cost_usd(Usage(prompt=1_000_000, cache_hit=1_000_000, cache_miss=0), price)
    assert c == pytest.approx(0.1)


def test_cost_embedding_uses_embed_price():
    price = ModelPrice(input_per_1m=99.0, output_per_1m=99.0, embed_per_1m=0.02)
    c = cost_usd(Usage(total=1_000_000), price, is_embed=True)
    assert c == pytest.approx(0.02)


# ── UsageLedger ──────────────────────────────────────────────────
def test_ledger_accumulates_and_flags_estimated(monkeypatch):
    monkeypatch.setattr(U, "_persist", lambda *a, **k: None)  # 不碰真实 registry
    led = UsageLedger(_table())
    led.record_llm("atom_split", "deepseek-v4-flash",
                   {"usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                              "total_tokens": 1200}})
    led.record_embed("text-embedding-v4", {"usage": {"total_tokens": 800}})
    snap = led.snapshot()
    assert snap["total_calls"] == 2
    assert snap["total_tokens"] == 2000
    assert snap["by_step"]["atom_split"]["calls"] == 1
    assert snap["by_step"]["embedding"]["tokens"] == 800
    # embedding 命中 static 价 → estimated=True
    assert snap["estimated"] is True
    assert snap["total_usd"] > 0


def test_ledger_record_never_raises(monkeypatch):
    monkeypatch.setattr(U, "_persist", lambda *a, **k: None)
    led = UsageLedger(_table())
    led.record_llm("x", "m", object())      # 垃圾 resp,不应抛
    led.record_llm("x", "m", None)
    assert led.snapshot()["total_calls"] == 2  # 仍记账(token=0)


# ── vendored 表加载 ──────────────────────────────────────────────
def test_load_vendored_price_table():
    t = load_price_table(None)
    p, src = t.resolve("deepseek-v4-flash")
    assert src == "static" and p.input_per_1m > 0
