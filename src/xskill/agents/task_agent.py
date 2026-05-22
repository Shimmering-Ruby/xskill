"""TaskAgent —— 从 trajectory.md 增量拆分 AtomTask
==================================================

输入：一条 ``traj.md`` + ``AtomTaskStore``
输出：把 LLM 拆分得到的 AtomTask 落盘到 store，前后 atom 链表化（含与
``store.last_atom_id()`` 的衔接）。

设计要点
========

1. **切分边界 cut signal 只考虑"用户意图切换"**。不引入 tool 切换 / 代码块
   出现 / 子目录变化等启发——避免过度拆分把同一 Task 切碎。提示词里"引导"
   模型留意 SKILL 工具完整调用过程，但**不作为硬性切分点**。

2. **增量提取**：watcher 每次扫到 traj 变化时调 ``run()``。
   - 若 store 已有 atom：``offset_start = store.last_offset(traj_id)``，
     prompt 附"上一段 AtomTask 摘要"作为衔接 + 头 200 字 + ``[省略 N 字符]``
     占位，让模型只看 delta。
   - 若新 traj：``offset_start=0``，``context_prefix = text[:0]``。

3. **不写 fallback**（CLAUDE.md 第 1 条）。LLM 返回不合法 / offset 非单调 /
   字段缺失 → 直接 ``raise ValueError``，让 watcher 转 error 重试。

4. **ux_score 严格分档表**: SYSTEM_PROMPT 内列 10/9/.../1 每档语义；
   ``used_skills`` 非空时降档/起步规则明列。永远质量驱动，不按"做了多少
   事/步数"打分。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from xskill.pipeline.atom import AtomTask, AtomTaskStore

logger = logging.getLogger("xskill.task_agent")


SYSTEM_PROMPT = """你是 AtomTask 拆分员。给你一段 agent 与用户的对话轨迹（markdown），
你的任务是按"用户意图切换"切成 1~N 个 AtomTask。每个 AtomTask 是 1~10 轮 chat-turn
的最小完整工作单元——可以被独立检索、被独立提炼成 skill 的最小素材。

切分原则（按优先级）
====================
1. 只在**用户意图切换**处切。"意图切换"指用户从一个目标/任务/问题转到另一个，例如：
   - "再帮我改一下前端" → 前后是两个 atom
   - "另外/接下来/对了" 这类衔接词后跟新动作 → 切
   - 同一目标的多轮调整（澄清、修正、加细节）→ **不切**，留在同一 atom
2. **不要**因为 tool 切换、代码块出现、子目录变化等结构性事件而切——避免过度拆分。
3. 如果一段轨迹只完成一件事，输出 1 个 atom 即可。不要硬凑数量。
4. 可以适当关注 Skill 工具的完整使用过程（"## Tool Call: Skill" 之类），把整次
   skill 调用包含在它所属的 atom 里——但**不作为硬性切分点**。

字段输出（XML，注意 offset 是该 atom 在原文中的字符 offset，半开区间）
======================================================================
- offset_start / offset_end（整数，字符数；offset_end > offset_start；多个 atom
  之间 offset_start 必须单调递增）
- intent（≤40 字，目标）
- summary（≤200 字，复盘：根因、关键动作、产出、验证）
- tags（3-5 个，小写下划线）
- used_skills（这段对话里 agent 实际触发了哪些 skill 名；没有就空）
- ux_score（1~10 整数；只评这段 atom 的用户体验，不评整条 traj）

ux_score 严格分档表
====================
**永远不要凭"做了多少事"或"步数"打分。质量驱动，参考表如下：**

  10 一次到位：用户提需求 → agent 一步给出正确产出 → 用户接受无澄清/无负面情绪。
   9 接近一次到位：仅一处细节澄清，后续顺畅。
   8 正确完成但绕了 1 个小弯（多读一个文件、命令小修一次），用户基本满意。
   7 正确完成，但用户做了 2-3 次澄清/修正；无明显不耐烦。
   6 完成度边界——用户对结果勉强可用，但已表达"这就行吧"之类不完全满意。
   5 部分完成：核心需求达成，但遗漏明显细节；用户不得不补一次说明。
   4 多次错误后才接近正确：用户已用否定词（"不对"/"错了"）2 次以上。
   3 任务勉强算完成但用户明显失望（"算了"/"我自己来"），或 agent 在关键
     步骤上误判方向但侥幸跑通。
   2 任务未完成 / agent 反复触发 blocker / 用户明显放弃。
   1 完全失败 / 引发副作用（删错文件、改坏代码、误推送等）。

判 ux_score 时同时看：
- 这段 atom 是否真正调用了某个 used_skill；若调了 skill 且导致绕弯/错误 → 直接降到
  ≤5；若调了 skill 且一步到位 → 至少 8 起步。
- 这段 atom 的产出在用户后续 turn 是否被否定 / 撤销 / 重做。

输出格式（严格 XML，不要 markdown 包裹）
========================================
<atoms>
<atom>
  <offset_start>120</offset_start>
  <offset_end>2400</offset_end>
  <intent>...</intent>
  <summary>...</summary>
  <tags><tag>...</tag><tag>...</tag></tags>
  <used_skills><skill>...</skill></used_skills>
  <ux_score>8</ux_score>
</atom>
...
</atoms>
"""


_ATOM_RE = re.compile(r"<atom>(.*?)</atom>", re.DOTALL)


def _field(tag: str, text: str) -> re.Match | None:
    return re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)


@dataclass
class TaskAgent:
    llm: object
    store: AtomTaskStore
    max_context_chars: int = 30000

    # ── public API ────────────────────────────────────────────────

    def run(self, *, traj_id: str, traj_path: Path) -> list[AtomTask]:
        text = traj_path.read_text(encoding="utf-8")
        offset_start = self.store.last_offset(traj_id)
        if offset_start >= len(text):
            return []  # 没有新增内容，省一次 LLM 调用

        prior_atom = None
        last_id = self.store.last_atom_id(traj_id)
        if last_id is not None:
            prior_atom = self.store.load(last_id)

        prompt = self._build_prompt(text, offset_start, prior_atom)
        raw = self.llm.chat(prompt, system=SYSTEM_PROMPT)
        parsed = self._parse(raw, offset_floor=offset_start)
        if not parsed:
            raise ValueError(
                f"TaskAgent: LLM returned no parseable atoms for {traj_id} "
                f"(offset_start={offset_start})"
            )

        next_idx = len(self.store.list_by_traj(traj_id)) + 1
        new_atoms: list[AtomTask] = []
        for i, p in enumerate(parsed):
            atom_id = f"atom_{traj_id}_{next_idx + i:04d}"
            new_atoms.append(AtomTask(
                atom_id=atom_id,
                traj_id=traj_id,
                offset_start=int(p["offset_start"]),
                offset_end=int(p["offset_end"]),
                intent=p["intent"],
                summary=p["summary"],
                tags=p["tags"],
                used_skills=p["used_skills"],
                ux_score=p.get("ux_score"),
                pre_atom_id=None,
                post_atom_id=None,
                context_prefix=self._context_prefix(text, int(p["offset_start"])),
                raw_segment=text[int(p["offset_start"]):int(p["offset_end"])],
            ))

        # 本批内部相邻 atom 互填 pre/post
        for i in range(1, len(new_atoms)):
            new_atoms[i].pre_atom_id = new_atoms[i - 1].atom_id
            new_atoms[i - 1].post_atom_id = new_atoms[i].atom_id

        # 与 store 末尾 atom 衔接
        if prior_atom is not None:
            new_atoms[0].pre_atom_id = prior_atom.atom_id
            prior_atom.post_atom_id = new_atoms[0].atom_id
            self.store.save(prior_atom)

        for a in new_atoms:
            self.store.save(a)
        return new_atoms

    # ── prompt helpers ────────────────────────────────────────────

    def _context_prefix(self, text: str, offset: int) -> str:
        """生成 ``offset_start`` 之前内容的省略表示。

        - ``offset ≤ 200``: 直接给原文，模型自己看 sysprompt 也够。
        - 否则保留头 200 字（一般是轨迹元信息 / 首次 user query）+ 占位符。
        """
        if offset <= 200:
            return text[:offset]
        return text[:200] + f"\n\n[省略 {offset - 200} 字符]\n\n"

    def _build_prompt(self, text: str, offset_start: int,
                       prior_atom: AtomTask | None) -> str:
        delta = text[offset_start:offset_start + self.max_context_chars]
        prior_block = ""
        if prior_atom is not None:
            prior_block = (
                "上一段 AtomTask 摘要（仅作为衔接上下文，不要重新拆分这段）：\n"
                f"  intent:  {prior_atom.intent}\n"
                f"  summary: {prior_atom.summary}\n"
                f"  ux_score: {prior_atom.ux_score}\n\n"
            )
        prefix_block = self._context_prefix(text, offset_start)
        return (
            f"{prior_block}"
            f"context_prefix (sysprompt + 之前内容省略表示)：\n"
            f"---\n{prefix_block}\n---\n\n"
            f"从 offset={offset_start} 开始的增量内容（按用户意图切换拆 AtomTask）：\n"
            f"---\n{delta}\n---"
        )

    # ── XML parsing ───────────────────────────────────────────────

    def _parse(self, raw: str, offset_floor: int) -> list[dict]:
        atoms: list[dict] = []
        prev_end = offset_floor
        for m in _ATOM_RE.finditer(raw):
            block = m.group(1)
            os_m = _field("offset_start", block)
            oe_m = _field("offset_end", block)
            it_m = _field("intent", block)
            sm_m = _field("summary", block)
            us_m = _field("ux_score", block)
            tags = re.findall(r"<tag>(.*?)</tag>", block, re.DOTALL)
            skills = re.findall(r"<skill>(.*?)</skill>", block, re.DOTALL)
            if not (os_m and oe_m and it_m and sm_m):
                raise ValueError(
                    f"TaskAgent: malformed atom block: {block[:200]}"
                )
            os_v = int(os_m.group(1).strip())
            oe_v = int(oe_m.group(1).strip())
            if oe_v <= os_v:
                raise ValueError(
                    f"TaskAgent: non-positive span {os_v}..{oe_v}"
                )
            if os_v < prev_end:
                raise ValueError(
                    f"TaskAgent: offset_start {os_v} < previous end {prev_end}"
                )
            prev_end = oe_v
            ux: int | None = None
            if us_m:
                try:
                    n = int(us_m.group(1).strip())
                    if 1 <= n <= 10:
                        ux = n
                except ValueError:
                    pass
            atoms.append({
                "offset_start": os_v,
                "offset_end": oe_v,
                "intent": it_m.group(1).strip(),
                "summary": sm_m.group(1).strip(),
                "tags": [t.strip() for t in tags if t.strip()],
                "used_skills": [s.strip() for s in skills if s.strip()],
                "ux_score": ux,
            })
        return atoms
