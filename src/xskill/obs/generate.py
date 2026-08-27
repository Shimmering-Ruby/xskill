"""GenerateAgent 专用观测：工具 hook、模型包装、compact 旁路。

只给 ``xskill generate`` 用。SkillEdit / TaskAgent 不走这里，共用的
``agno_factory`` 和 compact 默认路径也不挂 OpenTelemetry。

``llm.invoke`` 上除了 ``input.value`` / ``output.value`` 这两份人读的预览，
还按 OpenInference 约定摊一份结构化的消息列表（``llm.input_messages.{i}.*``、
``llm.output_messages.0.*``）。Phoenix 的对话卡片、tool call 卡片、Thinking
区块都只认后者，光有预览字符串它就退回贴一坨纯文本。
"""
from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, NamedTuple

from xskill import obs

_OPEN_LLM: contextvars.ContextVar[tuple[Any, Any] | None] = contextvars.ContextVar(
    "xskill_open_llm", default=None,
)

_IN_MSG = "llm.input_messages"
_OUT_MSG = "llm.output_messages"
_MSG_CONTENT_MAX = 4000

# llm_cfg 里混着 api_key，摊进 llm.invocation_parameters 之前按名字挡掉。
_SECRET_HINTS = ("key", "secret", "password", "credential", "cookie", "auth")
_INVOCATION_DROP = ("base_url", "_pool_weights")

# model 名 → OpenInference 的 llm.provider 取值。Phoenix 拿它挑图标和价目表。
_PROVIDER_HINTS = (
    ("deepseek", "deepseek"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("grok", "xai"),
    ("kimi", "moonshot"),
    ("moonshot", "moonshot"),
    ("mistral", "mistralai"),
    ("sonar", "perplexity"),
    ("gpt", "openai"),
)


def _message_text(content: Any) -> str:
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                texts.append(str(item["text"]))
            else:
                texts.append(str(item))
        return "\n".join(texts)
    return str(content or "")


def _clip_from_end(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - 48)
    return f"[head omitted, {len(text):,} chars]\n{text[-keep:]}"


def _messages_preview(messages, limit: int = 4000) -> str:
    """给 Phoenix 看本轮真正进模型的新内容：省略 system 正文，从对话尾部截。"""
    parts: list[str] = []
    for message in messages or []:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        parts.append(f"{role or '?'}: {_message_text(content)}")
    total_chars = sum(len(part) for part in parts)
    header_lines = [f"messages={len(parts)} total_chars={total_chars}"]
    rest = parts
    if parts and parts[0].startswith("system:"):
        header_lines.append(f"system: <{len(parts[0])} chars, omitted>")
        rest = parts[1:]
    header = "\n".join(header_lines) + "\n\n"
    budget = max(200, limit - len(header))
    taken_rev: list[str] = []
    used = 0
    omitted = 0
    for part in reversed(rest):
        extra = len(part) + (2 if taken_rev else 0)
        if not taken_rev and extra > budget:
            taken_rev.append(_clip_from_end(part, budget))
            omitted = len(rest) - 1
            break
        if taken_rev and used + extra > budget:
            omitted = len(rest) - len(taken_rev)
            break
        taken_rev.append(part)
        used += extra
    taken = list(reversed(taken_rev))
    body = "\n\n".join(taken)
    if omitted:
        body = f"...[{omitted} earlier messages omitted]\n\n{body}"
    return header + body


def _field(obj, key: str) -> Any:
    """dict 和对象两种形态都读得到同一个字段。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _tool_call_parts(call) -> tuple[str, str, str]:
    """一条 tool call → ``(id, name, arguments_json)``。"""
    call_id = str(_field(call, "id") or "")
    fn = _field(call, "function")
    name = str(_field(fn, "name") or _field(call, "name") or "")
    args = _field(fn, "arguments") or _field(call, "arguments") or ""
    if isinstance(args, dict):
        args = json.dumps(args, ensure_ascii=False)
    return call_id, name or "?", str(args or "")


def _tool_call_list(obj) -> list:
    raw = _field(obj, "tool_calls")
    return list(raw) if raw else []


def _iter_tool_calls(resp) -> Iterator[tuple[str, str]]:
    for call in _tool_call_list(resp):
        _call_id, name, args = _tool_call_parts(call)
        yield name, args


def _response_preview(resp) -> str:
    chunks: list[str] = []
    for name, args in _iter_tool_calls(resp):
        shown = args if len(args) <= 240 else f"<{len(args)} chars>"
        chunks.append(f"tool_call {name}({shown})")
    reasoning = str(_field(resp, "reasoning_content") or "")
    if reasoning:
        chunks.append(f"thinking: {reasoning}")
    text = _message_text(_field(resp, "content"))
    if text:
        chunks.append(text)
    return "\n".join(chunks) or "(empty)"


def _response_has_tool_calls(resp) -> bool:
    return next(_iter_tool_calls(resp), None) is not None


# ── OpenInference 消息属性 ──────────────────────────────────────


class _Msg(NamedTuple):
    role: str
    content: str
    tool_calls: list
    tool_call_id: str


def _as_msg(message) -> _Msg:
    return _Msg(
        role=str(_field(message, "role") or "user"),
        content=_message_text(_field(message, "content")),
        tool_calls=_tool_call_list(message),
        tool_call_id=str(_field(message, "tool_call_id") or ""),
    )


def _tool_call_attributes(base: str, calls) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for index, call in enumerate(calls or []):
        call_id, name, args = _tool_call_parts(call)
        prefix = f"{base}.tool_calls.{index}.tool_call"
        if call_id:
            attributes[f"{prefix}.id"] = call_id
        attributes[f"{prefix}.function.name"] = name
        attributes[f"{prefix}.function.arguments"] = obs.clip(
            args, _MSG_CONTENT_MAX,
        )
    return attributes


def _one_message_attributes(base: str, msg: _Msg) -> dict[str, Any]:
    attributes: dict[str, Any] = {f"{base}.role": msg.role}
    if msg.content:
        attributes[f"{base}.content"] = obs.clip(msg.content, _MSG_CONTENT_MAX)
    if msg.tool_call_id:
        attributes[f"{base}.tool_call_id"] = msg.tool_call_id
    attributes.update(_tool_call_attributes(base, msg.tool_calls))
    return attributes


def _budgeted_messages(messages) -> tuple[list[_Msg], int]:
    """按预算挑要摊平的消息：system 单独留位，其余从最近的往前收。

    丢中间是有代价的——摊平数组要求下标连续，被丢的那几条在 Phoenix 上
    直接消失，所以额外记一个 ``xskill.input_messages_omitted``。
    """
    rows = [_as_msg(message) for message in messages or []]
    if not rows:
        return [], 0
    budget = obs.msg_budget()
    head: list[_Msg] = []
    rest = rows
    if rows[0].role == "system":
        head = [rows[0]]
        rest = rows[1:]
        budget -= min(len(rows[0].content), _MSG_CONTENT_MAX)
    taken_rev: list[_Msg] = []
    used = 0
    for msg in reversed(rest):
        cost = min(len(msg.content), _MSG_CONTENT_MAX)
        if taken_rev and used + cost > budget:
            break
        taken_rev.append(msg)
        used += cost
    kept = head + list(reversed(taken_rev))
    return kept, len(rows) - len(kept)


def _input_message_attributes(messages) -> dict[str, Any]:
    kept, omitted = _budgeted_messages(messages)
    attributes: dict[str, Any] = {}
    for index, msg in enumerate(kept):
        attributes.update(
            _one_message_attributes(f"{_IN_MSG}.{index}.message", msg)
        )
    if omitted:
        attributes["xskill.input_messages_omitted"] = omitted
    return attributes


def _output_message_attributes(resp) -> dict[str, Any]:
    base = f"{_OUT_MSG}.0.message"
    content = _message_text(_field(resp, "content"))
    reasoning = str(_field(resp, "reasoning_content") or "")
    attributes: dict[str, Any] = {
        f"{base}.role": str(_field(resp, "role") or "assistant"),
    }
    if content:
        attributes[f"{base}.content"] = obs.clip(content, _MSG_CONTENT_MAX)
    if reasoning:
        # 思维链只摆进 contents，正文留给 message.content。两边都摆的话
        # Phoenix 的消息卡会把正文渲染两遍。
        attributes.update({
            f"{base}.contents.0.message_content.type": "reasoning",
            f"{base}.contents.0.message_content.text": obs.clip(
                reasoning, _MSG_CONTENT_MAX,
            ),
        })
    attributes.update(_tool_call_attributes(base, _tool_call_list(resp)))
    return attributes


def _provider_of(model_name: str) -> str:
    lowered = (model_name or "").lower()
    for hint, provider in _PROVIDER_HINTS:
        if hint in lowered:
            return provider
    return ""


def _invocation_parameters(llm_cfg: dict, kwargs: dict) -> str:
    params: dict[str, Any] = {}
    for key, value in {**(llm_cfg or {}), **(kwargs or {})}.items():
        name = str(key)
        if name.startswith("_") or name in _INVOCATION_DROP:
            continue
        if any(hint in name.lower() for hint in _SECRET_HINTS):
            continue
        if isinstance(value, (int, float, bool)):
            params[name] = value
        elif isinstance(value, str) and len(value) <= 200:
            params[name] = value
    return json.dumps(params, ensure_ascii=False, sort_keys=True)


def _tool_schema_attributes(tools) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for index, tool in enumerate(tools or []):
        try:
            schema = json.dumps(tool, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            continue
        attributes[f"llm.tools.{index}.tool.json_schema"] = obs.clip(
            schema, _MSG_CONTENT_MAX,
        )
    return attributes


def _token_attributes(resp, usage) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "llm.token_count.prompt": usage.prompt or 0,
        "llm.token_count.completion": usage.completion or 0,
        "llm.token_count.total": usage.total or 0,
    }
    if usage.cache_hit:
        # 多轮 agent 每轮重发整段历史，prompt token 看着吓人但大半是缓存命中。
        # 不记这一项，面板上只剩那个虚高的总数。
        attributes["llm.token_count.prompt_details.cache_read"] = usage.cache_hit
    reasoning_tokens = _field(resp, "reasoning_tokens")
    if isinstance(reasoning_tokens, int) and reasoning_tokens > 0:
        attributes["llm.token_count.completion_details.reasoning"] = (
            reasoning_tokens
        )
    return attributes


def close_open_llm_span() -> None:
    """关掉还开着的 llm.invoke（下一轮开始，或整趟 generate 结束）。"""
    pair = _OPEN_LLM.get()
    if pair is None:
        return
    span_obj, token = pair
    _OPEN_LLM.set(None)
    obs.end_span(span_obj, token)


def _tool_input_preview(arguments: dict) -> str:
    preview: dict[str, object] = {}
    for key, value in arguments.items():
        if isinstance(value, (int, float, bool)):
            preview[key] = value
        elif isinstance(value, str) and len(value) <= 200:
            preview[key] = value
        elif isinstance(value, str):
            preview[key] = f"<{len(value)} chars>"
        else:
            preview[key] = type(value).__name__
    return json.dumps(preview, ensure_ascii=False)


def wrap_generate_model(model, llm_cfg: dict):
    """给 Generate 这趟的 ``model.invoke`` 记一轮 llm span。关观测则原样返回。

    HTTP 返回后若带了 tool_calls，span 先不关：agno 随后跑的工具 hook
    会挂到这一轮下面。下一轮 invoke 或整趟跑完再关。
    """
    if not obs.is_enabled():
        return model
    original_invoke = model.invoke
    model_name = (llm_cfg or {}).get("model", "?")
    provider = _provider_of(model_name)

    def observed_invoke(messages, **kwargs):
        from xskill.usage import extract_usage
        close_open_llm_span()
        features = obs.collector()
        round_index = features.note_llm_round()
        attributes: dict[str, Any] = {
            obs.SPAN_KIND: obs.KIND_LLM,
            "llm.model_name": model_name,
            "llm.provider": provider or None,
            "llm.invocation_parameters": _invocation_parameters(llm_cfg, kwargs),
            "xskill.llm_round": round_index,
            "xskill.message_count": len(messages or []),
            "input.value": obs.clip_tail(_messages_preview(messages)),
        }
        attributes.update(_input_message_attributes(messages))
        attributes.update(_tool_schema_attributes(kwargs.get("tools")))
        span_obj, token = obs.start_span("llm.invoke", **attributes)
        _OPEN_LLM.set((span_obj, token))
        try:
            resp = original_invoke(messages, **kwargs)
            usage = extract_usage(resp)
            names = [name for name, _args in _iter_tool_calls(resp)]
            done = {
                "llm.tool_call_names": ",".join(names),
                "output.value": obs.clip(_response_preview(resp)),
                **_token_attributes(resp, usage),
                **_output_message_attributes(resp),
            }
            span_obj.set_attributes(done)
            if not _response_has_tool_calls(resp):
                close_open_llm_span()
            return resp
        except BaseException as exc:
            record = getattr(span_obj, "record_exception", None)
            if callable(record):
                try:
                    record(exc)
                except Exception:  # noqa: BLE001
                    pass
            close_open_llm_span()
            raise

    model.invoke = observed_invoke
    return model


def generate_tool_hooks() -> list:
    """只给 Generate 的 agno Agent 挂。签名必须保持 agno hook 约定。"""
    if not obs.is_enabled():
        return []

    def observe_tool(function_name, function_call, arguments):
        args = arguments if isinstance(arguments, dict) else {}
        traj_id = None
        for key in ("path", "file_path", "traj_id"):
            if key in args:
                traj_id = obs.traj_id_from_path(args.get(key))
                if traj_id:
                    break
        attributes = {
            obs.SPAN_KIND: obs.KIND_TOOL,
            "tool.name": str(function_name),
            "xskill.traj_id": traj_id,
            "input.value": _tool_input_preview(args),
        }
        for key, value in args.items():
            if isinstance(value, (int, float, bool)):
                attributes[f"tool.arg.{key}"] = value
            elif isinstance(value, str) and len(value) <= 200:
                attributes[f"tool.arg.{key}"] = value
            elif isinstance(value, str):
                attributes[f"tool.arg.{key}.chars"] = len(value)
        started = time.perf_counter()
        failed = False
        try:
            with obs.span(f"tool.{function_name}", **attributes) as sp:
                result = function_call(**args)
                output = str(result or "")
                sp.set_attribute("tool.result.chars", len(output))
                sp.set_attribute("output.value", obs.clip(output))
                return result
        except BaseException:
            failed = True
            raise
        finally:
            obs.collector().note_tool_call(
                str(function_name),
                arguments=args,
                seconds=time.perf_counter() - started,
                failed=failed,
            )

    return [observe_tool]


def wrap_generate_factory(factory: Callable[..., Any], llm_cfg: dict) -> Callable[..., Any]:
    """只包装 Generate 拿到的那份 factory。共用 ``make_default_factory`` 本身不动。"""
    if not obs.is_enabled():
        return factory

    def wrapped(*, instructions, tools, **kwargs):
        hooks = generate_tool_hooks()
        if hooks:
            kwargs.setdefault("tool_hooks", hooks)
        try:
            agent = factory(instructions=instructions, tools=tools, **kwargs)
        except TypeError:
            agent = factory(instructions=instructions, tools=tools)
        model = getattr(agent, "model", None)
        if model is not None:
            wrap_generate_model(model, llm_cfg)
        return agent

    return wrapped


def _observe_compact(manager, messages, compact_fn, prefix_box, inner=None) -> bool:
    from xskill.agents.context_budget import _estimate_history_tokens

    tokens_before = _estimate_history_tokens(
        messages,
        cjk_rate=manager.cjk_rate,
        calibration=manager._calibration,
        cache=manager._est_cache,
    )
    started = time.perf_counter()
    compacted = False
    with obs.span(
        "context.compact",
        **{
            obs.SPAN_KIND: obs.KIND_CHAIN,
            "xskill.tokens_before": tokens_before,
            "xskill.compact_token_limit": manager.compact_token_limit,
            "xskill.max_context": manager.max_context,
        },
    ) as sp:
        try:
            if inner is not None:
                compacted = inner(manager, messages, compact_fn, prefix_box)
            else:
                compacted = manager._run_compact(messages, compact_fn, prefix_box)
            return compacted
        finally:
            tokens_after = _estimate_history_tokens(
                messages,
                cjk_rate=manager.cjk_rate,
                calibration=manager._calibration,
                cache=manager._est_cache,
            )
            sp.set_attributes({
                "xskill.tokens_after": tokens_after,
                "xskill.compacted": compacted,
            })
            obs.collector().note_compact(
                seconds=time.perf_counter() - started,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                ok=compacted,
            )


def _spill_hook() -> None:
    obs.collector().note_spill()


def _bind_obs_context_hooks() -> None:
    from xskill.agents.context_budget import _STATE, bind_context_hooks

    previous = getattr(_STATE, "compact_wrapper", None)
    bind_context_hooks(
        compact_wrapper=lambda manager, messages, compact_fn, prefix_box: (
            _observe_compact(
                manager, messages, compact_fn, prefix_box, previous,
            )
        ),
        spill_hook=_spill_hook,
    )


@contextmanager
def attach_generate_context_hooks() -> Iterator[None]:
    """测试用：只绑本线程 compact/spill 旁路，不建 generate.run 根 span。"""
    from xskill.agents.context_budget import bind_context_hooks

    if not obs.is_enabled():
        yield
        return
    _bind_obs_context_hooks()
    try:
        yield
    finally:
        bind_context_hooks()


@contextmanager
def observe_generate_run(
    *,
    user_id: str,
    job_id: str,
    read_roots: int,
    user_msg: str,
) -> Iterator[Any]:
    """一次 GenerateAgent.run：根 span、本线程 compact 旁路、特征落盘。"""
    from xskill.agents.context_budget import bind_context_hooks

    if not obs.is_enabled():
        yield None
        return
    _bind_obs_context_hooks()
    try:
        with obs.agent_run(
            "generate",
            **{
                "xskill.user_id": user_id,
                "xskill.job_id": job_id,
                "xskill.read_roots": read_roots,
                "input.value": obs.clip(user_msg),
            },
        ) as root:
            try:
                yield root
            finally:
                close_open_llm_span()
    finally:
        bind_context_hooks()
