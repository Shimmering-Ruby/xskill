"""providers/ 共用工具 —— 只放真共用的纯函数,不放 fixture data。

每家 API 形态的 fixture 数据放在各自的 test_<provider>.py 顶部作为
模块级常量,读一个文件就有完整 provider 知识。
"""
from __future__ import annotations

from typing import Callable


def make_responder(body: dict) -> Callable[[dict], dict]:
    """构造 FakeLLMServer Responder.build 用的 callable —— 不管 request 总返同一个 body。

    用法:
        from tests._fake_llm_server import Responder
        from tests.rate_limit.providers._shared import make_responder
        fake_server.add_responder("/chat/completions", Responder(
            name="ok", match=lambda b: True, build=make_responder(SOME_FIXTURE)
        ))
    """
    def _build(request_body: dict) -> dict:
        # 浅 copy,防止 server 端 __status / __headers 弹出污染原 fixture
        return dict(body)
    return _build


def make_streaming_responder(sse_body: str) -> Callable[[dict], dict]:
    """构造 streaming responder —— 返 SSE 字符串字面量。
    依赖 FakeLLMServer._render_payload 识别 __stream 标记。
    """
    def _build(request_body: dict) -> dict:
        return {"__stream": True, "__sse_body": sse_body}
    return _build
