"""process_atom_task 单测（v2.1: cluster only, edit 独立扫描）"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.process import process_atom_task
from tests.test_atom_task_store import _FakeEmbed


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
    """模拟 cluster agent：每次 run 调用 add_task_to_skill 给目标 skill。"""
    target_skill: str = "auto-skill"
    target_score: int = 10

    def __init__(self, *, instructions, tools):
        self.tools = tools

    def run(self, user_msg, **kw):
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


def _cluster_factory(*, instructions, tools):
    return _ClusterCallsAddTaskStub(instructions=instructions, tools=tools)


class TestProcessAtomTask:
    def test_cluster_writes_candidates(self, tmp_path):
        """process_atom_task 只跑 cluster：写出 candidates，不再触发 edit。"""
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        store_root = tmp_path / "cc-sessions"; store_root.mkdir()
        store = AtomTaskStore(root=store_root)
        atom = _seed_atom(store)
        store.rebuild_vector_index(_FakeEmbed())

        result = process_atom_task(
            atom_id=atom.atom_id,
            config={"llm": {"base_url": "test://", "model": "stub", "api_key": "k"}},
            skill_dir=skill_dir, store=store,
            embed_client=_FakeEmbed(),
            agno_agent_factory=_cluster_factory,
        )
        assert result["action"] == "clustered"
        assert result["atom_id"] == "atom_x_0001"
        # Bug 1 修复：不再返回 edited_skills（edit 独立扫描）
        assert "edited_skills" not in result
        # cluster 写了 candidates
        from xskill import candidates as C
        data = C.load_candidates(skill_dir / "auto-skill")
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["atom_id"] == "atom_x_0001"
        assert data["candidates"][0]["weightscore_total"] == 10
        # 但 candidates 仍 promoted=false (edit 在 watcher 独立扫描时才跑)
        assert data["candidates"][0]["promoted"] is False

    def test_cluster_failure_does_not_corrupt_candidates(self, tmp_path):
        """Bug 1 关键回归：cluster 调 LLM 失败时，已写的 candidates 不被污染。

        独立 edit 扫描在 watcher 层，process_atom_task 失败不影响下一轮
        edit 触发——这是 Bug 1 修复的核心。
        """
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        store_root = tmp_path / "cc-sessions"; store_root.mkdir()
        store = AtomTaskStore(root=store_root)
        atom = _seed_atom(store)
        store.rebuild_vector_index(_FakeEmbed())

        # 模拟 cluster 抛异常（agno 本身可能不抛，但 stub 显式抛模拟极端情况）
        class _ThrowingClusterStub:
            def __init__(self, **kw): pass
            def run(self, msg, **kw):
                raise RuntimeError("LLM 402")

        def _throwing_factory(*, instructions, tools):
            return _ThrowingClusterStub(instructions=instructions, tools=tools)

        with pytest.raises(RuntimeError):
            process_atom_task(
                atom_id=atom.atom_id,
                config={"llm": {"base_url": "x", "model": "y", "api_key": "z"}},
                skill_dir=skill_dir, store=store,
                embed_client=_FakeEmbed(),
                agno_agent_factory=_throwing_factory,
            )
        # 即便抛异常，skill_dir 下不存在被污染的 candidates 文件
        # （新创建的 atom_id 也没 promoted=true）
        assert not (skill_dir / "auto-skill").exists()
