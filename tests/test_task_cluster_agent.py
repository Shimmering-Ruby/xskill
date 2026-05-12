"""TaskClusterAgent + build_skill_catalog_block 单测"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.task_cluster_agent import TaskClusterAgent, build_skill_catalog_block


def _make_skill(skill_dir: Path, name: str, desc: str):
    sd = skill_dir / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8",
    )


class TestSkillCatalogBudget:
    def test_short_list_keeps_full_descriptions(self, tmp_path):
        skill_dir = tmp_path / "skill"
        for n, d in [("a-skill", "deals with A"), ("b-skill", "deals with B")]:
            _make_skill(skill_dir, n, d)
        block = build_skill_catalog_block(skill_dir, max_chars=20000)
        assert "a-skill: deals with A" in block
        assert "b-skill: deals with B" in block

    def test_overflow_truncates_descriptions(self, tmp_path):
        skill_dir = tmp_path / "skill"
        # 100 skills, desc 100 chars 各，约 10k chars desc；预算 4000 强制截断
        for i in range(100):
            _make_skill(skill_dir, f"s-{i:03d}", "x" * 100)
        block = build_skill_catalog_block(skill_dir, max_chars=4000)
        for i in range(100):
            assert f"s-{i:03d}" in block
        # 不允许完整 100 字 desc
        assert "x" * 100 not in block

    def test_extreme_overflow_drops_all_descriptions(self, tmp_path):
        skill_dir = tmp_path / "skill"
        # 500 skills, 200 字 desc → 100k 字 desc，预算 5000 → per_desc 远 < 75
        for i in range(500):
            _make_skill(skill_dir, f"big-{i:04d}", "y" * 200)
        block = build_skill_catalog_block(skill_dir, max_chars=5000)
        for i in range(500):
            assert f"big-{i:04d}" in block
        # 该模式下不留 desc
        assert "yyyy" not in block

    def test_empty_skill_dir_returns_placeholder(self, tmp_path):
        skill_dir = tmp_path / "empty_skill"
        skill_dir.mkdir()
        block = build_skill_catalog_block(skill_dir, max_chars=10000)
        assert "no skills" in block.lower()


class TestClusterAgentPrompt:
    def test_prompt_includes_atom_id_and_skill_catalog(self, tmp_path):
        skill_dir = tmp_path / "skill"
        _make_skill(skill_dir, "fix-django", "django migration 修复")
        store = AtomTaskStore(root=tmp_path / "cc-sessions")
        atom = AtomTask(
            atom_id="atom_t_0001", traj_id="t",
            offset_start=0, offset_end=10,
            intent="修 django", summary="跑了 makemigrations",
            tags=["django"], used_skills=[], ux_score=7,
            pre_atom_id=None, post_atom_id=None,
            context_prefix="", raw_segment="",
        )
        store.save(atom)

        captured = {}

        class _StubAgnoAgent:
            def __init__(self, *, instructions, tools):
                captured["instructions"] = instructions
                captured["tools"] = tools

            def run(self, user_msg, **kw):
                captured["user_msg"] = user_msg
                class _R: pass
                r = _R(); r.content = ""
                return r

        def _factory(*, instructions, tools):
            return _StubAgnoAgent(instructions=instructions, tools=tools)

        agent = TaskClusterAgent(
            skill_dir=skill_dir, store=store,
            agno_agent_factory=_factory, llm_cfg={}, tools=[],
        )
        agent.process(atom)
        # User msg contains atom_id + intent + summary
        assert "atom_t_0001" in captured["user_msg"]
        assert "跑了 makemigrations" in captured["user_msg"]
        # System prompt含 skill 路由表 + 严格分档表
        sys_text = captured["instructions"][0]
        assert "fix-django" in sys_text
        assert "weightscore" in sys_text.lower() or "权重" in sys_text
        # 严格分档表关键词
        assert "10" in sys_text and "立即触发" in sys_text
