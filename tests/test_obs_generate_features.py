"""obs 层：默认关、开了才记，以及特征口径对不对。

重点覆盖：
1. ``XSKILL_OTEL`` 没设时所有埋点是空操作
2. 特征口径：compact 次数、工具调用次数、读到的轨迹 id 列表
3. compact 旁路只在 Generate 这趟绑上；共用 factory 不挂 OTel
4. OpenTelemetry / Phoenix 不进主依赖
"""
from __future__ import annotations

import json
import os

import pytest

from xskill import obs
from xskill.obs.features import FeatureCollector, traj_id_from_path


@pytest.fixture(autouse=True)
def _clean_obs_env(monkeypatch, tmp_path):
    for name in (
        "XSKILL_OTEL",
        "XSKILL_OTEL_JOB",
        "XSKILL_OTEL_SESSION",
        "XSKILL_OTEL_OUT",
        "XSKILL_OTEL_ENDPOINT",
        "XSKILL_OTEL_PROJECT",
        "XSKILL_OTEL_CONSOLE",
        "XSKILL_OTEL_CAPTURE_CONTENT",
        "XSKILL_OTEL_MSG_BUDGET",
        "PHOENIX_COLLECTOR_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    obs.reset_collector()
    yield
    from xskill.obs.generate import close_open_llm_span
    close_open_llm_span()
    obs.reset_collector()


def _enable(monkeypatch, out_dir, job="testjob"):
    monkeypatch.setenv("XSKILL_OTEL", "1")
    monkeypatch.setenv("XSKILL_OTEL_JOB", job)
    monkeypatch.setenv("XSKILL_OTEL_OUT", str(out_dir))


# ── 默认关 ──────────────────────────────────────────────────────


def test_disabled_by_default(tmp_path):
    assert obs.is_enabled() is False
    assert obs.setup() is False
    assert obs.features_path() is None


def test_disabled_agent_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL_OUT", str(tmp_path))
    with obs.agent_run("generate"):
        obs.collector().note_llm_round()
    assert not (tmp_path / "features.json").exists()


def test_disabled_span_is_noop():
    with obs.span("tool.read_file", **{"tool.name": "read_file"}) as sp:
        sp.set_attribute("x", 1)
        sp.set_attributes({"y": 2})


def test_disabled_tool_hooks_are_empty(monkeypatch):
    from xskill.obs.generate import generate_tool_hooks
    assert generate_tool_hooks() == []


def test_shared_factory_has_no_otel_hooks():
    from xskill.agents import agno_factory
    assert not hasattr(agno_factory, "otel_tool_hooks")
    assert not hasattr(agno_factory, "_wrap_with_otel")


# ── 轨迹 id 抽取 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mock/sessions/traj_cursor_unknown_1a2b3c4d.md",
         "traj_cursor_unknown_1a2b3c4d"),
        ("/mock/sessions/traj_cc_proj_9f8e.json", "traj_cc_proj_9f8e"),
        ("traj_oc_admin_1d6a", "traj_oc_admin_1d6a"),
        ("/mock/sessions", None),
        ("/etc/passwd", None),
        ("/mock/notes_traj_cursor_x.md", None),
        ("", None),
        (None, None),
    ],
)
def test_traj_id_from_path(path, expected):
    assert traj_id_from_path(path) == expected


# ── 特征口径 ────────────────────────────────────────────────────


def test_collector_counts_tools_and_dedupes_traj_ids():
    features = FeatureCollector()
    features.start(job="j", agent="generate")
    features.note_tool_call("list_files", arguments={"path": "/mock/sessions"})
    features.note_tool_call(
        "read_file", arguments={"path": "/mock/sessions/traj_a_0001.md"},
    )
    # 同一条轨迹翻第二页：调用次数 +1，id 列表不重复
    features.note_tool_call(
        "read_file",
        arguments={"path": "/mock/sessions/traj_a_0001.md", "offset": 200},
    )
    features.note_tool_call(
        "read_file", arguments={"path": "/mock/sessions/traj_b_0002.md"},
    )
    features.note_tool_call("write_file", arguments={"path": "s/SKILL.md"})
    features.note_tool_call("commit_generate_main", arguments={}, failed=True)
    features.finish()

    snapshot = features.as_dict()
    assert snapshot["tool_call_total"] == 6
    assert snapshot["tool_calls"] == {
        "commit_generate_main": 1,
        "list_files": 1,
        "read_file": 3,
        "write_file": 1,
    }
    assert snapshot["read_tool_calls"] == {"read_file": 3, "list_files": 1}
    assert snapshot["read_tool_call_total"] == 4
    assert snapshot["traj_read_calls"] == 3
    assert snapshot["read_traj_count"] == 2
    assert snapshot["read_traj_ids"] == ["traj_a_0001", "traj_b_0002"]
    assert snapshot["tool_errors"] == {"commit_generate_main": 1}


def test_collector_counts_traj_cards_separately_from_reads():
    features = FeatureCollector()
    features.note_tool_call(
        "traj_cards",
        arguments={"traj_ids": "traj_a_0001 traj_b_0002,traj_a_0001"},
    )
    features.note_tool_call(
        "traj_cards", arguments={"traj_ids": "traj_c_0003"},
    )
    features.note_tool_call(
        "read_traj", arguments={"traj_id": "traj_a_0001"},
    )
    snapshot = features.as_dict()
    assert snapshot["card_traj_ids"] == ["traj_a_0001", "traj_b_0002", "traj_c_0003"]
    assert snapshot["card_traj_count"] == 3
    assert snapshot["read_traj_ids"] == ["traj_a_0001"]
    assert snapshot["traj_read_calls"] == 1
    assert snapshot["read_tool_calls"]["traj_cards"] == 2


def test_collector_records_compact_with_window():
    features = FeatureCollector()
    features.note_budget(
        max_context=200_000, compact_token_limit=100_000, enable_spill=False,
    )
    features.note_llm_round()
    features.note_llm_round()
    features.note_compact(
        seconds=48.5, tokens_before=102_415, tokens_after=6_323,
    )
    snapshot = features.as_dict()
    assert snapshot["compact_count"] == 1
    assert snapshot["context"]["compact_token_limit"] == 100_000
    event = snapshot["compact_events"][0]
    assert event["tokens_before"] == 102_415
    assert event["tokens_after"] == 6_323
    # compact 发生在第几轮，是"读完就压"这类行为模式的关键
    assert event["at_llm_round"] == 2


def test_agent_run_dumps_features_json(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="dump-job")
    with obs.agent_run("generate", **{"xskill.user_id": "alice"}):
        obs.collector().note_llm_round()
        obs.collector().note_tool_call(
            "read_file", arguments={"path": "/m/traj_x_1.md"},
        )
    payload = json.loads((tmp_path / "features.json").read_text("utf-8"))
    assert payload["job"] == "dump-job"
    assert payload["agent"] == "generate"
    assert payload["llm_rounds"] == 1
    assert payload["read_traj_ids"] == ["traj_x_1"]
    assert payload["error"] == ""


def test_span_carries_session_id(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="sess-job")
    with obs.span("generate.run") as sp:
        attrs = dict(getattr(sp, "attributes", None) or {})
    assert attrs["session.id"] == "sess-job"
    assert attrs["openinference.session.id"] == "sess-job"
    assert attrs["xskill.job"] == "sess-job"


def test_agent_run_keeps_input_output(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="io-job")
    with obs.agent_run("generate", **{"input.value": "看这些轨迹"}) as root:
        assert root is not None
        root.set_attribute("output.value", "写好了")
        attrs = dict(getattr(root, "attributes", None) or {})
    assert attrs.get("input.value") == "看这些轨迹"
    assert attrs.get("output.value") == "写好了"


def test_agent_run_records_error_and_reraises(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="boom")
    with pytest.raises(RuntimeError, match="kaboom"):
        with obs.agent_run("generate"):
            raise RuntimeError("kaboom")
    payload = json.loads((tmp_path / "features.json").read_text("utf-8"))
    assert "kaboom" in payload["error"]


def test_span_body_exception_is_not_swallowed(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="inner"):
        with obs.span("tool.read_file"):
            raise ValueError("inner")


# ── 工具 hook 挂在真实链路上 ────────────────────────────────────


def test_tool_hook_counts_and_passes_arguments(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from xskill.obs.generate import generate_tool_hooks
    hooks = generate_tool_hooks()
    assert len(hooks) == 1

    seen = {}

    def next_func(**kwargs):
        seen.update(kwargs)
        return "file body"

    result = hooks[0](
        "read_file", next_func, {"path": "/m/traj_z_9.md", "offset": 1},
    )
    assert result == "file body"
    # hook 必须把参数原样传下去，不能吞
    assert seen == {"path": "/m/traj_z_9.md", "offset": 1}
    snapshot = obs.collector().as_dict()
    assert snapshot["tool_calls"] == {"read_file": 1}
    assert snapshot["read_traj_ids"] == ["traj_z_9"]


def test_tool_hook_records_failure_and_reraises(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from xskill.obs.generate import generate_tool_hooks
    hook = generate_tool_hooks()[0]

    def boom(**kwargs):
        del kwargs
        raise OSError("disk gone")

    with pytest.raises(OSError, match="disk gone"):
        hook("read_file", boom, {"path": "/m/traj_q_1.md"})
    snapshot = obs.collector().as_dict()
    assert snapshot["tool_errors"] == {"read_file": 1}
    assert snapshot["tool_call_total"] == 1


# ── compact 埋点挂在真实 ContextManager 上 ─────────────────────


def _history(turns: int, chars: int) -> list:
    """system + turn0 user + 若干轮，够 compact 有东西可丢。

    ``_compact_history_in_place`` 只压 system / 首条 user / 尾部之外的部分，
    历史不比 keep_recent 长就直接返回 False，压根不调 compact_fn。
    """
    from agno.models.message import Message
    messages = [
        Message(role="system", content="you are a test agent"),
        Message(role="user", content="turn0 " + "u" * chars),
    ]
    for index in range(turns):
        role = "assistant" if index % 2 == 0 else "user"
        messages.append(Message(role=role, content=f"m{index} " + "x" * chars))
    return messages


class _Resp:
    content = "ok"
    usage = None


def _static_invoke(messages, **kwargs):
    del messages, kwargs
    return _Resp()


def test_context_manager_compact_is_counted(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="compact-job")
    from xskill.agents.context_budget import ContextManager
    from xskill.obs.generate import attach_generate_context_hooks

    compact_calls = []

    def fake_compact(prompt: str) -> str:
        compact_calls.append(prompt)
        return "## Model handoff summary\n\ndone\n"

    manager = ContextManager(
        200_000,
        compact_token_limit=1_000,
        compact_keep_recent_messages=2,
        compact_fn=fake_compact,
    )
    invoke = manager.wrap(_static_invoke)
    # 8 轮 × 4000 字符 ≈ 8000+ token，远超 1000 的阈值
    messages = _history(turns=8, chars=4_000)
    with attach_generate_context_hooks():
        invoke(messages)

    assert compact_calls, "compact_fn 应该被调用"
    snapshot = obs.collector().as_dict()
    assert snapshot["compact_count"] == 1
    event = snapshot["compact_events"][0]
    assert event["ok"] is True
    assert event["tokens_before"] > event["tokens_after"]


def test_context_manager_no_compact_when_under_limit(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from xskill.agents.context_budget import ContextManager
    from xskill.obs.generate import attach_generate_context_hooks

    manager = ContextManager(
        200_000,
        compact_token_limit=100_000,
        compact_keep_recent_messages=2,
        compact_fn=lambda prompt: "summary",
    )
    invoke = manager.wrap(_static_invoke)
    with attach_generate_context_hooks():
        invoke(_history(turns=2, chars=200))
    assert obs.collector().as_dict()["compact_count"] == 0


def test_compact_not_counted_without_generate_hook(tmp_path, monkeypatch):
    """XSKILL_OTEL 开着，但不是 Generate 这趟，compact 不记账。"""
    _enable(monkeypatch, tmp_path)
    from xskill.agents.context_budget import ContextManager

    compact_calls = []
    manager = ContextManager(
        200_000,
        compact_token_limit=1_000,
        compact_keep_recent_messages=2,
        compact_fn=lambda prompt: compact_calls.append(prompt) or "summary",
    )
    invoke = manager.wrap(_static_invoke)
    invoke(_history(turns=8, chars=4_000))
    assert compact_calls, "没绑 Generate 旁路不该挡住 compact 本身"
    assert obs.collector().as_dict()["compact_count"] == 0


def test_wrap_factory_is_identity_when_disabled():
    from xskill.obs.generate import wrap_generate_factory

    def factory(*, instructions, tools, **kwargs):
        del instructions, tools, kwargs
        return "agent"

    assert wrap_generate_factory(factory, {}) is factory


def test_wrap_factory_adds_tool_hooks_when_enabled(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from xskill.obs.generate import wrap_generate_factory

    seen = {}

    class _Model:
        def invoke(self, messages, **kwargs):
            del messages, kwargs
            return _Resp()

    class _Agent:
        def __init__(self):
            self.model = _Model()

    def factory(*, instructions, tools, **kwargs):
        seen["hooks"] = kwargs.get("tool_hooks")
        return _Agent()

    wrapped = wrap_generate_factory(factory, {"model": "test"})
    agent = wrapped(instructions=["x"], tools=[])
    assert seen["hooks"], "Generate factory 应该挂上工具 hook"
    assert agent.model.invoke.__name__ == "observed_invoke"


def test_pyproject_keeps_otel_optional_only():
    from pathlib import Path

    text = Path("pyproject.toml").read_text(encoding="utf-8")
    deps_start = text.index("dependencies = [")
    extras_start = text.index("[project.optional-dependencies]")
    deps_block = text[deps_start:extras_start]
    assert "opentelemetry" not in deps_block
    assert "arize-phoenix" not in deps_block
    extras = text[extras_start:]
    assert "opentelemetry-api>=1.20" in extras
    assert "arize-phoenix>=7" in extras


def test_prepare_generate_obs_fills_job_and_out(tmp_path, monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL", "1")
    monkeypatch.delenv("XSKILL_OTEL_JOB", raising=False)
    monkeypatch.delenv("XSKILL_OTEL_SESSION", raising=False)
    monkeypatch.delenv("XSKILL_OTEL_OUT", raising=False)
    from xskill.team.server.generate_jobs import _prepare_generate_obs

    job = {"job_id": "abc123", "user_id": "alice"}
    _prepare_generate_obs(job, tmp_path)
    assert os.environ["XSKILL_OTEL_JOB"] == "abc123"
    out = tmp_path / "agents" / "generate_agents" / "alice" / "obs" / "abc123"
    assert os.environ["XSKILL_OTEL_OUT"] == str(out)
    assert out.is_dir()


def test_prepare_generate_obs_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL", raising=False)
    monkeypatch.delenv("XSKILL_OTEL_OUT", raising=False)
    from xskill.team.server.generate_jobs import _prepare_generate_obs

    _prepare_generate_obs({"job_id": "x", "user_id": "u"}, tmp_path)
    assert not os.environ.get("XSKILL_OTEL_OUT")


def test_compact_not_counted_when_obs_disabled(tmp_path, monkeypatch):
    """关掉埋点后 compact 照样发生，只是不记账。"""
    from xskill.agents.context_budget import ContextManager

    compact_calls = []
    manager = ContextManager(
        200_000,
        compact_token_limit=1_000,
        compact_keep_recent_messages=2,
        compact_fn=lambda prompt: compact_calls.append(prompt) or "summary",
    )
    invoke = manager.wrap(_static_invoke)
    invoke(_history(turns=8, chars=4_000))
    assert compact_calls, "关埋点不该影响 compact 本身"
    assert obs.collector().as_dict()["compact_count"] == 0


def test_messages_preview_keeps_latest_tool_result():
    from xskill.obs.generate import _messages_preview

    text = _messages_preview([
        {"role": "system", "content": "SYS" * 2000},
        {"role": "user", "content": "hello"},
        {"role": "tool", "content": "LATEST_WIKI_SURVEY_ROW"},
    ])
    assert "LATEST_WIKI_SURVEY_ROW" in text
    assert "system: <" in text
    assert "omitted>" in text
    assert "SYS" * 20 not in text


def test_response_preview_lists_tool_calls_first():
    from xskill.obs.generate import _response_preview

    class _RespWithTools:
        content = "thinking"
        tool_calls = [{
            "function": {
                "name": "session_cards",
                "arguments": '{"traj_ids":["traj_a"]}',
            },
        }]

    text = _response_preview(_RespWithTools())
    assert text.index("session_cards") < text.index("thinking")
    assert "traj_a" in text


def test_response_preview_keeps_reasoning():
    from xskill.obs.generate import _response_preview

    class _RespWithReasoning:
        content = "答案是 A"
        reasoning_content = "先看轨迹再排除 B"

    text = _response_preview(_RespWithReasoning())
    assert "先看轨迹再排除 B" in text
    assert text.index("先看轨迹") < text.index("答案是 A")


# ── OpenInference 消息属性 ─────────────────────────────────────


def test_input_messages_are_flattened_for_phoenix():
    from xskill.obs.generate import _input_message_attributes

    attrs = _input_message_attributes([
        {"role": "system", "content": "你是 GenerateAgent"},
        {"role": "user", "content": "看这些轨迹"},
        {
            "role": "assistant",
            "content": "先列目录",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "list_files", "arguments": '{"path":"/m"}'},
            }],
        },
        {"role": "tool", "content": "traj_a.md", "tool_call_id": "call_1"},
    ])
    assert attrs["llm.input_messages.0.message.role"] == "system"
    assert attrs["llm.input_messages.1.message.content"] == "看这些轨迹"
    base = "llm.input_messages.2.message.tool_calls.0.tool_call"
    assert attrs[f"{base}.id"] == "call_1"
    assert attrs[f"{base}.function.name"] == "list_files"
    assert attrs[f"{base}.function.arguments"] == '{"path":"/m"}'
    assert attrs["llm.input_messages.3.message.role"] == "tool"
    assert attrs["llm.input_messages.3.message.tool_call_id"] == "call_1"
    assert "xskill.input_messages_omitted" not in attrs


def test_input_messages_budget_keeps_system_and_tail(monkeypatch):
    from xskill.obs.generate import _input_message_attributes
    monkeypatch.setenv("XSKILL_OTEL_MSG_BUDGET", "600")

    messages = [{"role": "system", "content": "SYS"}]
    for index in range(10):
        messages.append({"role": "user", "content": f"m{index} " + "x" * 200})
    attrs = _input_message_attributes(messages)

    assert attrs["llm.input_messages.0.message.content"] == "SYS"
    # 预算只够尾部几条，被丢掉的必须报出条数，否则面板上凭空少消息
    assert attrs["xskill.input_messages_omitted"] > 0
    kept = sum(1 for key in attrs if key.endswith(".message.role"))
    assert kept + attrs["xskill.input_messages_omitted"] == len(messages)
    last = f"llm.input_messages.{kept - 1}.message.content"
    assert attrs[last].startswith("m9 ")


def test_output_message_carries_reasoning_and_content():
    from xskill.obs.generate import _output_message_attributes

    class _Resp:
        role = "assistant"
        content = "答案是 A"
        reasoning_content = "先排除 B"
        tool_calls = [{
            "id": "call_9",
            "function": {"name": "wiki_write", "arguments": "{}"},
        }]

    attrs = _output_message_attributes(_Resp())
    base = "llm.output_messages.0.message"
    assert attrs[f"{base}.role"] == "assistant"
    assert attrs[f"{base}.content"] == "答案是 A"
    assert attrs[f"{base}.contents.0.message_content.type"] == "reasoning"
    assert attrs[f"{base}.contents.0.message_content.text"] == "先排除 B"
    # 正文只由 message.content 出；再摆一份 text 进 contents 会被渲染两遍
    assert f"{base}.contents.1.message_content.type" not in attrs
    assert attrs[f"{base}.tool_calls.0.tool_call.function.name"] == "wiki_write"


def test_output_message_without_reasoning_has_no_contents():
    from xskill.obs.generate import _output_message_attributes

    class _Plain:
        content = "只有正文"

    attrs = _output_message_attributes(_Plain())
    assert attrs["llm.output_messages.0.message.content"] == "只有正文"
    assert not [key for key in attrs if ".contents." in key]


def test_invocation_parameters_drop_secrets():
    from xskill.obs.generate import _invocation_parameters

    payload = json.loads(_invocation_parameters(
        {
            "model": "deepseek-chat",
            "api_key": "sk-should-not-leak",
            "base_url": "http://10.0.0.1:8000",
            "temperature": 0.2,
            "max_tokens": 4096,
            "rate_limit": {"rpm": 60},
            "_pool_weights": [1, 2],
        },
        {"tool_choice": "auto", "compress_tool_results": False},
    ))
    assert payload == {
        "compress_tool_results": False,
        "max_tokens": 4096,
        "model": "deepseek-chat",
        "temperature": 0.2,
        "tool_choice": "auto",
    }


@pytest.mark.parametrize(
    "model_name,provider",
    [
        ("deepseek-chat", "deepseek"),
        ("deepseek-reasoner", "deepseek"),
        ("gpt-4o", "openai"),
        ("claude-sonnet-4", "anthropic"),
        ("some-local-model", ""),
    ],
)
def test_provider_inferred_from_model_name(model_name, provider):
    from xskill.obs.generate import _provider_of
    assert _provider_of(model_name) == provider


def test_llm_span_carries_openinference_attributes(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path, job="semconv-job")
    from xskill.obs.generate import close_open_llm_span, wrap_generate_model

    class _Usage:
        input_tokens = 2876
        output_tokens = 137
        total_tokens = 3013
        cache_read_tokens = 2048

    class _Resp:
        content = "先看轨迹"
        reasoning_content = ""
        response_usage = _Usage()
        tool_calls = [{
            "id": "call_a",
            "function": {"name": "list_sessions", "arguments": "{}"},
        }]

    captured = {}

    class _Model:
        def invoke(self, messages, **kwargs):
            del messages, kwargs
            return _Resp()

    model = wrap_generate_model(_Model(), {
        "model": "deepseek-chat", "api_key": "sk-nope", "temperature": 0.3,
    })
    model.invoke(
        messages=[
            {"role": "system", "content": "你是 GenerateAgent"},
            {"role": "user", "content": "看这些轨迹"},
        ],
        tools=[{"type": "function", "function": {"name": "list_sessions"}}],
    )
    from opentelemetry import trace
    captured.update(dict(getattr(trace.get_current_span(), "attributes", {}) or {}))
    close_open_llm_span()

    assert captured["llm.provider"] == "deepseek"
    assert captured["llm.input_messages.0.message.role"] == "system"
    assert captured["llm.input_messages.1.message.content"] == "看这些轨迹"
    assert captured["llm.output_messages.0.message.content"] == "先看轨迹"
    out_call = "llm.output_messages.0.message.tool_calls.0.tool_call.function.name"
    assert captured[out_call] == "list_sessions"
    assert captured["llm.token_count.total"] == 3013
    assert captured["llm.token_count.prompt_details.cache_read"] == 2048
    assert "list_sessions" in captured["llm.tools.0.tool.json_schema"]
    assert "sk-nope" not in captured["llm.invocation_parameters"]
    assert '"temperature": 0.3' in captured["llm.invocation_parameters"]


def test_span_attribute_limit_is_raised_for_message_lists(tmp_path, monkeypatch):
    """OTel 默认一条 span 只留 128 个属性，摊平对话会被静默截掉。"""
    _enable(monkeypatch, tmp_path, job="limit-job")
    assert obs.setup() is True
    from xskill.obs.tracing import _STATE
    limits = _STATE["provider"]._span_limits
    assert limits.max_span_attributes >= 1024


def test_tool_span_nests_under_open_llm(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from opentelemetry import trace
    from xskill.obs.generate import (
        close_open_llm_span,
        generate_tool_hooks,
        wrap_generate_model,
    )

    class _ToolResp:
        content = None
        usage = None
        tool_calls = [{
            "function": {"name": "wiki_status", "arguments": "{}"},
        }]

    class _Model:
        def invoke(self, messages, **kwargs):
            del messages, kwargs
            return _ToolResp()

    seen = {}
    model = wrap_generate_model(_Model(), {"model": "t"})
    model.invoke([{"role": "user", "content": "hi"}])
    llm_id = trace.get_current_span().get_span_context().span_id
    assert llm_id != 0

    def fn(**kwargs):
        del kwargs
        tool_span = trace.get_current_span()
        parent = getattr(tool_span, "parent", None)
        seen["parent"] = parent.span_id if parent is not None else None
        seen["name"] = getattr(tool_span, "name", "")
        return "wiki ok"

    generate_tool_hooks()[0]("wiki_status", fn, {})
    close_open_llm_span()
    assert seen["name"] == "tool.wiki_status"
    assert seen["parent"] == llm_id
