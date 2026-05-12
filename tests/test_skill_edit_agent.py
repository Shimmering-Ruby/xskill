"""SkillEditAgent：buffer 累计 ≥ 10 才触发；
**真的写出 SKILL.md** 才标 promoted（防止 agno 静默吞错误时污染 buffer）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill import candidates as C
from xskill.skill_edit_agent import SkillEditAgent


class _StubAgnoAgent:
    """默认 noop（不写文件，模拟 agno 静默吞 LLM 错误）；
    设置 ``writes_skill_md_with=...`` 让它写一份内容到 SKILL.md，模拟正常 agent。"""
    invoked = False
    writes_skill_md_with: str | None = None  # class-level 控制

    def __init__(self, *, instructions, tools):
        self.instructions = instructions
        self.tools = tools
        # 找到 write_file 工具，给"模拟写"用
        self._write_file = None
        for t in tools:
            if getattr(t, "__name__", "") == "write_file":
                self._write_file = t
                break

    def run(self, user_msg, **kw):
        type(self).invoked = True
        type(self).user_msg = user_msg
        # 模拟 agent 调 write_file 写出 SKILL.md
        if type(self).writes_skill_md_with is not None and self._write_file is not None:
            # 从 user_msg 抓 skill_dir 路径
            import re
            m = re.search(r"目标 SKILL\.md 路径:\s*(\S+)", user_msg)
            if m:
                self._write_file(m.group(1), type(self).writes_skill_md_with)
        class _R: pass
        r = _R(); r.content = "done"
        return r


def _stub_factory(*, instructions, tools):
    return _StubAgnoAgent(instructions=instructions, tools=tools)


@pytest.fixture(autouse=True)
def _init_v2_ctx(tmp_path):
    """每个 case 默认把 _ctx_v2 指到 tmp_path，让 write_file 能正常工作。"""
    from xskill import skill_tools as ST
    from xskill.atom_task import AtomTaskStore
    ST.init_context_v2(
        skill_dir=tmp_path / "skill",
        store=AtomTaskStore(root=tmp_path / "store"),
        embed_client=None,
        traj_root=tmp_path / "store",
    )
    yield
    # 复位 class state
    _StubAgnoAgent.invoked = False
    _StubAgnoAgent.writes_skill_md_with = None


# ────────────────────────────────────────────────────────────────────
# 触发门槛
# ────────────────────────────────────────────────────────────────────

class TestThresholdGate:
    def test_below_threshold_does_not_trigger(self, tmp_path):
        skill_dir = tmp_path / "skill" / "my-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 5)
        C.save_candidates(skill_dir, data)

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is False
        assert _StubAgnoAgent.invoked is False

    def test_at_threshold_triggers_and_marks_promoted_when_agent_writes(self, tmp_path):
        skill_dir = tmp_path / "skill" / "my-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 7)
        data, _ = C.add_atom_contribution(data, "atom_b", 4)
        C.save_candidates(skill_dir, data)

        # 让 stub agent 真写一份 SKILL.md
        _StubAgnoAgent.writes_skill_md_with = (
            "---\nname: my-skill\ndescription: stub\nmetadata:\n  version: 1\n---\n# body\n"
        )

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True
        assert _StubAgnoAgent.invoked is True
        # SKILL.md 真写了
        assert (skill_dir / "SKILL.md").is_file()
        # candidates 全部标 promoted
        data2 = C.load_candidates(skill_dir)
        assert all(c.get("promoted") for c in data2["candidates"])

    def test_already_promoted_ignored(self, tmp_path):
        """已 promoted 的不应让 maybe_run 再次触发。"""
        skill_dir = tmp_path / "skill" / "z-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_old", 10)
        data["candidates"][0]["promoted"] = True
        data, _ = C.add_atom_contribution(data, "atom_new", 4)
        C.save_candidates(skill_dir, data)

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is False
        assert _StubAgnoAgent.invoked is False


# ────────────────────────────────────────────────────────────────────
# 真实落盘判据（Bug 2 修复）：agent 没写 SKILL.md → 不标 promoted
# ────────────────────────────────────────────────────────────────────

class TestRequiresActualSkillMdWrite:
    """agno 静默吞 LLM 错误（agent.run 不抛但没写文件）时，候选不能被
    错误标 promoted——必须保留 promoted=false 下一轮 watcher scan 重试。"""

    def test_agent_returns_without_writing_skill_md_keeps_promoted_false(self, tmp_path):
        skill_dir = tmp_path / "skill" / "noop-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 10)
        C.save_candidates(skill_dir, data)

        # stub agent 默认 noop——不写 SKILL.md
        _StubAgnoAgent.writes_skill_md_with = None

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        result = agent.maybe_run()
        assert result is False, "agent 没写 SKILL.md，maybe_run 应返回 False"
        # 关键：candidates 仍保留 promoted=false，让下一轮 scan 重试
        data2 = C.load_candidates(skill_dir)
        assert all(not c.get("promoted") for c in data2["candidates"]), \
            "agent 没写 SKILL.md 时不能错误标 promoted"

    def test_agent_raises_keeps_promoted_false(self, tmp_path):
        """LLM 真抛异常时也不标 promoted。"""
        skill_dir = tmp_path / "skill" / "err-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 10)
        C.save_candidates(skill_dir, data)

        class _ThrowingAgent:
            def __init__(self, **kw): pass
            def run(self, msg, **kw):
                raise RuntimeError("LLM 402 余额不足")

        def _throwing_factory(*, instructions, tools):
            return _ThrowingAgent(instructions=instructions, tools=tools)

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_throwing_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        result = agent.maybe_run()
        assert result is False
        data2 = C.load_candidates(skill_dir)
        assert all(not c.get("promoted") for c in data2["candidates"])

    def test_retry_on_next_run_after_failed_attempt(self, tmp_path):
        """先一次失败（不写），下次成功（写）→ 第二次成功后正常 promoted。"""
        skill_dir = tmp_path / "skill" / "retry-skill"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 10)
        C.save_candidates(skill_dir, data)

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_stub_factory,
            llm_cfg={}, traj_root=tmp_path,
        )
        # 第一次：agent noop 不写
        _StubAgnoAgent.writes_skill_md_with = None
        assert agent.maybe_run() is False
        data1 = C.load_candidates(skill_dir)
        assert all(not c.get("promoted") for c in data1["candidates"])

        # 第二次：agent 写
        _StubAgnoAgent.writes_skill_md_with = (
            "---\nname: retry-skill\n---\n# body\n"
        )
        assert agent.maybe_run() is True
        data2 = C.load_candidates(skill_dir)
        assert all(c.get("promoted") for c in data2["candidates"])
