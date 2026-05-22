"""TaskAgent —— 行号锚定切分:stub LLM 验证首轮 / 增量 / 严格解析

v0.5.0a4 起,TaskAgent 不再让 LLM 裸眼猜 char offset。预处理给轨迹里每个
``## User`` 标题行打 ``[L<行号>]`` 标记,LLM 只报每个 atom 的起始行号
``start_line``(半开区间,只报起点),char offset 由程序按行号精确回填。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.agents.task_agent import TaskAgent, _annotate_user_lines


class _StubLLM:
    """Deterministic LLM stub:按调用顺序返回 canned 响应。"""
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


class _AutoSplitLLM:
    """动态 split stub:扫 prompt 里的 ``[L<n>]`` 标记,每个 ``## User`` 行产
    一个 atom。与轨迹的具体行偏移解耦——任何含 ``## User`` 的轨迹都能直接用,
    不必为每条轨迹手算行号。watcher 等下游测试用它喂 TaskAgent。"""
    model = "stub-autosplit"

    def __init__(self):
        self.calls: list[str] = []
        self.systems: list[str] = []

    def chat(self, prompt: str, system: str = "") -> str:
        self.calls.append(prompt)
        self.systems.append(system)
        marks = [int(n) for n in re.findall(r"\[L(\d+)\]", prompt)]
        if not marks:
            raise RuntimeError("_AutoSplitLLM: prompt 里没有 [L] 标记")
        atoms = "\n".join(
            f"<atom><start_line>{ln}</start_line>"
            f"<intent>stub intent</intent>"
            f"<summary>stub summary</summary>"
            f"<tags><tag>stub</tag></tags>"
            f"<used_skills></used_skills>"
            f"<ux_score>7</ux_score></atom>"
            for ln in marks
        )
        return f"<atoms>\n{atoms}\n</atoms>"


# 真实形态的小轨迹:`## User` 在第 5、17 行。
_TRAJ_MD = """## System

You are Claude Code.

## User

Deploy xquiz to 1717.

## Assistant

Cloning...

## Tool Call: Bash

git clone https://example.com/xquiz

## User

Now redesign the frontend.

## Assistant

Editing CSS...
"""

# 匹配 _TRAJ_MD 的 canned 响应:atom 起点 = 第 5 行、第 17 行。
_SPLIT_XML = """<atoms>
<atom>
  <start_line>5</start_line>
  <intent>部署 xquiz 到 1717</intent>
  <summary>用户要求克隆并部署 xquiz；agent 开始 git clone</summary>
  <tags><tag>deploy</tag></tags>
  <used_skills></used_skills>
  <ux_score>7</ux_score>
</atom>
<atom>
  <start_line>17</start_line>
  <intent>重新设计前端</intent>
  <summary>用户切到前端美化任务；agent 编辑 CSS</summary>
  <tags><tag>frontend</tag></tags>
  <used_skills><skill>frontend-design</skill></used_skills>
  <ux_score>8</ux_score>
</atom>
</atoms>"""


def _line_offset(text: str, line_no: int) -> int:
    """返回 text 中第 line_no 行(1-based)起始的字符 offset。"""
    off = 0
    for i, line in enumerate(text.splitlines(keepends=True), start=1):
        if i == line_no:
            return off
        off += len(line)
    raise AssertionError(f"line {line_no} not in text")


# ────────────────────────────────────────────────────────────────────
# _annotate_user_lines 单元测试
# ────────────────────────────────────────────────────────────────────

class TestAnnotateUserLines:
    def test_tags_only_user_headers_with_full_file_line_numbers(self):
        annotated, linemap = _annotate_user_lines(_TRAJ_MD, first_line_no=1,
                                                  base_char=0)
        # 只有 ## User 标题行被打标
        assert "[L5] ## User" in annotated
        assert "[L17] ## User" in annotated
        # ## Assistant / ## Tool Call / ## System 不打标
        assert "] ## Assistant" not in annotated
        assert "] ## Tool Call" not in annotated
        assert "] ## System" not in annotated
        # linemap:行号 → 原文 char offset
        assert set(linemap) == {5, 17}
        assert linemap[5] == _line_offset(_TRAJ_MD, 5)
        assert linemap[17] == _line_offset(_TRAJ_MD, 17)

    def test_offsets_and_line_numbers_shift_with_base(self):
        """delta 不是从文件头开始时,行号和 char offset 都按 base 平移。"""
        base = _line_offset(_TRAJ_MD, 9)  # 从第 9 行(## Assistant)起
        delta = _TRAJ_MD[base:]
        annotated, linemap = _annotate_user_lines(
            delta, first_line_no=9, base_char=base)
        assert set(linemap) == {17}
        assert linemap[17] == _line_offset(_TRAJ_MD, 17)
        assert "[L17] ## User" in annotated

    def test_content_hash_headers_not_mistaken_for_user(self):
        """用户正文里的 markdown 二级标题不能被误判成 ## User。"""
        md = "## User\n\n看这个 ## User 提到的步骤\n## Step 1 做点啥\n"
        annotated, linemap = _annotate_user_lines(md, first_line_no=1,
                                                  base_char=0)
        assert set(linemap) == {1}            # 只有第 1 行真标题
        assert annotated.count("[L") == 1


# ────────────────────────────────────────────────────────────────────
# 首轮:两个 atom + 精确行锚定 offset
# ────────────────────────────────────────────────────────────────────

class TestFirstRun:
    def test_persists_two_atoms_with_line_anchored_offsets(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_x.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        stub = _StubLLM([_SPLIT_XML])
        atoms = TaskAgent(llm=stub, store=store).run(
            traj_id="traj_x", traj_path=traj_path)

        assert len(atoms) == 2
        l17 = _line_offset(_TRAJ_MD, 17)
        # 首 atom 从 offset_floor(0)起,终点 = 下一 atom 起点(第 17 行)
        assert atoms[0].offset_start == 0
        assert atoms[0].offset_end == l17
        # 次 atom 起点精确锚定到第 17 行,终点 = 文件末
        assert atoms[1].offset_start == l17
        assert atoms[1].offset_end == len(_TRAJ_MD)
        # 半开衔接:无重叠无缝隙
        assert atoms[0].offset_end == atoms[1].offset_start
        # 链表
        assert atoms[0].pre_atom_id is None
        assert atoms[0].post_atom_id == atoms[1].atom_id
        assert atoms[1].pre_atom_id == atoms[0].atom_id
        # 富化字段仍产出
        assert atoms[1].used_skills == ["frontend-design"]
        assert atoms[1].ux_score == 8
        # raw_segment 按精确 offset 切回
        assert atoms[0].raw_segment == _TRAJ_MD[0:l17]
        assert atoms[1].raw_segment == _TRAJ_MD[l17:]
        # 落盘
        assert (traj_dir / "traj_x" / "tasks" /
                f"{atoms[0].atom_id}.json").is_file()

    def test_prompt_carries_line_markers(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_p.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        stub = _StubLLM([_SPLIT_XML])
        TaskAgent(llm=stub, store=store).run(traj_id="traj_p", traj_path=traj_path)
        # 喂给 LLM 的 prompt 必须带行号标记
        assert "[L5] ## User" in stub.calls[0]
        assert "[L17] ## User" in stub.calls[0]


# ────────────────────────────────────────────────────────────────────
# 增量轮:append 新 user turn → 续切
# ────────────────────────────────────────────────────────────────────

class TestIncrementalRun:
    def test_second_run_splits_appended_turn(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_y.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        TaskAgent(llm=_StubLLM([_SPLIT_XML]), store=store).run(
            traj_id="traj_y", traj_path=traj_path)

        # append 第三个 user turn
        appended = "\n## User\n\nAdd tests.\n\n## Assistant\n\nWriting pytest...\n"
        full = _TRAJ_MD + appended
        traj_path.write_text(full, encoding="utf-8")
        new_user_line = full[:full.rindex("## User")].count("\n") + 1

        delta_xml = f"""<atoms>
<atom>
  <start_line>{new_user_line}</start_line>
  <intent>补单元测试</intent>
  <summary>用户要求加测试；agent 写 pytest</summary>
  <tags><tag>testing</tag></tags>
  <used_skills></used_skills>
  <ux_score>7</ux_score>
</atom>
</atoms>"""
        stub = _StubLLM([delta_xml])
        new_atoms = TaskAgent(llm=stub, store=store).run(
            traj_id="traj_y", traj_path=traj_path)

        assert len(new_atoms) == 1
        # 增量 atom 起点 = resume offset(上一轮窗口末),终点到文件末
        assert new_atoms[0].offset_start == len(_TRAJ_MD)
        assert new_atoms[0].offset_end == len(full)
        # 与上一轮末 atom 链接
        assert new_atoms[0].pre_atom_id is not None
        assert new_atoms[0].pre_atom_id.startswith("atom_traj_y_")
        prior = store.load(new_atoms[0].pre_atom_id)
        assert prior.post_atom_id == new_atoms[0].atom_id
        assert len(store.list_by_traj("traj_y")) == 3
        # 增量 prompt 用的是全文件行号
        assert f"[L{new_user_line}] ## User" in stub.calls[0]

    def test_no_new_content_returns_empty_no_llm_call(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_z.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        TaskAgent(llm=_StubLLM([_SPLIT_XML]), store=store).run(
            traj_id="traj_z", traj_path=traj_path)
        # 文件没变,再跑一次 → 空,且不碰 LLM
        stub = _StubLLM([])
        result = TaskAgent(llm=stub, store=store).run(
            traj_id="traj_z", traj_path=traj_path)
        assert result == []
        assert stub.calls == []

    def test_window_without_user_header_returns_empty_no_llm_call(self, tmp_path):
        """窗口内没有 ## User → 没有可切分的新 atom,直接返回,不调 LLM。"""
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_n.md"
        traj_path.write_text("## System\n\njust a system note, no user turn\n",
                             encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        stub = _StubLLM([])
        result = TaskAgent(llm=stub, store=store).run(
            traj_id="traj_n", traj_path=traj_path)
        assert result == []
        assert stub.calls == []


# ────────────────────────────────────────────────────────────────────
# 严格解析:非法 start_line / 非单调 / 缺字段 → raise,不写 fallback
# ────────────────────────────────────────────────────────────────────

class TestStrictParsing:
    def _setup(self, tmp_path: Path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_e.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        return traj_path, store

    def test_start_line_not_a_user_header_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        # 第 9 行是 ## Assistant,不是被标记的 ## User 行
        bad = """<atoms><atom>
  <start_line>9</start_line>
  <intent>i</intent><summary>s</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score>
</atom></atoms>"""
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="start_line"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_non_monotonic_start_line_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        bad = """<atoms>
<atom><start_line>17</start_line><intent>i1</intent><summary>s1</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score></atom>
<atom><start_line>5</start_line><intent>i2</intent><summary>s2</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score></atom>
</atoms>"""
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="start_line"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_first_atom_not_first_user_line_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        # 窗口里第一个 ## User 是第 5 行,首 atom 却从第 17 行起 → 漏了内容
        bad = """<atoms><atom>
  <start_line>17</start_line>
  <intent>i</intent><summary>s</summary>
  <tags></tags><used_skills></used_skills><ux_score>5</ux_score>
</atom></atoms>"""
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="first"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_missing_required_field_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        bad = """<atoms><atom>
  <start_line>5</start_line>
  <intent>i</intent>
</atom></atoms>"""  # 缺 summary
        agent = TaskAgent(llm=_StubLLM([bad]), store=store)
        with pytest.raises(ValueError, match="malformed"):
            agent.run(traj_id="traj_e", traj_path=traj_path)

    def test_empty_atoms_raises(self, tmp_path):
        traj_path, store = self._setup(tmp_path)
        agent = TaskAgent(llm=_StubLLM(["<atoms></atoms>"]), store=store)
        with pytest.raises(ValueError, match="no parseable atoms"):
            agent.run(traj_id="traj_e", traj_path=traj_path)


# ────────────────────────────────────────────────────────────────────
# SYSTEM_PROMPT 仍含严格分档表 + 新协议说明
# ────────────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_prompt_contains_strict_rubric_and_protocol(self, tmp_path):
        traj_dir = tmp_path / "cc-sessions"
        traj_dir.mkdir()
        traj_path = traj_dir / "traj_p.md"
        traj_path.write_text(_TRAJ_MD, encoding="utf-8")
        store = AtomTaskStore(root=traj_dir)
        stub = _StubLLM([_SPLIT_XML])
        TaskAgent(llm=stub, store=store).run(traj_id="traj_p", traj_path=traj_path)
        system_msg = stub.systems[0]
        assert "10 一次到位" in system_msg
        assert "用户意图切换" in system_msg
        assert "ux_score" in system_msg
        # 新协议关键词
        assert "start_line" in system_msg
        assert "[L" in system_msg
