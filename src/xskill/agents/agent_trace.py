"""
agents/agent_trace.py — 每次 agent 调用的逐轮 CoT / 工具调用 trace，按 traj/atom/skill
落独立文件
================================================================================

单一 ``agno.log`` 把所有 agent 的请求糊在一起，没法按"这条 traj 拆分时模型想了啥、
look 了哪、submit 了什么"排查。这里给每次 ``agent.run()`` 开一个**上下文 sink**
（线程本地），由工厂在 ``model.invoke`` 外层每轮把 ``reasoning_content`` / ``content``
/ ``tool_calls`` 流式 append 进去。``tail -f`` 就能实时看某条 traj 的拆分推理。

路径由调用方通过当前 XSkill 实例的 ``logs_dir`` 显式决定：
  task_agents/<traj_id>.log
  task_cluster_agents/<traj_id>/<atom_id>.log
  skill_edit_agents/skills/<skill>.log  (all SkillEdit turns append here)

线程模型：watcher 每条 agent 跑在线程池各自线程，``threading.local`` 让各线程的
sink 互不串（同一次 run 内的多轮 invoke 都在同一线程 → 同一文件）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_STATE = threading.local()
_WARNING_CACHE_LIMIT = 2048
_WARNED_FAILURES: OrderedDict[tuple[str, str], None] = OrderedDict()
_WARNING_LOCK = threading.Lock()
logger = logging.getLogger("xskill.agent_trace")


def _sink() -> "Path | None":
    return getattr(_STATE, "sink", None)


def _warn_io_failure(exc: OSError, sink: Path) -> None:
    """Emit one path-redacted warning for each sink/error-type pair."""
    sink_hash = hashlib.sha256(
        str(sink).encode("utf-8", errors="replace"),
    ).hexdigest()[:12]
    error_type = type(exc).__name__
    warning_key = (sink_hash, error_type)
    with _WARNING_LOCK:
        if warning_key in _WARNED_FAILURES:
            _WARNED_FAILURES.move_to_end(warning_key)
            return
        _WARNED_FAILURES[warning_key] = None
        if len(_WARNED_FAILURES) > _WARNING_CACHE_LIMIT:
            _WARNED_FAILURES.popitem(last=False)
    # Keep the message path- and exception-text-free. Logging does not call
    # ``record`` and therefore cannot recurse into the trace sink.
    logger.warning(
        "agent trace I/O failed error_type=%s sink_hash=%s",
        error_type,
        sink_hash,
    )


def set_sink(
    path,
    *,
    append: bool = False,
    spill_token_limit: int | None = None,
    compact_token_limit: int | None = None,
) -> None:
    """Set the current trace sink.

    Ordinary agents keep the historical truncate-on-run behaviour.  SkillEdit
    opts into ``append=True`` so every turn for one skill lands in one file.
    """
    if path is None:
        _STATE.sink = None
        _STATE.round = 0
        _STATE.spill_token_limit = None
        _STATE.compact_token_limit = None
        _STATE.seen_tool_results = set()
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            p.touch(exist_ok=True)
        else:
            p.write_text("", encoding="utf-8")
    except OSError as exc:
        _warn_io_failure(exc, p)
        _STATE.sink = None
        _STATE.round = 0
        return
    _STATE.sink = p
    _STATE.round = 0
    _STATE.spill_token_limit = spill_token_limit
    _STATE.compact_token_limit = compact_token_limit
    _STATE.seen_tool_results = set()


def clear_sink() -> None:
    _STATE.sink = None
    _STATE.round = 0
    _STATE.spill_token_limit = None
    _STATE.compact_token_limit = None
    _STATE.seen_tool_results = set()


class trace_to:
    """``with trace_to(path): agent.run(...)`` —— 这次 run 的每轮 LLM 交互写进 path。

    path 为 None → 在该作用域显式禁用 trace，退出后恢复外层 sink。
    """

    def __init__(
        self,
        path,
        *,
        append: bool = False,
        spill_token_limit: int | None = None,
        compact_token_limit: int | None = None,
    ):
        self.path = path
        self.append = append
        self.spill_token_limit = spill_token_limit
        self.compact_token_limit = compact_token_limit
        self._previous_sink: Path | None = None
        self._previous_round = 0
        self._previous_spill_token_limit: int | None = None
        self._previous_compact_token_limit: int | None = None
        self._previous_seen_tool_results: set[str] = set()

    def __enter__(self) -> "trace_to":
        self._previous_sink = _sink()
        self._previous_round = getattr(_STATE, "round", 0)
        self._previous_spill_token_limit = getattr(
            _STATE, "spill_token_limit", None,
        )
        self._previous_compact_token_limit = getattr(
            _STATE, "compact_token_limit", None,
        )
        self._previous_seen_tool_results = getattr(
            _STATE, "seen_tool_results", set(),
        )
        set_sink(
            self.path,
            append=self.append,
            spill_token_limit=self.spill_token_limit,
            compact_token_limit=self.compact_token_limit,
        )
        return self

    def __exit__(self, *exc) -> bool:
        # Direct restoration is intentional: calling set_sink here would
        # truncate an outer trace file when nested scopes unwind.
        _STATE.sink = self._previous_sink
        _STATE.round = self._previous_round
        _STATE.spill_token_limit = self._previous_spill_token_limit
        _STATE.compact_token_limit = self._previous_compact_token_limit
        _STATE.seen_tool_results = self._previous_seen_tool_results
        return False


def _content_len(m: Any) -> int:
    c = getattr(m, "content", None)
    if c is None and isinstance(m, dict):
        c = m.get("content")
    return len(c if isinstance(c, str) else str(c or ""))


def _extract(resp: Any):
    """从 agno / OpenAI 响应抽 (reasoning, content, [(tool_name, args), ...])。

    防御式：兼容 ``resp.choices[0].message`` 结构与直接挂在 resp 上的属性、
    以及 tool_call 的 dict / 对象两种形态。
    """
    msg: Any
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, KeyError, TypeError):
        msg = resp
    rc = getattr(msg, "reasoning_content", None)
    ct = getattr(msg, "content", None)
    tcs = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        if fn is None and isinstance(tc, dict):
            fn = tc.get("function")
        name = (getattr(fn, "name", None)
                or (fn.get("name") if isinstance(fn, dict) else "?"))
        args = (getattr(fn, "arguments", None)
                or (fn.get("arguments") if isinstance(fn, dict) else ""))
        tcs.append((name, args))
    return rc, ct, tcs


def _append(text: str) -> None:
    sink = _sink()
    if sink is None:
        return
    try:
        with open(sink, "a", encoding="utf-8") as trace_file:
            trace_file.write(text)
    except OSError as exc:
        _warn_io_failure(exc, sink)


def append_to(path: Path | str | None, text: str) -> None:
    """Append framework-owned turn markers outside an active model call."""
    if path is None:
        return
    sink = Path(path)
    try:
        sink.parent.mkdir(parents=True, exist_ok=True)
        with sink.open("a", encoding="utf-8") as trace_file:
            trace_file.write(text)
    except OSError as exc:
        _warn_io_failure(exc, sink)


def event(
    level: str,
    message: str,
    *,
    include_timestamp: bool = True,
) -> None:
    """Write a readable retry/context/checkpoint event to the active sink."""
    if _sink() is None:
        return
    prefix = (
        f"{time.strftime('%H:%M:%S')} {level.upper():<5} "
        if include_timestamp else ""
    )
    lines = str(message).splitlines() or [""]
    rendered = [prefix + lines[0]]
    rendered.extend(" " * len(prefix) + line for line in lines[1:])
    _append("\n".join(rendered) + "\n")


def _format_tokens(value: int | None) -> str:
    if value is None:
        return "off"
    if value >= 1000:
        rounded = value / 1000
        return f"{rounded:.1f}k".replace(".0k", "k")
    return str(value)


def _message_value(message: Any, name: str, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def _tool_result_lines(messages) -> list[str]:
    seen = getattr(_STATE, "seen_tool_results", set())
    out: list[str] = []
    for message in messages or []:
        if _message_value(message, "role") != "tool":
            continue
        content = str(_message_value(message, "content", "") or "")
        tool_name = str(
            _message_value(message, "tool_name")
            or _message_value(message, "name")
            or "tool"
        )
        tool_call_id = str(_message_value(message, "tool_call_id", "") or "")
        fingerprint = hashlib.sha256(
            f"{tool_call_id}\0{tool_name}\0{content}".encode(
                "utf-8", errors="replace",
            )
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if tool_name == "commit_baby":
            # commit_baby writes its authoritative multi-line result directly.
            continue
        out.append(f"TOOL RESULT  {tool_name}")
        stripped = content.strip()
        try:
            structured = json.loads(stripped) if stripped else None
        except (json.JSONDecodeError, TypeError):
            structured = None
        if structured is not None:
            summary = f"Returned structured result ({len(content):,} chars)."
        elif not stripped:
            summary = "Returned no text."
        elif len(stripped) <= 160 and "\n" not in stripped:
            summary = stripped
        else:
            summary = f"Returned {len(content):,} chars."
        out.append(f"             {summary}")
    _STATE.seen_tool_results = seen
    return out


def _format_argument_value(value: Any) -> str:
    if isinstance(value, str):
        flattened = " ".join(value.split())
        if len(flattened) > 100:
            return f"<{len(value):,} chars>"
        return flattened
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, (list, tuple)):
        if len(value) <= 4 and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            return "[" + ", ".join(_format_argument_value(item) for item in value) + "]"
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} fields>"
    return f"<{type(value).__name__}>"


def _format_tool_call(name: str, arguments: Any) -> str:
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            parsed = arguments
    if isinstance(parsed, dict):
        values = list(parsed.items())
        if len(values) == 1:
            rendered = _format_argument_value(values[0][1])
        else:
            rendered = ", ".join(
                f"{key}={_format_argument_value(value)}"
                for key, value in values
            )
    else:
        rendered = _format_argument_value(parsed)
    return f"TOOL  {name}({rendered})"


def begin_round(messages) -> None:
    """Start one outer model.invoke round before retries/context management."""
    if _sink() is None:
        return
    n = getattr(_STATE, "round", 0) + 1
    _STATE.round = n
    chars = sum(_content_len(m) for m in (messages or []))
    spill = _format_tokens(getattr(_STATE, "spill_token_limit", None))
    compact = _format_tokens(getattr(_STATE, "compact_token_limit", None))
    out = [
        "",
        (
            f"---- ROUND {n} | tokens={_format_tokens(chars // 4)} "
            f"| spill@{spill} | compact@{compact} ----"
        ),
        "",
        *_tool_result_lines(messages),
    ]
    _append("\n".join(out).rstrip() + "\n")


def record_response(resp) -> None:
    """Append reasoning, answer, and human-readable tool calls for one round."""
    if _sink() is None:
        return
    out: list[str] = []
    rc, ct, tcs = _extract(resp)
    if rc and str(rc).strip():
        out.extend(["THINK", str(rc).strip(), ""])
    if ct and str(ct).strip():
        out.extend(["SAY", str(ct).strip(), ""])
    for name, args in tcs:
        out.extend([_format_tool_call(name, args), ""])
    if out:
        _append("\n".join(out).rstrip() + "\n")


def record(messages, resp) -> None:
    """Backward-compatible one-shot record used by tests and direct callers."""
    begin_round(messages)
    record_response(resp)
