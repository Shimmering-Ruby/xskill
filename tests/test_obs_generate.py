"""Generate OTel：没 endpoint 就是空壳，有值才包 invoke。不要求装 SDK。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from xskill.agents import agent_tools
from xskill.agents.generate_agent import GenerateAgent


def _ctx(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    return skill_dir, agent_tools.create_agent_tool_context(
        skill_dir=skill_dir, data_dir=skill_dir, extra_read_roots=(),
    )


def test_no_endpoint_is_off(monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL_ENDPOINT", raising=False)
    from xskill.obs import tracing

    tracing.reset()
    assert tracing.endpoint() == ""
    assert tracing.is_enabled() is False


def test_endpoint_turns_on(monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL_ENDPOINT", "http://127.0.0.1:6006")
    from xskill.obs import tracing

    tracing.reset()
    assert tracing.is_enabled() is True


def test_generate_runs_without_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL_ENDPOINT", raising=False)
    from xskill.obs import tracing

    tracing.reset()

    def factory(*, instructions, tools):
        class _Agent:
            def run(self, *_a, **_k):
                class _R:
                    content = "ok"

                return _R()

        return _Agent()

    skill_dir, ctx = _ctx(tmp_path)
    agent = GenerateAgent(
        skill_dir=skill_dir, agno_agent_factory=factory, llm_cfg={}, logs_dir=None,
    )
    with agent_tools.use_agent_tool_context(ctx):
        text = agent.run(instruction="写一个 skill", user_id="u", job_id="j")
    assert text == "ok"


def test_wraps_invoke_when_endpoint_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL_ENDPOINT", "http://127.0.0.1:6006")
    from xskill.obs import tracing
    from xskill.obs.generate import wrap_factory

    tracing.reset()
    names: list[str] = []

    @contextmanager
    def fake_span(name, **_attrs):
        names.append(name)

        class _S:
            def set_attribute(self, *_a, **_k):
                return None

        yield _S()

    monkeypatch.setattr(tracing, "is_enabled", lambda: True)
    monkeypatch.setattr(tracing, "setup", lambda: True)
    monkeypatch.setattr(tracing, "span", fake_span)

    calls = {"n": 0}

    class _Model:
        def invoke(self, messages, **_k):
            calls["n"] += 1

            class _R:
                content = "hi"
                tool_calls = []

            return _R()

    def factory(*, instructions, tools):
        class _Agent:
            model = _Model()

            def run(self, *_a, **_k):
                self.model.invoke([{"role": "user", "content": "x"}])

                class _R:
                    content = "done"

                return _R()

        return _Agent()

    skill_dir, ctx = _ctx(tmp_path)
    agent = GenerateAgent(
        skill_dir=skill_dir,
        agno_agent_factory=wrap_factory(factory),
        llm_cfg={},
        logs_dir=None,
    )
    with agent_tools.use_agent_tool_context(ctx):
        assert agent.run(instruction="写", user_id="u", job_id="j1") == "done"
    assert calls["n"] == 1
    assert "generate.run" in names
    assert "llm.invoke" in names


def test_skill_edit_source_does_not_import_obs():
    text = Path("src/xskill/agents/skill_edit_agent.py").read_text(encoding="utf-8")
    assert "xskill.obs" not in text
    assert "opentelemetry" not in text
