"""compact 插入的 assistant 摘要必须带 reasoning_content，避免 DeepSeek thinking 400。"""
from __future__ import annotations

from types import SimpleNamespace

from xskill.agents.context_budget import (
    _COMPACT_MARK,
    _compact_history_in_place,
    _msg_reasoning_str,
    _new_summary_message,
)


def test_summary_message_has_reasoning_content() -> None:
    msg = _new_summary_message({"role": "system", "content": "sys"}, "hello")
    assert msg["role"] == "assistant"
    assert _msg_reasoning_str(msg)


def test_compact_history_assistant_messages_keep_reasoning() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": "old think",
            "reasoning_content": "original-reason",
        },
        {"role": "user", "content": "more"},
        {"role": "assistant", "content": "later", "reasoning_content": "later-reason"},
        {"role": "user", "content": "now"},
        {"role": "assistant", "content": "tail", "reasoning_content": "tail-reason"},
    ]

    ok = _compact_history_in_place(
        messages,
        compact_fn=lambda _prompt: "short memory",
        keep_recent_messages=2,
    )
    assert ok
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert assistants
    assert any(_COMPACT_MARK in (m.get("content") or "") for m in assistants)
    for msg in assistants:
        assert _msg_reasoning_str(msg), msg


def test_compact_fills_object_assistant_without_reasoning() -> None:
    messages = [
        SimpleNamespace(role="system", content="sys"),
        SimpleNamespace(role="user", content="start"),
        SimpleNamespace(role="assistant", content="old", reasoning_content="r1"),
        SimpleNamespace(role="user", content="now"),
        SimpleNamespace(role="assistant", content="tail", reasoning_content="r2"),
    ]
    ok = _compact_history_in_place(
        messages,
        compact_fn=lambda _prompt: "short memory",
        keep_recent_messages=2,
    )
    assert ok
    for msg in messages:
        if getattr(msg, "role", None) == "assistant":
            assert _msg_reasoning_str(msg)
