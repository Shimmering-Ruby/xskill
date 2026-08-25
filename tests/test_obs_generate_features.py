"""obs 层：默认关、开了才记，以及特征口径对不对。

重点覆盖三件事：
1. ``XSKILL_OTEL`` 没设时所有埋点是空操作，产品行为一点不变
2. 特征口径：compact 次数、工具调用次数、读到的轨迹 id 列表
3. compact 埋点挂在真实的 ``ContextManager`` 上，数得出触发次数
"""
from __future__ import annotations

import json

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
        "PHOENIX_COLLECTOR_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    obs.reset_collector()
    yield
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
    from xskill.agents import agno_factory
    assert agno_factory.otel_tool_hooks() == []


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
    from xskill.agents import agno_factory
    hooks = agno_factory.otel_tool_hooks()
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
    from xskill.agents import agno_factory
    hook = agno_factory.otel_tool_hooks()[0]

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
    invoke(messages)

    assert compact_calls, "compact_fn 应该被调用"
    snapshot = obs.collector().as_dict()
    assert snapshot["compact_count"] == 1
    event = snapshot["compact_events"][0]
    assert event["ok"] is True
    assert event["tokens_before"] > event["tokens_after"]
    assert event["attempts"] == 1


def test_context_manager_no_compact_when_under_limit(tmp_path, monkeypatch):
    _enable(monkeypatch, tmp_path)
    from xskill.agents.context_budget import ContextManager

    manager = ContextManager(
        200_000,
        compact_token_limit=100_000,
        compact_keep_recent_messages=2,
        compact_fn=lambda prompt: "summary",
    )
    invoke = manager.wrap(_static_invoke)
    invoke(_history(turns=2, chars=200))
    assert obs.collector().as_dict()["compact_count"] == 0


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
