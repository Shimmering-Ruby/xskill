"""TaskClusterAgent —— AtomTask → skill 归类
============================================

输入：单个 AtomTask（由 watcher / process 喂进来）
副作用：往一个或多个 skill 的 .candidates.yml 写 atom 贡献（含 weightscore 0-10）；
       如有必要先 new_skill_folder 建空 skill 目录。

sysprompt 设计
==============
把当前 skill_dir 下所有 skill 的 ``name: description`` 注进去作为路由表，让
cluster agent 决定 atom 归到哪个已存在 skill 或新建。预算总额约
20% × LLM context window，参考 CC 的做法（``analysis/04c-skills-implementation.md``）。

budget 控制（``build_skill_catalog_block``）：
- name 不限：超 budget 也全部保留（otherwise 模型完全看不到候选）
- 剩余预算 / 条数 ≥ 75 字符（≈25 token） → desc 按 min(per_desc, 300) 截断
- 剩余预算 / 条数 < 75 字符 → 全部丢 desc 只留 ``- <name>``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.frontmatter import parse as fm_parse

logger = logging.getLogger("xskill.task_cluster_agent")


_DESC_MIN_CHARS = 75   # 约 25 token；低于这个值就只留 name
_DESC_HARD_CAP = 300   # 单条 desc 上限（即使预算够也别全塞）


def build_skill_catalog_block(skill_dir: Path, max_chars: int) -> str:
    """构造 sysprompt 中的 skill 路由表块。

    无 skill 时返回 ``(no skills yet)``，让 cluster agent 知道当前是空的；
    它仍可以 new_skill_folder 创建第一个。
    """
    names_descs: list[tuple[str, str]] = []
    if skill_dir.is_dir():
        for d in sorted(skill_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm, _ = fm_parse(skill_md.read_text(encoding="utf-8"))
            name = fm.get("name") or d.name
            desc = (fm.get("description") or "").strip().replace("\n", " ")
            names_descs.append((name, desc))

    if not names_descs:
        return "(no skills yet)"

    name_only_lines = [f"- {n}" for n, _ in names_descs]
    name_cost = sum(len(l) + 1 for l in name_only_lines)
    remaining = max_chars - name_cost
    per_desc = remaining // len(names_descs)

    if per_desc < _DESC_MIN_CHARS:
        return "\n".join(name_only_lines)

    cap = min(per_desc, _DESC_HARD_CAP)
    out_lines: list[str] = []
    for n, d in names_descs:
        if not d:
            out_lines.append(f"- {n}")
        else:
            truncated = d[:cap] + ("…" if len(d) > cap else "")
            out_lines.append(f"- {n}: {truncated}")
    return "\n".join(out_lines)


SYSTEM_PROMPT_TEMPLATE = """你是 TaskClusterAgent。我会给你一个 AtomTask（用户的一段
完整意图 + agent 的执行复盘）；你决定它是否值得被某个 skill 收录，应该归到哪个
已有 skill（用 add_task_to_skill），或者应该新建一个 skill 容纳它（用
NewSkillFolder 再 add_task_to_skill）。

# 可用工具
- AtomTaskRead(atom_id) — 读某 atom 的完整内容
- AtomTaskSearch(query) — 检索其他 atom（语义 + 关键字 union）
- ReadTraj(traj_id, offset_start, offset_end) — 按 offset 读 traj.md 片段（细节兜底）
- SkillRead(skill_name) — 读 SKILL.md
- NewSkillFolder(skill_name) — 新建一个空 skill 目录骨架
- add_task_to_skill(skill_name, atom_id, weightscore) — 把 atom 当贡献写进 skill 的
  candidates buffer。weightscore 0-10 整数。**重要**：当一个 skill 的 candidates
  buffer 累计 weightscore_total ≥ 10 时会自动触发 SkillEditAgent，所以打分时要严肃。

# 当前可见的 skill 路由表（name: description）
{skill_catalog}

# weightscore 严格分档表（永远质量驱动，不要凑条数）

  10 这一个 atom 就足以单独或强烈支撑该 skill 的核心场景（罕见；只在 atom 包含
     可机械执行的、跨多类相似问题都成立的修复决策时才给）。给 10 立即触发 SkillEdit。
   8-9 高质量贡献：atom 完整覆盖某 skill 的关键阶段，包含具体命令/路径/函数名 + 可
       核验的产出 + 用户成功反馈。两个 8 分相加即触发 SkillEdit。
   6-7 中等贡献：atom 在该 skill 的范围内，但只覆盖了一个子阶段或某个 warning；
       需要别的 atom 补齐才有意义。
   4-5 弱贡献：atom 提到了相关问题，但执行细节模糊或不完整；放进 candidates 但
       不要寄希望于它单独触发。
   2-3 边缘相关：atom 只是和 skill 沾边，不要写进 candidates；除非确实想刷量
       否则直接不调 add_task_to_skill。
   1 完全不相关：不要写。

# 处理流程
1. 先用 AtomTaskSearch 看看有没有相似 atom（语义+关键字混合返回）。
2. 用 SkillRead 看候选 skill 的当前内容。
3. 决策：
   (a) 已有合适 skill → add_task_to_skill(<name>, <atom_id>, <weightscore>)
   (b) 没有但应该新建 → NewSkillFolder(<new_name>) 再 add_task_to_skill。
       **新建门槛**：单 atom weightscore < 7 不要新建（会污染 skill 列表）。
   (c) 不值得收录 → 不调任何 add_task_to_skill，直接说明理由结束。
4. 多个相关 skill 都可以加，但每个 skill 独立打分（不要把 10 分摊到多个）。

# 硬禁止
- 不要为了"做点事"乱打高分。低质 atom 就别加，否则会污染 candidates 触发劣质 skill。
- 不要伪造 atom_id；只用我给你的或 AtomTaskSearch 返回的真实 id。
- 不要直接写 SKILL.md——那是 SkillEditAgent 的职责。
"""


@dataclass
class TaskClusterAgent:
    skill_dir: Path
    store: AtomTaskStore
    agno_agent_factory: Callable[..., Any]
    llm_cfg: dict
    tools: list
    # ~20% of 128k token context window（保守起点；DeepSeek v4-flash 实际容量
    # 更大，按 plan 用 20% 作为软门槛）
    sysprompt_budget_chars: int = 25000

    def process(self, atom: AtomTask) -> str:
        """跑一次 cluster 决策，返回 agent 的 final content（日志用）。"""
        catalog = build_skill_catalog_block(
            self.skill_dir, self.sysprompt_budget_chars,
        )
        sysprompt = SYSTEM_PROMPT_TEMPLATE.format(skill_catalog=catalog)

        user_msg = (
            f"待分类 AtomTask:\n"
            f"  atom_id:   {atom.atom_id}\n"
            f"  traj_id:   {atom.traj_id}\n"
            f"  intent:    {atom.intent}\n"
            f"  summary:   {atom.summary}\n"
            f"  tags:      {atom.tags}\n"
            f"  used_skills (agent 自报): {atom.used_skills}\n"
            f"  ux_score:  {atom.ux_score}\n"
            f"  offset:    [{atom.offset_start}..{atom.offset_end}]\n\n"
            f"按系统指令处理这个 atom，做出归类决策。"
        )

        agent = self.agno_agent_factory(
            instructions=[sysprompt],
            tools=self.tools,
        )
        result = agent.run(user_msg)
        return getattr(result, "content", "") or ""
