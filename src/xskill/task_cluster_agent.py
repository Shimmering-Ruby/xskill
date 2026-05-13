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


def _scan_skill_state(skill_dir: Path) -> list[tuple[str, str, str]]:
    """扫所有 skill 子目录，返回 ``[(name, state, desc), ...]``。

    state 用 git 分支作为单一事实源（不再读 .meta.yml）：
    - ``baby``: 仅 baby 分支存在（cluster 刚创建，SkillEditAgent 还没跑过）
    - ``main``: main 分支存在 + 无 staging
    - ``staging``: main + staging 双分支
    - ``unknown``: 没 .git（异常状态，理论上不该出现）

    desc 从该 skill 的 SKILL.md（baby 分支也有 stub SKILL.md）frontmatter
    取，确保 cluster agent 看到的 desc 是 cluster 自己当初在 new_skill_folder
    时填的语义边界——和 main/staging 状态 desc 信息密度对等。
    """
    from xskill.git_lock import run_git
    out: list[tuple[str, str, str]] = []
    if not skill_dir.is_dir():
        return out
    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        name = d.name
        if not (d / ".git").is_dir():
            # 没初始化 git——历史遗留或异常
            out.append((name, "unknown", "(skill 无 git 仓库)"))
            continue

        # 用 git branch --list 判 state
        _, branches_out, _ = run_git(["branch", "--list"], cwd=str(d))
        branches = {
            line.lstrip("* ").strip()
            for line in branches_out.splitlines() if line.strip()
        }
        if "staging" in branches:
            state = "staging"  # 包含 main+staging 情况；staging 中的是新候选
        elif "main" in branches:
            state = "main"
        elif "baby" in branches:
            state = "baby"
        else:
            state = "unknown"

        # 读当前 SKILL.md frontmatter 取 desc（baby/main/staging 的 SKILL.md
        # 都至少含 stub frontmatter）
        desc = ""
        skill_md = d / "SKILL.md"
        if skill_md.is_file():
            try:
                fm, _ = fm_parse(skill_md.read_text(encoding="utf-8"))
                desc = (fm.get("description") or "").strip().replace("\n", " ")
            except Exception:
                pass

        # baby 状态额外拼 buffer 计数让 agent 看到"还差多少分到阈值"
        if state == "baby":
            n_cand = 0
            cand_yml = d / ".candidates.yml"
            if cand_yml.is_file():
                try:
                    import yaml
                    data = yaml.safe_load(cand_yml.read_text(encoding="utf-8")) or {}
                    n_cand = len(data.get("candidates", []) or [])
                except Exception:
                    pass
            desc = f"{desc} ({n_cand} cand in buffer)" if desc else f"({n_cand} cand)"
        out.append((name, state, desc))
    return out


def build_skill_catalog_block(skill_dir: Path, max_chars: int) -> str:
    """构造 sysprompt 中的 skill 路由表块。

    展示**所有** skill 目录（含 wip 空骨架），让 cluster agent 看到完整画
    像——避免对近义场景反复 ``new_skill_folder`` 创建近义 slug。

    每行格式：``- <name>[<state>]: <desc>``
    state 有 main / staging / main+staging / wip 四种。

    无 skill 时返回 ``(no skills yet)``。
    """
    entries = _scan_skill_state(skill_dir)
    if not entries:
        return "(no skills yet)"

    # name 头永远完整保留；desc 按预算压
    head_lines = [f"- {n}[{s}]" for n, s, _ in entries]
    head_cost = sum(len(l) + 1 for l in head_lines)
    remaining = max_chars - head_cost
    per_desc = remaining // len(entries) if entries else 0

    if per_desc < _DESC_MIN_CHARS:
        return "\n".join(head_lines)

    cap = min(per_desc, _DESC_HARD_CAP)
    out_lines: list[str] = []
    for n, s, d in entries:
        if not d:
            out_lines.append(f"- {n}[{s}]")
        else:
            truncated = d[:cap] + ("…" if len(d) > cap else "")
            out_lines.append(f"- {n}[{s}]: {truncated}")
    return "\n".join(out_lines)


SYSTEM_PROMPT_TEMPLATE = """你是 TaskClusterAgent。我会给你一个 AtomTask（用户的一段
完整意图 + agent 的执行复盘）；你决定它是否值得被某个 skill 收录，应该归到哪个
已有 skill（用 add_task_to_skill），或者应该新建一个 skill 容纳它（用
NewSkillFolder 再 add_task_to_skill）。

# 可用工具
- AtomTaskRead(atom_id) — 读某 atom 的完整 JSON（intent / summary / used_skills /
  raw_segment 全字段）
- AtomTaskSearch(query) — 混合检索其他 atom（语义向量 + BM25 关键字 union）
- ReadTraj(traj_id, offset_start, offset_end) — 按字符 offset 读 traj.md 原文片段
- SkillRead(skill_name) — 读某 skill 的 SKILL.md；wip 骨架返回 placeholder 提示
- NewSkillFolder(skill_name, description) — 新建一个空 skill 目录骨架（state=wip）。
  ``description`` 是**必填**：2-3 句中文，写清这个 skill 服务于什么类型的 atom——
  后续 cluster agent 看路由表就靠这段 desc 判断"该不该复用这个 wip"。**desc 留空
  会被工具拒绝**。永远不要为了"先占个 slug"开一个含糊 desc 的 wip。
- add_task_to_skill(skill_name, atom_id, weightscore) — 把 atom 当贡献写进 skill 的
  candidates buffer。weightscore 0-10 整数。**重要**：当一个 skill 的 candidates
  buffer 累计 weightscore_total ≥ 10 时会自动触发 SkillEditAgent 写 SKILL.md，
  所以打分要严肃。

# 当前可见的 skill 路由表
格式：``- <name>[<state>]: <desc>``，state ∈ {{baby, main, staging}}：
- ``baby`` = 刚被 cluster 创建的草稿，SKILL.md 是 stub；candidates 在 buffer 攒分
  中。如果新来的 atom 跟它的 desc 同类，**优先复用** baby 加分而不是新建近义 slug。
- ``main`` = 已正式产出的 skill 主版本（消费者 agent 可加载）
- ``staging`` = main + 灰度候选并存（注意此状态下 SkillEditAgent 暂停在该 skill
  上触发——但你 cluster 仍可继续 add_task_to_skill 累积 buffer，灰度结束后会消费）

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
2. 看路由表里是否已有合适候选——**包括 wip 骨架**。SkillRead 看候选 skill 的
   当前内容。wip skill 的 SkillRead 返回 placeholder，但 name + buffer 计数已
   告诉你它在攒什么类型的证据。
3. 决策：
   (a) 已有合适 skill（main / staging / **wip 都行**）→ add_task_to_skill
       (<name>, <atom_id>, <weightscore>)
   (b) 没有合适候选才新建 → NewSkillFolder(<new_name>) 再 add_task_to_skill。
       **新建门槛**：单 atom weightscore < 7 不要新建（会污染 skill 列表）。
   (c) 不值得收录 → 不调任何 add_task_to_skill，直接说明理由结束。
4. 多个相关 skill 都可以加，但每个 skill 独立打分（不要把 10 分摊到多个）。

# 硬禁止
- **避免近义 slug 泛滥**：如果路由表里已有 ``3gpp-crawl-routine[wip]``，你就
  **不要**再开 ``3gpp-crawler-cron-routine`` / ``cron-crawl-gate-check`` 这种
  近义 slug——直接 add_task_to_skill 加到 wip 骨架上让它攒满阈值出 SKILL.md。
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
