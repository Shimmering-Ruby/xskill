"""agno 工厂 × 触发探针 集成冒烟（真 agno Agent，零网络）。

daemon 注入链路是：serve 启动 ``init_context``(llm/embed) → commit 钩子
``_run_description_optimization`` 现建 ``make_default_factory(config)`` →
``optimize_description`` → ``probe_trigger`` 真跑代理。此前所有单测用
``_FakeAgent`` 替身——没有任何测试证明 **真 agno Agent** 能：

  1. 把 ``probe_trigger`` 造的普通函数注册成工具；
  2. 执行工具时被 ``StopAgentRun`` 优雅终止（不当 error）；
  3. ``record["triggered"]`` 闭包正确捕获。

本文件用真 agno 类补这一段：模型层在最低点（``model.invoke``）换成脚本化
ModelResponse——**绝不打真 API**。
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("agno")

from agno.agent import Agent  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402

from xskill.agents.agno_factory import make_default_factory  # noqa: E402
from xskill.skill import trigger_probe as tp  # noqa: E402

_CFG = {
    "llm": {
        "base_url": "http://127.0.0.1:9/v1",   # 黑洞地址：真发请求必失败
        "model": "stub-model",
        "api_key": "sk-test",
        "max_context": 200000,
    },
}


def _scripted_invoke(tool_name: str | None):
    """造一个 model.invoke 替身：第一轮回指定工具调用，之后回纯文本。

    agno 2.x 的 ``Model.invoke`` 契约：填充 ``assistant_message`` 并返回
    ``ModelResponse``。
    """
    state = {"n": 0}

    def _tc(name):
        return [{
            "id": "call_1", "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps({"reason": "fits"})},
        }]

    def invoke(messages=None, assistant_message=None, **kwargs):  # noqa: ARG001
        state["n"] += 1
        if state["n"] == 1 and tool_name:
            if assistant_message is not None:
                assistant_message.role = "assistant"
                assistant_message.content = None
                assistant_message.tool_calls = _tc(tool_name)
            return ModelResponse(role="assistant", content=None,
                                 tool_calls=_tc(tool_name))
        if assistant_message is not None:
            assistant_message.role = "assistant"
            assistant_message.content = "no skill fits"
        return ModelResponse(role="assistant", content="no skill fits")

    return invoke


def _smoke_factory(tool_name: str | None):
    """包真工厂：Agent 照常构造（验证 make_default_factory 全链路），只在
    最低层把 model.invoke 换成脚本——上层 agno 工具执行逻辑全是真的。"""
    real = make_default_factory(_CFG)

    def factory(*, instructions, tools, **kwargs):
        agent = real(instructions=instructions, tools=tools, **kwargs)
        agent.model.invoke = _scripted_invoke(tool_name)
        return agent

    return factory


def test_factory_builds_real_agno_agent():
    factory = make_default_factory(_CFG)
    agent = factory(instructions=["x"], tools=[tp._stub_read_file],
                    tool_call_limit=4)
    assert isinstance(agent, Agent)
    # 模型配置确实从 config.llm 读取
    assert agent.model.id == "stub-model"


def test_probe_trigger_self_through_real_agno():
    catalog = [{"name": "decoy-skill", "description": "deploy to k8s"}]
    out = tp.probe_trigger(
        "fix my django migration", "my-skill", "Use this for django fixes",
        catalog,
        agno_agent_factory=_smoke_factory(tp._slug_to_tool("my-skill")),
        desc_cap=256,
    )
    assert out == "my-skill"


def test_probe_trigger_decoy_through_real_agno():
    catalog = [{"name": "decoy-skill", "description": "deploy to k8s"}]
    out = tp.probe_trigger(
        "deploy my app", "my-skill", "django fixes", catalog,
        agno_agent_factory=_smoke_factory(tp._slug_to_tool("decoy-skill")),
        desc_cap=256,
    )
    assert out == "decoy-skill"


def test_probe_trigger_none_through_real_agno():
    out = tp.probe_trigger(
        "explain merge sort", "my-skill", "django fixes", [],
        agno_agent_factory=_smoke_factory(None), desc_cap=256,
    )
    assert out == "NONE"
