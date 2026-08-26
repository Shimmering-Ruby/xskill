"""没 endpoint 不打点；有 endpoint 只调用一次 setup。"""
from __future__ import annotations

from xskill.agents import agent_tools
from xskill.agents.generate_agent import GenerateAgent


def _ctx(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    return skill_dir, agent_tools.create_agent_tool_context(
        skill_dir=skill_dir, data_dir=skill_dir, extra_read_roots=(),
    )


def _factory(*, instructions, tools):
    class _Agent:
        def run(self, *_a, **_k):
            class _R:
                content = "ok"

            return _R()

    return _Agent()


def test_no_endpoint_is_off(monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL_ENDPOINT", raising=False)
    from xskill.obs import tracing

    tracing.reset()
    assert tracing.is_enabled() is False
    assert tracing.setup() is False


def test_generate_runs_without_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("XSKILL_OTEL_ENDPOINT", raising=False)
    from xskill.obs import tracing

    tracing.reset()
    skill_dir, ctx = _ctx(tmp_path)
    agent = GenerateAgent(
        skill_dir=skill_dir, agno_agent_factory=_factory, llm_cfg={}, logs_dir=None,
    )
    with agent_tools.use_agent_tool_context(ctx):
        assert agent.run(instruction="写", user_id="u", job_id="j") == "ok"


def test_setup_once_when_endpoint_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XSKILL_OTEL_ENDPOINT", "http://127.0.0.1:8873")
    from xskill.obs import tracing

    tracing.reset()
    hits = {"n": 0}

    def fake_setup():
        hits["n"] += 1
        return True

    monkeypatch.setattr(tracing, "setup", fake_setup)
    skill_dir, ctx = _ctx(tmp_path)
    agent = GenerateAgent(
        skill_dir=skill_dir, agno_agent_factory=_factory, llm_cfg={}, logs_dir=None,
    )
    with agent_tools.use_agent_tool_context(ctx):
        agent.run(instruction="写", user_id="u", job_id="j")
    assert hits["n"] == 1
