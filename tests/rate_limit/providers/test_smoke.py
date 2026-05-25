"""FakeLLMServer + RateLimitResponder + _shared 链路冒烟测试。"""
from __future__ import annotations

import httpx

from tests._fake_llm_server import RateLimitResponder, Responder
from tests.rate_limit.providers._shared import make_responder


def test_rate_limit_responder_returns_429_after_quota(fake_server):
    inner = Responder(
        name="echo",
        match=lambda b: True,
        build=make_responder({"choices": [{"message": {"content": "ok"}}]}),
    )
    fake_server.add_responder(
        "/chat/completions",
        RateLimitResponder(name="rl", inner=inner, rpm=2, over_limit_mode="http_429"),
    )

    url = f"{fake_server.base_url}/chat/completions"
    # 前 2 个 200,第 3 个 429
    assert httpx.post(url, json={}).status_code == 200
    assert httpx.post(url, json={}).status_code == 200
    r = httpx.post(url, json={})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "2"
