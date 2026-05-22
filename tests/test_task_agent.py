"""TaskAgent —— stub LLM 验证首轮拆分 + 增量拆分行为"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.pipeline.atom import AtomTaskStore
from xskill.agents.task_agent import TaskAgent


class _StubLLM:
    """Deterministic LLM stub: 按调用顺序返回 canned 响应。"""
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.systems: list[str] = []
        self.model = "stub"

    def chat(self, prompt: str, system: str = "") -> str:
        self.calls.append(prompt)
        self.systems.append(system)
        if not self.responses:
            raise RuntimeError("stub exhausted")
        return self.responses.pop(0)


_TRAJ_MD = """## System

You are Claude Code.

## User

Deploy xquiz to 1717.

## Assistant

Cloning...

## Tool Call: Bash
git clone ...

## User

Now redesign frontend.

## Assistant

Editing CSS...
"""


_SPLIT_XML = """<atoms>
<atom>
  <offset_start>20</offset_start>
  <offset_end>80</offset_end>
  <intent>部署 xquiz 到 1717</intent>
  <summary>用户要求克隆并部署 xquiz；agent 开始 git clone</summary>
  <tags><tag>deploy</tag></tags>
  <used_skills></used_skills>
  <ux_score>7</ux_score>
</atom>
<atom>
  <offset_start>80</offset_start>
  <offset_end>200</offset_end>
  <intent>重新设计前端</intent>
  <summary>用户切到前端美化任务；agent 编辑 CSS</summary>
  <tags><tag>frontend</tag></tags>
  <used_skills><skill>frontend-design</skill></used_skills>
  <ux_score>8</ux_score>
</atom>
</atoms>"""


# ────────────────────────────────────────────────────────────────────
# 首轮：两个 atom + 链接正确
# ────────────────────────────────────────────────────────────────────

class TestFirstRun:
    def test_persists_two_atoms_with_linked_pre_post(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_x.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        agent = TaskAgent(llm=_StubLLM([_SPLIT_XML]), store=store)
        atoms = agent.run(traj_id="traj_x", traj_path=traj_path)

        assert len(atoms) == 2
        assert atoms[0].pre_atom_id is None
        assert atoms[0].post_atom_id == atoms[1].atom_id
        assert atoms[1].pre_atom_id == atoms[0].atom_id
        assert atoms[1].post_atom_id is None
        assert atoms[1].used_skills == ["frontend-design"]
        assert atoms[1].ux_score == 8
        # 落盘
        assert (traj_dir / "traj_x" / "tasks" /
                f"{atoms[0].atom_id}.json").is_file()
        # raw_segment 从 offset 段抓回（≠ 空字符串）
        assert atoms[0].raw_segment == _TRAJ_MD[20:80]
        assert atoms[1].raw_segment == _TRAJ_MD[80:200]


# ────────────────────────────────────────────────────────────────────
# 增量轮：只发 delta + 衔接 prior atom 摘要
# ────────────────────────────────────────────────────────────────────

class TestIncrementalRun:
    def test_second_run_only_adds_delta_and_includes_prior_summary(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_y.md"
        # 用更长的 traj 让首轮 offset_end 远超 200，下一轮 prompt 必走省略路径
        long_traj = "## System\n" + ("filler line for padding offset.\n" * 80)
        traj_path.write_text(long_traj, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)

        # 首轮（覆盖到约 1500 字符）
        first_xml = """<atoms>
<atom>
  <offset_start>10</offset_start>
  <offset_end>800</offset_end>
  <intent>前半段任务</intent>
  <summary>用户切到前端美化任务；agent 编辑 CSS</summary>
  <tags><tag>frontend</tag></tags>
  <used_skills></used_skills>
  <ux_score>7</ux_score>
</atom>
</atoms>"""
        TaskAgent(llm=_StubLLM([first_xml]), store=store).run(
            traj_id="traj_y", traj_path=traj_path,
        )
        # 追加内容
        traj_path.write_text(long_traj + "\n## User\nNext task.\n", encoding="utf-8")

        stub_delta = """<atoms>
<atom>
  <offset_start>800</offset_start>
  <offset_end>1500</offset_end>
  <intent>下一项</intent>
  <summary>新增 turn 的简短任务</summary>
  <tags><tag>misc</tag></tags>
  <used_skills></used_skills>
  <ux_score>6</ux_score>
</atom>
</atoms>"""
        stub = _StubLLM([stub_delta])
        agent = TaskAgent(llm=stub, store=store)
        new_atoms = agent.run(traj_id="traj_y", traj_path=traj_path)

        assert len(new_atoms) == 1
        sent_prompt = stub.calls[0]
        # 含上一段 atom 衔接摘要
        assert "上一段 AtomTask 摘要" in sent_prompt
        # 包含 prior summary 内容
        assert "用户切到前端美化任务" in sent_prompt
        # 不发整篇 traj：用 context_prefix 省略
        assert "[省略" in sent_prompt
        # 新 atom 的 pre_atom_id 指向旧的最后一个
        assert new_atoms[0].pre_atom_id is not None
        assert new_atoms[0].pre_atom_id.startswith("atom_traj_y_")
        # 旧 atom 的 post_atom_id 已被回写为新 atom 的 id
        prior = store.load(new_atoms[0].pre_atom_id)
        assert prior.post_atom_id == new_atoms[0].atom_id
        # 全 store 现在 2 条（首轮 1 + 增量 1）
        assert len(store.list_by_traj("traj_y")) == 2

    def test_offset_already_at_end_returns_empty_no_llm_call(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_z.md"
        traj_path.write_text("hello", encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        # 手动塞一个已覆盖整篇的 atom
        from xskill.pipeline.atom import AtomTask
        store.save(AtomTask(
            atom_id="atom_traj_z_0001", traj_id="traj_z",
            offset_start=0, offset_end=len("hello"),
            intent="i", summary="s", tags=[], used_skills=[],
            ux_score=None, pre_atom_id=None, post_atom_id=None,
            context_prefix="", raw_segment="hello",
        ))
        stub = _StubLLM([])  # 不该被调用
        agent = TaskAgent(llm=stub, store=store)
        result = agent.run(traj_id="traj_z", traj_path=traj_path)
        assert result == []
        assert stub.calls == []  # 关键：完全没碰 LLM


# ────────────────────────────────────────────────────────────────────
# 错误路径：offset 不单调 / 字段缺失 / 空响应 —— 直接 raise，不写 fallback
# ────────────────────────────────────────────────────────────────────

class TestStrictParsing:
    def _setup(self, tmp_path: Path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_e.md"
        traj_path.write_text("0123456789" * 30, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        return traj_path, store

    def test_non_monotonic_offset_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        bad = """<atoms>
<atom>
  <offset_start>50</offset_start>
  <offset_end>100</offset_end>
  <intent>i1</intent><summary>s1</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score>
</atom>
<atom>
  <offset_start>30</offset_start>
  <offset_end>80</offset_end>
  <intent>i2</intent><summary>s2</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score>
</atom>
</atoms>"""
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="offset_start"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_missing_required_field_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        bad = """<atoms><atom>
  <offset_start>0</offset_start>
  <offset_end>10</offset_end>
  <intent>i</intent>
</atom></atoms>"""  # missing summary
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="malformed"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_empty_atoms_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        agent = TaskAgent(llm=_StubLLM(["<atoms></atoms>"]), store=store)
        with pytest.raises(ValueError, match="no parseable atoms"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_non_positive_span_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        bad = """<atoms><atom>
  <offset_start>50</offset_start>
  <offset_end>50</offset_end>
  <intent>i</intent><summary>s</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score>
</atom></atoms>"""
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="non-positive"):
            agent.run(traj_id="traj_e", traj_path=traj_path)


# ────────────────────────────────────────────────────────────────────
# SYSTEM_PROMPT 含严格分档表
# ────────────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_prompt_contains_strict_rubric(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_p.md"
        traj_path.write_text("hi" * 100, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        stub = _StubLLM([_SPLIT_XML])
        agent = TaskAgent(llm=stub, store=store)
        try:
            agent.run(traj_id="traj_p", traj_path=traj_path)
        except ValueError:
            pass  # XML 中 offset 跟实际文本长度对不上没关系，我们只看 system msg
        system_msg = stub.systems[0]
        # 严格分档表关键字
        assert "10 一次到位" in system_msg
        assert "用户意图切换" in system_msg
        assert "ux_score" in system_msg
        # used_skills 信号
        assert "used_skills" in system_msg
