"""process_atom_task 集成单测（stub agno + 真 store + 真 candidates）"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.process import process_atom_task
from tests.test_atom_task_store import _FakeEmbed


def _seed_skill(skill_dir: Path, name: str):
    sd = skill_dir / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: stub\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8",
    )
    return sd


def _seed_atom(store: AtomTaskStore) -> AtomTask:
    atom = AtomTask(
        atom_id="atom_x_0001", traj_id="x",
        offset_start=0, offset_end=10,
        intent="修 django migration", summary="跑了 makemigrations",
        tags=["django"], used_skills=[], ux_score=7,
        pre_atom_id=None, post_atom_id=None,
        context_prefix="", raw_segment="",
    )
    store.save(atom)
    return atom


class _ClusterCallsAddTaskStub:
    """模拟 cluster agent：每次 run 调用 add_task_to_skill 给目标 skill 10 分。"""
    target_skill: str = "auto-skill"
    target_score: int = 10

    def __init__(self, *, instructions, tools):
        self.tools = tools

    def run(self, user_msg, **kw):
        # extract atom_id from user_msg
        import re
        m = re.search(r"atom_id:\s*(\S+)", user_msg)
        atom_id = m.group(1) if m else None
        for fn in self.tools:
            if getattr(fn, "__name__", "") == "new_skill_folder":
                fn(type(self).target_skill)
            if getattr(fn, "__name__", "") == "add_task_to_skill" and atom_id:
                fn(type(self).target_skill, atom_id, type(self).target_score)
        class _R: pass
        r = _R(); r.content = "stub-cluster-done"; return r


class _NoopAgnoStub:
    """SkillEditAgent 的 stub：不写文件，只确认被触发。"""
    invocations: list = []

    def __init__(self, *, instructions, tools):
        self.tools = tools

    def run(self, user_msg, **kw):
        type(self).invocations.append(user_msg[:80])
        class _R: pass
        r = _R(); r.content = "stub-edit-done"; return r


def _composite_factory(*, instructions, tools):
    """根据 sysprompt 头判断是 cluster 还是 edit agent，分发到对应 stub。"""
    head = instructions[0][:60] if instructions else ""
    if "TaskClusterAgent" in head:
        return _ClusterCallsAddTaskStub(instructions=instructions, tools=tools)
    if "SkillEditAgent" in head:
        return _NoopAgnoStub(instructions=instructions, tools=tools)
    raise RuntimeError(f"unknown agent role: {head!r}")


class TestProcessAtomTask:
    def test_cluster_then_edit_full_chain(self, tmp_path):
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        store_root = tmp_path / "cc-sessions"; store_root.mkdir()
        store = AtomTaskStore(root=store_root)
        atom = _seed_atom(store)
        store.rebuild_vector_index(_FakeEmbed())

        _NoopAgnoStub.invocations = []
        result = process_atom_task(
            atom_id=atom.atom_id,
            config={"llm": {"base_url": "test://", "model": "stub", "api_key": "k"}},
            skill_dir=skill_dir, store=store,
            embed_client=_FakeEmbed(),
            agno_agent_factory=_composite_factory,
        )
        assert result["action"] == "clustered"
        assert result["atom_id"] == "atom_x_0001"
        # cluster 创建 auto-skill 并打 10 分 → SkillEdit 触发
        assert "auto-skill" in result["edited_skills"]
        assert len(_NoopAgnoStub.invocations) >= 1
        # candidates.yml 中 atom 已 promoted
        from xskill import candidates as C
        data = C.load_candidates(skill_dir / "auto-skill")
        assert all(c.get("promoted") for c in data["candidates"])

    def test_no_skill_below_threshold_does_not_trigger_edit(self, tmp_path):
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        store_root = tmp_path / "cc-sessions"; store_root.mkdir()
        store = AtomTaskStore(root=store_root)
        atom = _seed_atom(store)
        store.rebuild_vector_index(_FakeEmbed())

        # 这次让 cluster 给 5 分（< threshold）
        class _LowScoreCluster(_ClusterCallsAddTaskStub):
            target_score = 5

        def _factory(*, instructions, tools):
            head = instructions[0][:60] if instructions else ""
            if "TaskClusterAgent" in head:
                return _LowScoreCluster(instructions=instructions, tools=tools)
            return _NoopAgnoStub(instructions=instructions, tools=tools)

        _NoopAgnoStub.invocations = []
        result = process_atom_task(
            atom_id=atom.atom_id,
            config={"llm": {"base_url": "test://", "model": "stub", "api_key": "k"}},
            skill_dir=skill_dir, store=store,
            embed_client=_FakeEmbed(),
            agno_agent_factory=_factory,
        )
        assert result["action"] == "clustered"
        assert result["edited_skills"] == []
        assert _NoopAgnoStub.invocations == []
