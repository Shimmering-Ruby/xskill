"""SkillEditAgent：buffer 累计 ≥ 10 才触发；触发后 pending 全标 promoted"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill import candidates as C
from xskill.skill_edit_agent import SkillEditAgent


class _StubAgnoAgent:
    """Captures user_msg + tools list; agent.run is a no-op."""
    invoked = False

    def __init__(self, *, instructions, tools):
        self.instructions = instructions
        self.tools = tools

    def run(self, user_msg, **kw):
        type(self).invoked = True
        type(self).user_msg = user_msg
        class _R: pass
        r = _R(); r.content = "done"
        return r


def _stub_factory(*, instructions, tools):
    return _StubAgnoAgent(instructions=instructions, tools=tools)


class TestThresholdGate:
    def test_below_threshold_does_not_trigger(self, tmp_path):
        skill_dir = tmp_path / "skill" / "my-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 5)
        C.save_candidates(skill_dir, data)

        _StubAgnoAgent.invoked = False
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is False
        assert _StubAgnoAgent.invoked is False

    def test_at_threshold_triggers_and_marks_promoted(self, tmp_path):
        skill_dir = tmp_path / "skill" / "my-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 7)
        data, _ = C.add_atom_contribution(data, "atom_b", 4)
        C.save_candidates(skill_dir, data)

        _StubAgnoAgent.invoked = False
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True
        assert _StubAgnoAgent.invoked is True
        # 触发后全部标 promoted
        data2 = C.load_candidates(skill_dir)
        assert all(c.get("promoted") for c in data2["candidates"])

    def test_user_msg_lists_atoms_by_score_descending(self, tmp_path):
        skill_dir = tmp_path / "skill" / "y-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_low", 3)
        data, _ = C.add_atom_contribution(data, "atom_high", 8)
        C.save_candidates(skill_dir, data)

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        agent.maybe_run()
        msg = _StubAgnoAgent.user_msg
        # high 在前
        high_idx = msg.find("atom_high")
        low_idx = msg.find("atom_low")
        assert high_idx >= 0 and low_idx > high_idx

    def test_already_promoted_ignored(self, tmp_path):
        """已 promoted 的不应让 maybe_run 再次触发。"""
        skill_dir = tmp_path / "skill" / "z-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_old", 10)
        data["candidates"][0]["promoted"] = True
        data, _ = C.add_atom_contribution(data, "atom_new", 4)
        C.save_candidates(skill_dir, data)

        _StubAgnoAgent.invoked = False
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is False
        assert _StubAgnoAgent.invoked is False
