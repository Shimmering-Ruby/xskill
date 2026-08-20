"""agent 工具的参数注解必须在 Python 3.9 上可求值。

agno 从函数签名生成 JSON schema；``list | None`` 这类 PEP 604 写法在 3.9 上
无法在运行时求值，agno 会放弃解析**整个函数**的参数（日志
``Could not parse args for <tool>``），生成的 schema 里 ``required`` 为空、
``properties`` 缺失——模型看到的工具定义就是残缺的。#246 给 ``submit_atom``
加了「ux_score 必填」的断言，恰好把这个既有隐患在 3.9 CI 上照了出来。

本测试对每个 ``@tool`` 装饰的函数做一次不依赖 agno 的求值检查，让这类回归
在任何 Python 版本上都能被本地 pytest 抓到，而不只是等 3.9 的 CI。
"""
from __future__ import annotations

import inspect
import typing

import pytest

from xskill.agents import agent_tools


def _tool_functions():
    for name, obj in vars(agent_tools).items():
        entrypoint = getattr(obj, "entrypoint", None)
        if callable(entrypoint):          # agno Function wrapper
            yield name, entrypoint
        elif inspect.isfunction(obj) and getattr(obj, "__wrapped__", None):
            yield name, obj.__wrapped__


@pytest.mark.parametrize("tool_name,fn", list(_tool_functions()))
def test_tool_annotations_evaluate_on_this_python(tool_name, fn):
    """``typing.get_type_hints`` 会像 3.9 上的 agno 一样求值注解；PEP 604 写法在
    3.9 上会在这里抛 TypeError。3.10+ 上恒通过，本测试的价值在 3.9 CI job。"""
    try:
        typing.get_type_hints(fn)
    except TypeError as exc:
        pytest.fail(
            f"tool {tool_name!r} has an annotation that cannot be evaluated on "
            f"this Python: {exc}. Use typing.Optional[...] instead of 'X | None' "
            f"so agno can build the tool schema on 3.9."
        )
