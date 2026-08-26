"""只给 GenerateAgent.run 用：根 span + llm.invoke + 工具。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from xskill.obs import tracing


@contextmanager
def observe_run(*, user_id: str, job_id: str) -> Iterator[None]:
    if not tracing.is_enabled():
        yield
        return
    try:
        with tracing.span("generate.run", user_id=user_id, job_id=job_id):
            yield
    finally:
        tracing.shutdown()


def wrap_factory(factory: Callable[..., Any]) -> Callable[..., Any]:
    if not tracing.is_enabled():
        return factory

    def wrapped(*, instructions, tools, **kwargs):
        try:
            agent = factory(instructions=instructions, tools=tools, **kwargs)
        except TypeError:
            agent = factory(instructions=instructions, tools=tools)
        _attach(agent)
        return agent

    return wrapped


def _attach(agent: Any) -> None:
    model = getattr(agent, "model", None)
    invoke = getattr(model, "invoke", None)
    if callable(invoke) and not getattr(invoke, "_xskill_otel", False):
        wrapped = _wrap_invoke(invoke)
        wrapped._xskill_otel = True  # type: ignore[attr-defined]
        model.invoke = wrapped
    hooks = list(getattr(agent, "tool_hooks", None) or [])
    if _on_tool not in hooks:
        hooks.append(_on_tool)
        try:
            agent.tool_hooks = hooks
        except Exception:  # noqa: BLE001
            return


def _wrap_invoke(invoke: Callable[..., Any]) -> Callable[..., Any]:
    def observed(messages, **kwargs):
        with tracing.span("llm.invoke") as current:
            current.set_attribute("input.value", tracing.clip(_preview(messages)))
            response = invoke(messages, **kwargs)
            current.set_attribute("output.value", tracing.clip(_response(response)))
            return response

    return observed


def _on_tool(function_name, function_call, arguments):
    attrs = {"tool": function_name}
    if isinstance(arguments, dict):
        for key in ("path", "traj_id", "skill_name"):
            value = arguments.get(key)
            if value:
                attrs[key] = tracing.clip(value, 200)
    with tracing.span(f"tool.{function_name}", **attrs):
        return function_call(**arguments)


def _preview(messages: Any) -> str:
    parts = []
    for message in messages or []:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        parts.append(f"{role or '?'}: {content or ''}")
    return "\n".join(parts)


def _response(response: Any) -> str:
    content = getattr(response, "content", None)
    if content:
        return str(content)
    tool_calls = getattr(response, "tool_calls", None) or []
    names = []
    for call in tool_calls:
        fn = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        if isinstance(fn, dict):
            names.append(fn.get("name") or "")
        else:
            names.append(getattr(fn, "name", "") or "")
    return " ".join(n for n in names if n)
