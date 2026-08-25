"""GenerateAgent 专用观测：工具 hook、模型包装、compact 旁路。

只给 ``xskill generate`` 用。SkillEdit / TaskAgent 不走这里，共用的
``agno_factory`` 和 compact 默认路径也不挂 OpenTelemetry。
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from xskill import obs


def _messages_preview(messages) -> str:
    parts: list[str] = []
    for message in messages or []:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(str(item["text"]))
                else:
                    texts.append(str(item))
            content = "\n".join(texts)
        parts.append(f"{role or '?'}: {content or ''}")
    return "\n\n".join(parts)


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
    """给 Generate 这趟的 ``model.invoke`` 记一轮 llm span。关观测则原样返回。"""
    if not obs.is_enabled():
        return model
    original_invoke = model.invoke
    model_name = (llm_cfg or {}).get("model", "?")

    def observed_invoke(messages, **kwargs):
        from xskill.usage import extract_usage
        features = obs.collector()
        round_index = features.note_llm_round()
        with obs.span(
            "llm.invoke",
            **{
                obs.SPAN_KIND: obs.KIND_LLM,
                "llm.model_name": model_name,
                "xskill.llm_round": round_index,
                "xskill.message_count": len(messages or []),
                "input.value": obs.clip(_messages_preview(messages)),
            },
        ) as sp:
            resp = original_invoke(messages, **kwargs)
            usage = extract_usage(resp)
            sp.set_attributes({
                "llm.token_count.prompt": usage.prompt or 0,
                "llm.token_count.completion": usage.completion or 0,
                "output.value": obs.clip(getattr(resp, "content", "") or ""),
            })
            return resp

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


def _compact_wrapper(manager, messages, compact_fn, prefix_box) -> bool:
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


@contextmanager
def attach_generate_context_hooks() -> Iterator[None]:
    """测试用：只绑本线程 compact/spill 旁路，不建 generate.run 根 span。"""
    from xskill.agents.context_budget import bind_context_hooks

    if not obs.is_enabled():
        yield
        return
    bind_context_hooks(compact_wrapper=_compact_wrapper, spill_hook=_spill_hook)
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
    bind_context_hooks(compact_wrapper=_compact_wrapper, spill_hook=_spill_hook)
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
            yield root
    finally:
        bind_context_hooks()
