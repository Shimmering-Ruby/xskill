"""Generate OTel：没 endpoint 就是空壳。不要求装探针。"""
from __future__ import annotations

from contextlib import contextmanager

from xskill.agents import agent_tools
from xskill.agents.generate_agent import GenerateAgent


def _ctx(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    return skill_dir, agent_tools.create_agent_tool_context(
        skill_dir=skill_dir, data_dir=skill_dir, extra_read_roots=(),
    )


def test_no_endpoint_is_off(monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL_ENDPOINT", raising=False)
    from xskill.obs import tracing

    tracing.reset()
    assert tracing.is_enabled() is False


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
        assert agent.run(instruction="写一个 skill", user_id="u", job_id="j") == "ok"


def test_agent_span_when_endpoint_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL_ENDPOINT", "http://127.0.0.1:8873")
    from xskill.obs import tracing

    tracing.reset()
    names: list[str] = []
    kinds: list[str] = []

    @contextmanager
    def fake_span(name, **attrs):
        names.append(name)
        kinds.append(attrs.get("openinference.span.kind", ""))

        class _S:
            def set_attribute(self, *_a, **_k):
                return None

        yield _S()

    monkeypatch.setattr(tracing, "setup", lambda: True)
    monkeypatch.setattr(tracing, "span", fake_span)

    def factory(*, instructions, tools):
        class _Agent:
            def run(self, *_a, **_k):
                class _R:
                    content = "done"

                return _R()

        return _Agent()

    skill_dir, ctx = _ctx(tmp_path)
    agent = GenerateAgent(
        skill_dir=skill_dir, agno_agent_factory=factory, llm_cfg={}, logs_dir=None,
    )
    with agent_tools.use_agent_tool_context(ctx):
        assert agent.run(instruction="写", user_id="u", job_id="j1") == "done"
    assert names == ["generate.run"]
    assert kinds == ["AGENT"]
