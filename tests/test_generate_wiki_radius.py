"""会话 wiki 只进 Generate，不进其他 agent，也不进客户端主依赖。"""
from __future__ import annotations

from pathlib import Path

from xskill.agents import agent_tools
from xskill.agents.generate_agent import GenerateAgent
from xskill.team.server.generate_jobs import _prepare_generate_wiki


def test_default_context_has_no_wiki_root():
    ctx = agent_tools.create_agent_tool_context()
    assert ctx.wiki_root is None


def test_snapshot_restore_keeps_wiki_root(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    ctx = agent_tools.create_agent_tool_context(
        skill_dir=tmp_path / "skill",
        wiki_root=wiki,
    )
    token = agent_tools.bind_agent_tool_context(ctx)
    try:
        snap = agent_tools.agent_tool_config.snapshot()
        assert snap["wiki_root"] == wiki
        agent_tools.agent_tool_config.restore(snap)
        assert agent_tools.current_agent_tool_context().wiki_root == wiki
    finally:
        agent_tools.reset_agent_tool_context(token)


def test_prepare_generate_wiki_seeds_pages(tmp_path: Path):
    root = _prepare_generate_wiki(
        {"job_id": "job1", "user_id": "alice"}, tmp_path,
    )
    expected = tmp_path / "agents" / "generate_agents" / "alice" / "wiki" / "job1"
    assert root == expected
    assert (root / "pages" / "survey.md").is_file()
    assert (root / "SCHEMA.md").is_file()


def test_other_agents_do_not_import_wiki_modules():
    skill_edit = Path("src/xskill/agents/skill_edit_agent.py").read_text(
        encoding="utf-8",
    )
    task_agent = Path("src/xskill/agents/task_agent.py").read_text(encoding="utf-8")
    scripting = Path("src/xskill/agents/skill_scripting_agent.py").read_text(
        encoding="utf-8",
    )
    factory = Path("src/xskill/agents/agno_factory.py").read_text(encoding="utf-8")
    for text in (skill_edit, task_agent, scripting, factory):
        assert "llm_wiki" not in text
        assert "traj_tools" not in text
        assert "wiki_write" not in text
        assert "traj_search" not in text


def test_generate_agent_registers_wiki_tools(tmp_path: Path):
    seen = {}

    def factory(*, instructions, tools, **kwargs):
        del instructions, kwargs
        seen["names"] = [getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools]

        class _Agent:
            def run(self, _msg):
                class _R:
                    content = "ok"
                return _R()

        return _Agent()

    agent = GenerateAgent(
        skill_dir=tmp_path,
        agno_agent_factory=factory,
        llm_cfg={},
        logs_dir=tmp_path,
    )
    agent.run(instruction="写一个 skill", user_id="u", job_id="j")
    names = set(seen["names"])
    assert {
        "traj_search",
        "traj_cards",
        "read_traj",
        "wiki_status",
        "wiki_read",
        "wiki_write",
        "wiki_search",
        "wiki_log",
        "commit_generate_main",
    } <= names


def test_generate_list_files_refuses_traj_tree(tmp_path: Path):
    traj_root = tmp_path / "team_trajectories" / "clients" / "cursor-local" / "sessions"
    traj_root.mkdir(parents=True)
    (traj_root / "traj_cursor_x_aaaaaaaa.md").write_text("x\n", encoding="utf-8")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    skill = tmp_path / "skill"
    skill.mkdir()
    ctx = agent_tools.create_agent_tool_context(
        skill_dir=skill,
        default_traj_root=tmp_path / "team_trajectories",
        extra_read_roots=(tmp_path / "team_trajectories",),
        generate_user_id="obs-user",
        wiki_root=wiki,
    )
    with agent_tools.use_agent_tool_context(ctx):
        refused = agent_tools.list_files.entrypoint(str(traj_root))
        skill_listing = agent_tools.list_files.entrypoint(str(skill))
    assert refused.startswith("error:")
    assert "traj_search" in refused
    assert "(empty)" in skill_listing or str(skill) in skill_listing


def test_compact_nudge_appends_wiki_hint():
    from agno.models.message import Message

    from xskill.agents.context_budget import ContextManager
    from xskill.agents.generate_agent import generate_compact_nudge
    from xskill.agents.llm_wiki import AFTER_COMPACT_EMPTY_HINT

    messages = [
        Message(role="system", content="you are a test agent"),
        Message(role="user", content="turn0 " + "u" * 4000),
    ]
    for index in range(8):
        role = "assistant" if index % 2 == 0 else "user"
        messages.append(Message(role=role, content=f"m{index} " + "x" * 4000))

    class _Resp:
        content = "ok"
        usage = None

    manager = ContextManager(
        200_000,
        compact_token_limit=1_000,
        compact_keep_recent_messages=2,
        compact_fn=lambda prompt: "## Model handoff summary\n\ndone\n",
    )
    invoke = manager.wrap(lambda _messages, **_kwargs: _Resp())
    with generate_compact_nudge():
        invoke(messages)
    assert any(
        AFTER_COMPACT_EMPTY_HINT in str(getattr(message, "content", ""))
        for message in messages
    )


def test_pyproject_wiki_adds_no_main_deps():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    deps_start = text.index("dependencies = [")
    extras_start = text.index("[project.optional-dependencies]")
    deps_block = text[deps_start:extras_start]
    assert "opentelemetry" not in deps_block
    assert "arize-phoenix" not in deps_block
    assert "chromadb" not in deps_block
    assert "llama-index" not in deps_block
