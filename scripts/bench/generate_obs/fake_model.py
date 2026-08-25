"""dry-run 用的假模型：不打网络，但把 agno 的工具循环真的跑一遍。

为什么不直接跳过 ``agent.run``：要验的恰恰是埋点挂在真实链路上有没有数
对——``tool_hooks`` 有没有被 agno 调、``context.compact`` 会不会触发、
features.json 的数字对不对。跳过 agent.run 什么都验不到。

做法是只把最底层的 HTTP 那一层换掉：继承 agno 的 ``OpenAIChat``，只改
``get_client()``，返回一个 ``chat.completions.create`` 吐预制响应的替身。
agno 自己的请求组装、响应解析、工具分发、以及 xskill 的 rate_limit /
context_mgmt / retry / trace / otel 包装全部照原样跑。

剧本：先 list_files 摸目录，再逐条 read_file 读轨迹（条数由
``--fake-reads`` 决定，读得够多就会把上下文顶过 compact 阈值，真触发一次
压缩），然后 new_skill_folder、write_file、commit_generate_main，最后给
一句收尾。压缩请求单独认出来，回一段摘要文本。
"""
from __future__ import annotations

import itertools
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_TOOL_CALL_SEQ = itertools.count(1)


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "id": f"call_fake_{next(_TOOL_CALL_SEQ):04d}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class FakeScript:
    """决定每一轮回什么。与模型无关，方便单独测。"""

    # compact 提示词里的固定标记，用来认出"这是压缩请求不是正常一轮"
    COMPACT_MARKER = "CONTEXT CHECKPOINT COMPACTION"

    def __init__(
        self,
        *,
        sessions_dir: Path,
        skill_name: str,
        reads: int,
        read_limit: int = 400,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.skill_name = skill_name
        self.reads = max(0, int(reads))
        self.read_limit = read_limit
        # 大的排前面：mock 目录里中位数只有 6KB、最大 755KB，按文件名顺序
        # 读几十条也顶不到 compact 阈值，验不到压缩那条路。
        self.traj_files = sorted(
            self.sessions_dir.glob("traj_*.md"),
            key=lambda path: -path.stat().st_size,
        )
        self.round = 0
        self.compact_calls = 0

    # ── 剧本 ────────────────────────────────────────────────────

    def is_compact_request(self, messages: list) -> bool:
        for msg in reversed(messages or []):
            content = _message_text(msg)
            if content and self.COMPACT_MARKER in content:
                return True
        return False

    def next_step(self) -> tuple[str, list[dict]]:
        """返回 (文本, tool_calls)。tool_calls 空表示这轮收尾。"""
        self.round += 1
        step = self.round

        if step == 1:
            return "先看目录里有哪些轨迹。", [
                _tool_call("list_files", {"path": str(self.sessions_dir)}),
            ]

        read_index = step - 2
        if read_index < self.reads and self.traj_files:
            target = self.traj_files[read_index % len(self.traj_files)]
            return f"读第 {read_index + 1} 条轨迹。", [
                _tool_call("read_file", {
                    "path": str(target),
                    "offset": 1,
                    "limit": self.read_limit,
                }),
            ]

        after_reads = read_index - self.reads
        if after_reads == 0:
            return "材料够了，建 skill 目录。", [
                _tool_call("new_skill_folder", {
                    "skill_name": self.skill_name,
                    "description": "dry-run 产物，不是真 skill",
                }),
            ]
        if after_reads == 1:
            # frontmatter 是产品硬校验的（skill/frontmatter.py），缺了
            # write_file 会拒写——dry-run 也得按规矩来。
            body = (
                "---\n"
                f"name: {self.skill_name}\n"
                "description: generate 行为观测实验的 dry-run 产物，"
                "不含真实经验，只用于验证工具链与埋点。\n"
                "---\n\n"
                f"# {self.skill_name}\n\n"
                "这是 dry-run 写出来的占位 skill。\n"
            )
            return "写 SKILL.md。", [
                _tool_call("write_file", {
                    "path": f"{self.skill_name}/SKILL.md",
                    "content": body,
                }),
            ]
        if after_reads == 2:
            return "提交到 main。", [
                _tool_call("commit_generate_main", {
                    "skill_name": self.skill_name,
                    "message": "dry-run: generate 行为观测冒烟",
                }),
            ]
        return f"dry-run 结束，共 {self.round} 轮。", []

    def compact_answer(self) -> str:
        self.compact_calls += 1
        return (
            "## Model handoff summary\n\n"
            f"（假模型的第 {self.compact_calls} 次压缩）已读若干条 Cursor "
            "轨迹，尚未写出 skill。下一步：建目录、写 SKILL.md、提交。\n"
        )


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


# ── 假 HTTP 层 ──────────────────────────────────────────────────


def _completion(
    *,
    model: str,
    text: str,
    tool_calls: list[dict] | None,
    prompt_tokens: int,
) -> Any:
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall,
        Function,
    )
    from openai.types.completion_usage import CompletionUsage

    calls = None
    if tool_calls:
        calls = [
            ChatCompletionMessageToolCall(
                id=call["id"],
                type="function",
                function=Function(
                    name=call["function"]["name"],
                    arguments=call["function"]["arguments"],
                ),
            )
            for call in tool_calls
        ]
    message = ChatCompletionMessage(
        role="assistant", content=text or None, tool_calls=calls,
    )
    return ChatCompletion(
        id="chatcmpl-fake",
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[
            Choice(
                index=0,
                message=message,
                finish_reason="tool_calls" if calls else "stop",
            )
        ],
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=max(1, len(text) // 4),
            total_tokens=prompt_tokens + max(1, len(text) // 4),
        ),
    )


def _stream_chunks(*, model: str, text: str) -> Iterator[Any]:
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    # 按行切片，模拟流式：compact 走的是 invoke_stream。
    pieces = [line + "\n" for line in text.splitlines()] or [text]
    for index, piece in enumerate(pieces):
        yield ChatCompletionChunk(
            id="chatcmpl-fake-stream",
            object="chat.completion.chunk",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    delta=ChoiceDelta(role="assistant", content=piece),
                    finish_reason=None,
                )
            ],
        )
        del index
    yield ChatCompletionChunk(
        id="chatcmpl-fake-stream",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[
            Choice(index=0, delta=ChoiceDelta(), finish_reason="stop")
        ],
    )


class _FakeCompletions:
    def __init__(self, script: FakeScript, model: str):
        self._script = script
        self._model = model

    def create(self, **kwargs):  # noqa: ANN003 — 冒充 openai SDK
        messages = kwargs.get("messages") or []
        prompt_tokens = sum(
            len(str(_message_text(m))) for m in messages
        ) // 4
        if self._script.is_compact_request(messages):
            text = self._script.compact_answer()
            if kwargs.get("stream"):
                return _stream_chunks(model=self._model, text=text)
            return _completion(
                model=self._model,
                text=text,
                tool_calls=None,
                prompt_tokens=prompt_tokens,
            )
        text, tool_calls = self._script.next_step()
        if kwargs.get("stream"):
            return _stream_chunks(model=self._model, text=text)
        return _completion(
            model=self._model,
            text=text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
        )


class _FakeClient:
    def __init__(self, script: FakeScript, model: str):
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeCompletions(script, model)


@contextmanager
def fake_openai_client(script: FakeScript, model_id: str) -> Iterator[None]:
    """在作用域内把 agno 的 OpenAI HTTP client 换成替身。

    只补 ``OpenAIChat.get_client`` 这一个方法：agno 里只有它定义了这个
    方法，``DeepSeek`` / ``OpenAILike`` 都是继承，所以不管 base_url 路由到
    哪个子类都会用上替身。除此之外——工厂、限流、上下文管理、重试、trace、
    otel、工具分发——全是产品原路。
    """
    from agno.models.openai.chat import OpenAIChat

    original = OpenAIChat.get_client

    def get_client(self):  # noqa: ANN001, ANN202
        del self
        return _FakeClient(script, model_id)

    OpenAIChat.get_client = get_client
    try:
        yield
    finally:
        OpenAIChat.get_client = original
