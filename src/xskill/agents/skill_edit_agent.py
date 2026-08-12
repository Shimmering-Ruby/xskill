"""SkillEditAgent —— SKILL.md 自主整理 + 可恢复 checkpoint
===========================================================

何时触发（``maybe_run`` 守门）：
  1. 该 skill 没有 staging 分支（灰度中不再触发新 SkillEdit）
  2. ``.candidates.yml`` 中所有 atom 的 ``weightscore`` 累加 ≥ 10
  3. 触发场景是"create staging"时（即 main 已存在），额外要求
     ``.ux_scores.jsonl`` 至少有 1 条 ``side=main`` 的真实评分——
     避免冷启动后 main 无人用就连开 staging 卡死灰度链路
  4. 调用方（watcher._check_pending_skill_edits）保证不在冷启动期触发

baby 冷启动按配置 N（默认 5）以 candidates YAML 插入顺序逐批处理。每批使用
全新 agent 上下文，写完后必须调用 ``commit_baby``：在 baby 上创建真实非空
commit，并在同一仓锁内消费框架绑定的 atom_id。失败按 N/2（最小 1）重试；
任一批成功后下一批恢复配置 N。buffer 清空后框架统一跑一次 description
optimization，并把 baby 晋升 main。这样第 199/200 批崩溃也只重试未提交批次。

main→staging 与 jam 路径保留旧 ``*_turnN`` 渐进分支语义，避免扩大 Issue #146
的行为面。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from xskill.skill import candidates as C

logger = logging.getLogger("xskill.skill_edit_agent")


def _candidate_order(candidate: dict) -> tuple[int, str]:
    return (
        -int(candidate.get("weightscore", 0) or 0),
        str(candidate.get("atom_id", "")),
    )


def _candidate_weight(candidate: dict) -> int:
    return int(candidate.get("weightscore", 0) or 0)


# ---------------------------------------------------------------------------
# 写作指导段（GUIDANCE）—— 白名单 / 反模式 / 泛化闸 / 参数化 / 失败挖掘 /
# 结构纪律 / 证据纪律 / 长度预算。这一段是**可切换**的：
#   - 默认（环境变量 XSKILL_SKILLEDIT_GUIDANCE_FILE 未设）：用下方 committed 文本，
#     与历史行为零差异。
#   - 若 XSKILL_SKILLEDIT_GUIDANCE_FILE 指向一个文件：用该文件内容**整体替换**本段，
#     管线契约段（场景块、SKILL.md schema、commit 工具协议、隐私守护、frontmatter
#     schema、工具清单、硬禁止）保持不动。
# 见 build_system_prompt() / _resolve_guidance()。
# ---------------------------------------------------------------------------
DEFAULT_GUIDANCE_BLOCK = """## 只允许写三类内容（白名单）

1. **领域规则**（domain rule）：该领域客观成立的事实与约束，写明机制原因。
   例式："X 会导致 Y，因为 Z；所以必须 W"。
2. **可复用工具**（tool）：参数化的脚本/代码片段，放 scripts/ 辅助文件；正文只写
   何时用、怎么调。**所有路径、文件名、单元格地址一律参数化**。
3. **坑位清单**（pitfall）：错误模式 → 症状 → 根因 → 修法，四元组俱全。

## 反模式（黑名单，出现即不合格）

- **任务流程复述**：把"读说明 → 写代码 → 跑验证"这类任何任务都一样的执行流程当内容写。
- **实例细节搬运**：把某次任务的具体文件名、具体数据值、具体题目要求抄进正文。
- **触发表述抄题面**：description 里引用的"典型触发表述"必须是**用户意图**的概括，
  不是任务提示词原文。

## 泛化自检闸（写完每条内容后必做）

自问："换一道同领域、不同题目的任务，这条还成立吗？"——不成立的删掉。
每条规则写明**适用条件**（什么场景下成立 / 不成立）。

## 参数化禁兜底

可复用脚本里所有路径、文件名、单元格地址一律走参数，**禁止任何具体值兜底**——
如 ``else 'somefile.xlsx'`` / ``path = path or 'input.xlsx'`` 这种硬编码默认值
写法一律算不合格，宁可让缺参直接报错。

## 失败轨迹是一等公民（成功教"怎么做"，失败教"哪里会死"，后者往往更值钱）

1. **死因回溯**：对每条失败轨迹，找到**死因**（结局信息里的失败原因，如评分读到了
   空值），向上回溯到导致它的具体动作，写成坑位四元组（错误模式 → 症状 → 根因 → 修法），
   根因必须解释**机制**（为什么这个动作导致这个结局），不许停在"做错了"层面。
2. **成败差分**：材料里存在"同一道题，一条过、一条挂"的对照时**必须**做差分——
   两条在哪一步分道扬镳、过的一侧做对了什么、挂的一侧做错了什么，写成一条带证据的
   领域规则（"同题对照：做 A 者过、做 B 者挂 → 必须 A 禁止 B"）。
3. **无症状死亡深挖**：agent 自信收工但结局是失败的轨迹，其根因往往是领域里最隐蔽、
   最值得沉淀的规则，优先深挖。
"""


# 写作指导的第二段（默认）：结构 / 证据 / 长度。env 切换时与上段一并被替换为空。
DEFAULT_GUIDANCE_BLOCK_2 = """# 正文结构纪律

正文四段顺序固定：``## 核心原则`` → ``## 领域规则`` → ``## 坑位清单`` → ``## 工具``。
- 核心原则：≤3 条，每条一句话 + 一句机制原因。
- 领域规则：逐条编号，每条三件套（规则本身 / 为什么（机制）/ 适用条件）。
- 坑位清单：表格，列为 错误模式 | 症状 | 根因 | 修法（坑位四元组）。
- 工具：每个 scripts/ 文件一行说明（何时用 + 调用方式 + 参数含义）。

# 证据纪律

每条规则 / 坑位末尾标注证据强度：``[实证：N 条轨迹]`` 或 ``[单例]`` 或 ``[推断]``。
仅靠 ``[推断]`` 支撑的内容总数 **≤ 2 条**。``[实证]`` 必须在生成上下文中有具体
atom 证据。atom/traj id 可以保留为服务器端证据标记，但规则本身必须自包含，不能把
这些 id 写成用户或用户 agent 可以读取的外部引用。

# 长度预算（防灌水，但知识完整性优先）

- 正文总长 **≤ 200 行**。
- **删减顺序铁律**：超预算时先删可泛化性最弱的内容；带 ``[实证]`` 标注的规则和
  坑位四元组**不许为省行数而截断适用场景**——一条规则的全部适用场景必须写全，
  **宁可删掉整条弱规则，不许把强规则砍成半条**。
"""


# 轨迹堰塞强砍（jam-merge）专用纪律：注入 scenario，约束"合并去重而非拼接"。
MERGE_DISCIPLINE_BLOCK = """# 本次是【轨迹堰塞强砍合并】，不是普通蒸馏
candidates 已堆到强砍阈值仍未等到灰度裁决（疑似灰度错位/无真实流量）。你要把
**现有 main 正文 + staging 正文 + 下列候选 atom** 合并成一份新的 main 正文：
- **合并去重，不是拼接**：main 与 staging 里的同义规则/坑位合并成一条，证据强度
  `[实证:N]` 相加计票，不要并列两条近义内容。
- **冲突择强**：main 与 staging 对同一点说法相反时，标注分歧并择证据强者保留，
  不允许两条都留。
- **守预算与 schema**：正文 ≤200 行、frontmatter schema 不变、`source_atoms`
  取三方并集（main + staging + 本次候选）。
- 写完调 `commit_update_main(skill_name, message)` 直接 commit 回 main（不开
  staging、不走灰度）。commit message 注明：发生轨迹堰塞、疑似灰度错位，并分列
  provenance——哪些来自存量（main/staging）、哪些是本次候选合并进来的。
"""


_SYSTEM_PROMPT_TEMPLATE_WITH_GUIDANCE = """你是 SkillEditAgent。某 skill 的 candidates buffer 累计
weightscore ≥ 10，需要你产出/更新它的 SKILL.md。

# 当前场景

{scenario_block}

# 你的目标

读 atom 内容（AtomTaskRead），必要时读 traj 原文（ReadTraj），从轨迹里
**提炼可泛化的知识**，写成 skill。skill 的价值 = 读它的人少踩多少坑、
少试多少次错，而不是把一次执行过程复述一遍。SKILL.md 是必产物，但你
**不限于**只写 SKILL.md——可以补充任何辅助文件，只要在 skill 目录范围内：

- ``<skill_dir>/SKILL.md`` — 必产物，frontmatter + body
- ``<skill_dir>/scripts/*.py`` / ``*.sh`` — 可机械执行、参数化的脚本
- ``<skill_dir>/references/*.md`` — 长参考材料（trace / 配置 / 文档摘录）
- ``<skill_dir>/templates/*`` 等任意子目录

{guidance_block}

# SKILL.md schema

```
---
name: <英文 slug>
description: <2-5 句中文：干什么、典型触发表述（引号引原文）、需要的工具/权限>
compatibility: <环境/版本/权限 + 至少一条负向硬约束>
metadata:
  version: <自增整数；新建从 1 开始，更新版本号+1>
  created: "<AUTO>"
  last_updated: "<AUTO>"
  source_atoms: ["atom_xxx_0001", ...]
---

# <中文标题>

<开头一段：这个 skill 解决什么>

## 核心原则
- <≤3 条。该领域最高优先级的不变式，每条一句话 + 一句机制原因。>

## 领域规则
1. <逐条编号。每条三件套——**规则本身 / 为什么（机制）/ 适用条件**。>
   末尾标证据强度：``[实证：N 条轨迹]`` / ``[单例]`` / ``[推断]``。

   > ⚠️ <坑位/警告：把错误模式、症状、根因、修法与适用条件写完整，使内容
   > 不依赖外部材料即可理解；如需保留来源，可在末尾写
   > ``[XSkill 服务器端证据标记：atom_xxx_0001]``，同时标注证据强度。>

``atom_id`` / ``traj_id`` 是供 XSkill 服务器端处理的内部证据标记，不是用户可访问的
引用。用户 agent 没有读取原始 atom / traj 的接口；不得用“见、参见、读取、查找”等
措辞要求或暗示它回查这些 id。即使正文保留证据标记，标记前的结论也必须完整、自包含。

## 坑位清单
| 错误模式 | 症状 | 根因（机制） | 修法 | 证据 |
|---|---|---|---|---|
| <做了什么> | <表现> | <为什么会死> | <怎么改> | [实证：N 条] |

## 工具
- ``scripts/<file>`` — <何时用 + 调用方式 + 参数含义；路径/文件名全参数化>
```

{guidance_block_2}

# 写完文件**必须 commit**

写完所有文件后**必须**调以下其中一个工具完成版本化——否则改动只是工作目录
脏文件，watcher 会判定本次 SkillEdit 失败重试。

## 当前在 {branch_now} 分支，按场景调对应工具：

- **如果在 baby 分支**：调 ``commit_baby(skill_name, message)``
  checkpoint 当前批次。它保持 baby 分支并消费系统绑定的 atom_id；当
  candidates 全部清空后，框架会自动 graduate 到 main。
- **如果在 main 分支**（已有 main，本次是更新）：调
  ``commit_to_staging(skill_name, message)`` 把更新作为灰度候选放进 staging。
  staging 会被灰度系统 vs main 对比，胜出才升级。

commit message 写明本次基于哪些 atom_id 整理，例如：
``"v2: 合并 atom_traj_x_0001/0003 的 zombie cleanup 步骤；新增 references/pidns_pitfall.md"``

# 可用工具
- AtomTaskRead(atom_id) — 读 atom JSON
- ReadTraj(traj_id, offset_start, offset_end) — 按行号取轨迹原文（offset 即 1-based 行号）
- SkillRead(skill_name) — 读现有 SKILL.md + 该 skill 目录其余文件树（更新场景先看这个）
- read_file(path, offset=1, limit=200) — 按 1-based 行号窗口读取 skill 仓 /
  ~/.xskill / /tmp spill 下的文件；trim 后的 ``spill_path`` 也用它分页回读
- list_files(path) — 列目录文件，返回可直接传给 read_file 的完整路径
- grep_files(pattern, path="", glob="", max_results=100) — 全文检索（ripgrep），
  返回「文件:行号:内容」；先检索定位、再 read_file 精读，别逐个翻文件
- write_file(path, content) — 写任意文件到 skill_dir 下
- commit_baby(skill_name, message) — 仅 baby 分支可用；checkpoint 当前批次
- commit_to_staging(skill_name, message) — 仅 main 分支可用

# 隐私守护

source atom / traj 原文来自真实开发轨迹，即便上传时已脱敏，仍可能残留
**敏感信息**——API key、token、密码、私钥、内网地址、个人邮箱/姓名等。
整理 skill 时：

- **绝不**把这些原样抄进 SKILL.md / scripts / references 任何产物。skill 要
  沉淀的是「做法」，不是「某次跑用的具体密钥」。
- 引用命令/配置/代码时，凡凭证位置一律用占位符——``API_KEY=<your-api-key>``、
  ``--token <TOKEN>``、``password: <password>``、``ssh user@<host>``。
- 看到 ``[REDACTED]``（上传脱敏留下的）保持原样，不要试图"还原"或编一个值。
- 拿不准某串是否敏感 → 一律当敏感处理、占位符化。

# 提交前质量闸（写完 SKILL.md，commit 前**必须逐条自检**，并把结论写进 commit message）

1. **价值自检**：这个 skill 替用户解决了什么具体问题——精简流程 / 发现问题 /
   解决问题 / 统计问题？在 commit message 里写出一句"替用户发现/解决了 X"。
   说不出具体价值的 skill 不该出版本。
2. **渐进式披露（progressive disclosure）**：description 只放"是什么 + 何时用"
   （触发信息）；执行细节、命令、判据进正文/辅助文件，**正文里不要再放触发判据**。
3. **无孤立脚本**：每个 ``scripts/`` / ``references/`` 下的脚本或辅助文件**必须**
   被 SKILL.md 正文引用并说明用途——没有任何正文引用的孤儿文件不合格，要么在
   正文里写清怎么用，要么删掉。
4. **description 可触发**：祈使语气（"Use this skill for…"而非"this skill does…"）
   + 聚焦用户意图 + 主动列出典型触发场景（防 undertrigger 漏触发）
   + 100–200 词 + 严格 <1024 字符。

（注：第 4 条最终由系统的硬编码 description 优化器在 commit 时兜底重写并按
held-out test 集选优；你只需先写个像样的初稿。）

# 硬禁止
- 不要随便引用没在 atom 中出现过的命令/函数；以 AtomTaskRead 为唯一可信来源
- 不要在描述里发明用户群体或场景
- 正文超长按上面的「长度预算」铁律删减；过长的参考材料拆到 references/ 或 scripts/
- 不要写 ``## trigger`` / ``## 触发条件`` 段——触发信号只在 frontmatter.description
  （坑位写进上面的 ``## 坑位清单`` 表格，规则附带的警告用 ``> ⚠️`` 内联 blockquote）
- **不要自己用 write_file 写 ``.git/`` 下文件**或 ``.candidates.yml``——前者会
  破坏 git 状态，后者是 cluster 的 buffer 由系统管理
"""

SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT_TEMPLATE_WITH_GUIDANCE.format(
    scenario_block="{scenario_block}",
    branch_now="{branch_now}",
    guidance_block=DEFAULT_GUIDANCE_BLOCK,
    guidance_block_2=DEFAULT_GUIDANCE_BLOCK_2,
)


GUIDANCE_ENV = "XSKILL_SKILLEDIT_GUIDANCE_FILE"


def _resolve_guidance() -> tuple[str, str]:
    """返回 (guidance_block, guidance_block_2)。

    默认（env 未设）：committed 文本，行为零改变。
    若 XSKILL_SKILLEDIT_GUIDANCE_FILE 指向可读文件：用该文件内容整体替换写作
    指导段（block_1），block_2 置空（外部 guidance 文件自含全部结构/证据/长度规则）。
    env 指向不存在/读不了的文件 → 记 warning 并退回默认，绝不静默用空指导。
    """
    import os

    path = os.environ.get(GUIDANCE_ENV, "").strip()
    if not path:
        return DEFAULT_GUIDANCE_BLOCK, DEFAULT_GUIDANCE_BLOCK_2
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning(
            "%s=%r 读不了（%s）——退回默认 committed 写作指导段",
            GUIDANCE_ENV, path, e,
        )
        return DEFAULT_GUIDANCE_BLOCK, DEFAULT_GUIDANCE_BLOCK_2
    if not text:
        logger.warning(
            "%s=%r 内容为空——退回默认 committed 写作指导段", GUIDANCE_ENV, path
        )
        return DEFAULT_GUIDANCE_BLOCK, DEFAULT_GUIDANCE_BLOCK_2
    logger.info("SkillEdit 写作指导段由 %s 替换为 %s（%d 字符）",
                GUIDANCE_ENV, path, len(text))
    return text, ""


def build_system_prompt(scenario_block: str, branch_now: str) -> str:
    """组装 SkillEdit system prompt：管线契约段固定，写作指导段按 env 可切换。"""
    guidance, guidance2 = _resolve_guidance()
    return _SYSTEM_PROMPT_TEMPLATE_WITH_GUIDANCE.format(
        scenario_block=scenario_block,
        branch_now=branch_now,
        guidance_block=guidance,
        guidance_block_2=guidance2,
    )


@dataclass
class SkillEditAgent:
    """每个实例服务**一个**具体 skill 子目录。"""
    skill_dir: Path
    store: Any  # AtomTaskStore (only needed by atom_task_read tool indirectly)
    agno_agent_factory: Callable[..., Any]
    llm_cfg: dict
    traj_root: Path
    threshold: int = C.ATOM_PROMOTION_THRESHOLD
    jam_threshold: int = 50  # candidates Σws 地板（与 age/plateau 合取）
    min_jam_age_sec: float = 1800.0
    jam_plateau_sec: float = 600.0
    batch_size: int = 5
    retry_batch_size: int | None = None
    logs_dir: Path | None = None
    next_batch_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.skill_dir = Path(self.skill_dir)
        self.traj_root = Path(self.traj_root)
        if isinstance(self.batch_size, bool) or int(self.batch_size) <= 0:
            raise ValueError("SkillEditAgent batch_size must be a positive integer")
        self.batch_size = int(self.batch_size)
        if self.retry_batch_size is not None:
            if (
                isinstance(self.retry_batch_size, bool)
                or int(self.retry_batch_size) <= 0
            ):
                raise ValueError(
                    "SkillEditAgent retry_batch_size must be a positive integer"
                )
            self.retry_batch_size = int(self.retry_batch_size)
        self.next_batch_size = self.retry_batch_size or self.batch_size
        if self.logs_dir is not None:
            self.logs_dir = Path(self.logs_dir)

    def _skill_tree_context_lines(self, max_entries: int = 80) -> list[str]:
        """返回当前 skill 根目录与文件树，供 agent 决定是否 read_file。"""
        lines = [
            "",
            f"skill_base_path: {self.skill_dir}",
            "# 当前 skill 文件树（相对 skill_base_path；需要内容时用 read_file 读取）",
        ]
        entries: list[str] = []
        for path in sorted(self.skill_dir.rglob("*")):
            rel = path.relative_to(self.skill_dir)
            if ".git" in rel.parts:
                continue
            if rel.name in {".candidates.yml", ".ux_scores.jsonl", ".lock"}:
                continue
            suffix = "/" if path.is_dir() else ""
            entries.append(f"- {rel.as_posix()}{suffix}")
            if len(entries) >= max_entries:
                entries.append(f"- ... truncated after {max_entries} entries")
                break
        lines.extend(entries or ["- (empty)"])
        return lines

    def maybe_run(self) -> bool:
        """检查所有守门条件 → 触发 agent → 验证落盘 → 清 buffer。

        守门顺序（任一失败即 return False）：
          1. 该 skill 有 staging 分支 → 灰度中：
             - age≥min_jam_age ∧ plateau≥jam_plateau ∧ ws≥jam_threshold → jam
             - 否则维持 hold（并打门控日志）
          2. 无 staging 时：candidates 累计 weightscore < threshold → 没攒够
          3. 触发场景是 "create staging"（main 已存在）：
             - .ux_scores.jsonl 必须至少有 1 条 side=main → 证明 main 真有人用
             - 否则保留 candidates 等用户用过 main 再触发

        baby 全过 → 按 N 逐批 checkpoint + 消费，清空后框架晋升 main。
        main/jam 全过 → 保留旧渐进分支链，终态成功后摘除入口快照 atom_id。
        """
        from xskill.skill.git import current_branch, run_git
        from xskill.canary import CanaryConfig, evaluate_jam_gates

        self._recover_crashed_turns()

        # 守门 1：staging 存在时——三条件合取才 jam；否则 hold。
        staging_exists = run_git(
            ["rev-parse", "--verify", "staging"], cwd=str(self.skill_dir),
        )[0] == 0
        data = C.load_candidates(self.skill_dir)
        total_ws = sum(
            int(c.get("weightscore", 0))
            for c in (data.get("candidates", []) or [])
        )
        jam = False
        if staging_exists:
            jam_cfg = CanaryConfig(
                jam_threshold=self.jam_threshold,
                min_jam_age_sec=self.min_jam_age_sec,
                jam_plateau_sec=self.jam_plateau_sec,
            )
            gates = evaluate_jam_gates(
                self.skill_dir, total_ws=total_ws, config=jam_cfg,
            )
            logger.info(
                "jam_gate %s: ok=%s age=%.1f plateau_s=%.1f "
                "main_n=%s/%s staging_n=%s/%s ws=%s/%s "
                "main_sha=%s staging_sha=%s reason=%s",
                self.skill_dir.name, gates.get("ok"), gates.get("age"),
                gates.get("plateau_s"),
                gates.get("main_n"), gates.get("need"),
                gates.get("staging_n"), gates.get("need"),
                gates.get("ws"), gates.get("jam_threshold"),
                gates.get("main_sha"), gates.get("staging_sha"),
                gates.get("reason"),
            )
            jam = bool(gates.get("ok"))
            if not jam:
                return False

        if not staging_exists:
            cur = current_branch(str(self.skill_dir))
            if cur == "baby":
                partial_baby = self._baby_has_checkpoint()
                # 首次启动仍要求累计达到 promotion threshold；一旦已有 baby
                # checkpoint，就必须无视剩余权重继续 drain，避免最后几个低分
                # atom 永远无法完成毕业。candidate 已空则补完 crash window 中
                # “remove 成功、rename 前进程退出”的最终晋升。
                if not partial_baby:
                    if not C.ready_for_promotion_v2(
                        data, threshold=self.threshold,
                    ):
                        return False
                return self._run_baby_until_empty()
            if cur == "main":
                # 守门 2: main 的新一轮更新仍要求累计达到阈值。
                ready = C.ready_for_promotion_v2(data, threshold=self.threshold)
                if not ready:
                    return False
                # 守门 3: create staging 前要求 main 真有人用过。
                if not self._main_has_ux_score():
                    logger.info(
                        "skip SkillEdit: %s main 还没真实 ux_score，"
                        "保留 candidates 等用户用 main 后再产 staging",
                        self.skill_dir.name,
                    )
                    return False
            else:
                logger.warning(
                    "skip SkillEdit: %s 在异常分支 %r (期望 baby 或 main)",
                    self.skill_dir.name, cur,
                )
                return False
        else:
            ready = list(data.get("candidates", []) or [])
            cur = "main"

        skill_md = self.skill_dir / "SKILL.md"
        mtime_before = skill_md.stat().st_mtime if skill_md.is_file() else 0.0
        size_before = skill_md.stat().st_size if skill_md.is_file() else 0
        main_sha_before = ""
        if jam:
            code, out, _ = run_git(["rev-parse", "main"], cwd=str(self.skill_dir))
            if code == 0:
                main_sha_before = out.strip()

        # 快照：本次消化范围 = 当前 buffer 里的这些 atom_id。多轮渐进式消化
        # 期间 cluster 并发 add 进来的新候选不在这个集合里，不受影响，留给
        # 下一次 maybe_run 处理（避免竞争/饿死）。
        snapshot_atom_ids = {
            c.get("atom_id") for c in ready if c.get("atom_id")
        }

        try:
            run_ok = self._run(ready, current_branch_name=cur, jam=jam)
        except Exception:
            logger.exception("SkillEditAgent _run failed: %s", self.skill_dir.name)
            run_ok = False

        # 实测落盘：mtime 推进 + 非空 = agent 真写了
        wrote = (
            skill_md.is_file()
            and skill_md.stat().st_size > 0
            and (
                skill_md.stat().st_mtime > mtime_before
                or skill_md.stat().st_size != size_before
            )
        )
        if not wrote:
            logger.warning(
                "SkillEditAgent ran but SKILL.md not written/empty: %s — "
                "保留 candidates 等下轮重试",
                self.skill_dir.name,
            )
            return False

        # 发布门兜底：write_file 已挡住非法 frontmatter，但 agent 可能绕开
        # write_file（或别的路径）落了坏 SKILL.md。commit 前再跑一次 parse_strict，
        # 非法 → 不清 buffer、标重试，绝不把坏 skill 静默发布出去。
        from xskill.skill.frontmatter import (
            parse_strict as fm_parse_strict,
            FrontmatterError,
        )
        try:
            fm_parse_strict(skill_md.read_text(encoding="utf-8"))
        except FrontmatterError as e:
            logger.warning(
                "SkillEditAgent 落了非法 SKILL.md: %s — %s；保留 candidates 重试",
                self.skill_dir.name, e,
            )
            return False

        if not run_ok:
            logger.warning(
                "SkillEditAgent _run 未完整跑完终态提交（多轮渐进式消化中途失败/"
                "崩溃）: %s — 保留 candidates，下轮 maybe_run 会清残留 turn 分支"
                "重新开始",
                self.skill_dir.name,
            )
            return False

        if jam:
            from xskill.canary import discard_staging

            code, out, _ = run_git(["rev-parse", "main"], cwd=str(self.skill_dir))
            main_sha_after = out.strip() if code == 0 else ""
            if not main_sha_after or main_sha_after == main_sha_before:
                logger.warning(
                    "SkillEditAgent jam-merge did not advance main: %s — "
                    "保留 candidates/staging 等下轮重试",
                    self.skill_dir.name,
                )
                return False
            if not discard_staging(self.skill_dir):
                logger.warning(
                    "SkillEditAgent jam-merge could not discard staging: %s — "
                    "保留 candidates 等下轮重试",
                    self.skill_dir.name,
                )
                return False
            self._cleanup_turn_branches("main")

        # commit 工具的成功效应（baby→main 或 main→staging）通过当前分支变化
        # 自然反映，不需要在这里做额外检查（非 jam 路径的 turn 分支已在
        # ``_run`` 尾部的终态校验通过后就地清理）
        C.remove_candidates(self.skill_dir, snapshot_atom_ids)
        logger.info("SkillEditAgent done + %d candidate(s) removed: %s",
                    len(snapshot_atom_ids), self.skill_dir.name)
        return True

    def _baby_has_checkpoint(self) -> bool:
        """Return whether baby has at least one commit after its init commit."""
        from xskill.skill.git import run_git

        return run_git(
            ["rev-parse", "baby~1"],
            cwd=str(self.skill_dir),
        )[0] == 0

    def _trace_path(self) -> Path | None:
        if self.logs_dir is None:
            return None
        return (
            self.logs_dir
            / "agents"
            / "skill_edit_agents"
            / "skills"
            / f"{self.skill_dir.name}.log"
        )

    def _trace_limits(self) -> tuple[int, int | None]:
        from xskill.agents.context_budget import (
            DEFAULT_MAX_CONTEXT,
            TRIM_TRIGGER_RATIO,
        )

        max_context = int(
            (self.llm_cfg or {}).get("max_context") or DEFAULT_MAX_CONTEXT
        )
        spill_limit = int(max_context * TRIM_TRIGGER_RATIO)
        compact_raw = (self.llm_cfg or {}).get("compact_token_limit")
        compact_limit = (
            max(int(compact_raw), spill_limit)
            if compact_raw not in (None, "")
            else None
        )
        return spill_limit, compact_limit

    def _append_turn_start(self, n: int, processing: int, pending: int) -> None:
        from xskill.agents import agent_trace

        agent_trace.append_to(
            self._trace_path(),
            (
                "\n================ TURN START "
                f"| N={n} | processing {processing} of {pending} "
                "pending atoms ================\n\n"
            ),
        )

    def _append_turn_end(
        self,
        status: str,
        *,
        consumed: int,
        remaining: int,
        next_n: int,
    ) -> None:
        from xskill.agents import agent_trace

        agent_trace.append_to(
            self._trace_path(),
            (
                f"\nTURN END | {status} | consumed={consumed} "
                f"| {remaining} remaining | next N={next_n}\n\n"
            ),
        )

    def _reset_baby_worktree(self) -> None:
        """Discard uncheckpointed tracked/untracked edits, preserving ignored buffers."""
        from xskill.skill.git import run_git

        reset_code, _, reset_error = run_git(
            ["reset", "--hard", "HEAD"],
            cwd=str(self.skill_dir),
        )
        clean_code, _, clean_error = run_git(
            ["clean", "-fd"],
            cwd=str(self.skill_dir),
        )
        if reset_code != 0 or clean_code != 0:
            logger.warning(
                "SkillEdit baby worktree cleanup incomplete for %s: reset=%s clean=%s",
                self.skill_dir.name,
                reset_error,
                clean_error,
            )

    def _graduate_completed_baby(self) -> bool:
        """Promote an empty, checkpointed baby to main under framework control."""
        from xskill.agents import agent_tools, agent_trace
        from xskill.skill.git import skill_md_still_baby_stub

        if skill_md_still_baby_stub(self.skill_dir):
            logger.warning(
                "SkillEdit refuse graduate (stub still present), re-trigger rewrite: %s",
                self.skill_dir.name,
            )
            agent_trace.append_to(
                self._trace_path(),
                (
                    f"{time.strftime('%H:%M:%S')} INFO  "
                    "Candidate buffer empty but SKILL.md still stub; "
                    "re-trigger rewrite before graduate.\n"
                ),
            )
            if not self._run_stub_rewrite_turn():
                logger.warning(
                    "SkillEdit stub rewrite failed; stay on baby: %s",
                    self.skill_dir.name,
                )
                return False
            if skill_md_still_baby_stub(self.skill_dir):
                logger.warning(
                    "SkillEdit stub rewrite left placeholder; stay on baby: %s",
                    self.skill_dir.name,
                )
                return False

        ok = agent_tools.graduate_baby_to_main(
            self.skill_dir,
            self.skill_dir.name,
            "graduate baby after all candidate checkpoints",
        )
        if ok:
            self.next_batch_size = self.batch_size
            agent_trace.append_to(
                self._trace_path(),
                (
                    f"{time.strftime('%H:%M:%S')} INFO  "
                    "Candidate buffer empty; promoted baby -> main.\n"
                ),
            )
        else:
            logger.warning(
                "SkillEdit baby checkpoints complete but graduation failed: %s",
                self.skill_dir.name,
            )
        return ok

    def _run_stub_rewrite_turn(self) -> bool:
        """Buffer empty but body still stub → one forced write_file turn, no commit tool."""
        from xskill.agents import agent_tools, agent_trace

        skill_md = self.skill_dir / "SKILL.md"
        scenario_block = "\n".join(
            [
                f"skill_name: {self.skill_dir.name}（**baby 分支 · stub 未清除，强制重写**）",
                "SKILL.md 仍是 init placeholder，框架拒绝 graduate。",
                "必须用 write_file 覆盖 SKILL.md 为正式正文（去掉 placeholder 行）。",
                "本轮没有 commit 工具；写完后由框架校验并 graduate。",
                *self._skill_tree_context_lines(),
                f"目标 SKILL.md 路径: {skill_md}",
            ]
        )
        sysprompt = build_system_prompt(
            scenario_block=scenario_block,
            branch_now="baby",
        )
        tools = [
            agent_tools.atom_task_read,
            agent_tools.read_traj,
            agent_tools.skill_read,
            agent_tools.read_file,
            agent_tools.list_files,
            agent_tools.grep_files,
            agent_tools.write_file,
        ]
        agent = self.agno_agent_factory(instructions=[sysprompt], tools=tools)
        agent_trace.append_to(
            self._trace_path(),
            (
                f"{time.strftime('%H:%M:%S')} INFO  "
                "Stub rewrite turn start (no commit tool).\n"
            ),
        )
        try:
            self._trace_run(agent, scenario_block)
        except Exception:
            logger.exception(
                "SkillEdit stub rewrite turn raised: %s",
                self.skill_dir.name,
            )
            return False
        from xskill.skill.git import skill_md_still_baby_stub

        return not skill_md_still_baby_stub(self.skill_dir)

    def _run_baby_until_empty(self) -> bool:
        """Drain baby candidates via durable per-batch commits, then graduate."""
        current_n = self.retry_batch_size or self.batch_size
        current_n = max(1, min(int(current_n), self.batch_size))
        self.next_batch_size = current_n

        while True:
            data = C.load_candidates(self.skill_dir)
            candidates = list(data.get("candidates", []) or [])
            if not candidates:
                return self._graduate_completed_baby()

            pending = len(candidates)
            batch = self._take_baby_batch(candidates, current_n)
            batch_ids = [
                str(candidate["atom_id"])
                for candidate in batch
                if candidate.get("atom_id")
            ]
            if not batch_ids:
                logger.warning(
                    "SkillEdit baby candidate batch has no atom_id: %s",
                    self.skill_dir.name,
                )
                return False

            self._append_turn_start(current_n, len(batch_ids), pending)
            committed, remaining = self._run_baby_batch(
                batch,
                batch_ids=batch_ids,
                n=current_n,
            )
            if committed:
                self.next_batch_size = self.batch_size
                self._append_turn_end(
                    "COMMITTED",
                    consumed=len(batch_ids),
                    remaining=remaining,
                    next_n=self.batch_size,
                )
                current_n = self.batch_size
                continue

            next_n = max(1, current_n // 2)
            self.next_batch_size = next_n
            from xskill.agents import agent_trace
            agent_trace.append_to(
                self._trace_path(),
                (
                    f"{time.strftime('%H:%M:%S')} INFO  "
                    f"Retry batch reduced: {current_n} -> {next_n}\n"
                ),
            )
            self._append_turn_end(
                "FAILED",
                consumed=0,
                remaining=remaining,
                next_n=next_n,
            )
            if current_n == 1:
                return False
            current_n = next_n

    @staticmethod
    def _take_baby_batch(candidates: list[dict], n: int) -> list[dict]:
        """Take at most N candidates in stable YAML insertion (FIFO) order."""
        return candidates[:min(max(1, int(n)), len(candidates))]

    def _run_baby_batch(
        self,
        batch: list[dict],
        *,
        batch_ids: list[str],
        n: int,
    ) -> tuple[bool, int]:
        """Run one baby turn and verify the durable checkpoint, not run() return."""
        from xskill.agents import agent_tools
        from xskill.skill.git import run_git

        before_code, before_out, _ = run_git(
            ["rev-parse", "HEAD"],
            cwd=str(self.skill_dir),
        )
        before_sha = before_out.strip() if before_code == 0 else ""
        run_error: Exception | None = None
        try:
            with agent_tools.use_skill_edit_batch(
                self.skill_dir.name,
                batch_ids,
            ):
                self._run_normal_round(
                    batch,
                    current_branch_name="baby",
                    turn_idx=1,
                    num_batches=1,
                    is_last=True,
                )
        except Exception as error:  # noqa: BLE001
            run_error = error
            logger.warning(
                "SkillEdit baby turn failed for %s (N=%d): %s",
                self.skill_dir.name,
                n,
                error,
            )

        after_code, after_out, _ = run_git(
            ["rev-parse", "HEAD"],
            cwd=str(self.skill_dir),
        )
        after_sha = after_out.strip() if after_code == 0 else ""
        try:
            remaining_candidates = list(
                C.load_candidates(self.skill_dir).get("candidates", []) or []
            )
        except Exception:
            logger.exception(
                "failed to reload candidates after baby turn: %s",
                self.skill_dir.name,
            )
            remaining_candidates = []
            remaining_ids = set(batch_ids)
        else:
            remaining_ids = {
                str(candidate.get("atom_id"))
                for candidate in remaining_candidates
                if candidate.get("atom_id")
            }
        checkpointed = bool(
            before_sha
            and after_sha
            and before_sha != after_sha
            and not (set(batch_ids) & remaining_ids)
        )
        self._reset_baby_worktree()
        if checkpointed:
            if run_error is not None:
                logger.info(
                    "SkillEdit baby turn raised after durable checkpoint; "
                    "treating as success: %s",
                    self.skill_dir.name,
                )
            return True, len(remaining_candidates)
        return False, len(remaining_candidates)

    def _main_has_ux_score(self) -> bool:
        """检查该 skill 的 .ux_scores.jsonl 是否有至少 1 条 side=main 记录。

        冷启动后没人用过 main 时，避免立刻产生 staging 卡死灰度链路（B 守门）。
        """
        from xskill.canary import load_ux_scores
        try:
            scores = load_ux_scores(self.skill_dir)
        except Exception:
            return False
        return any(s.get("side") == "main" for s in scores)

    # ───────────────────────────────────────────────────────────────
    # 崩溃恢复 + turn 分支生命周期
    # ───────────────────────────────────────────────────────────────

    def _recover_crashed_turns(self) -> None:
        """``maybe_run`` 入口第一件事：发现残留 turn 分支就判定上次跑到一半
        崩溃/失败，简单粗暴重置——不续传。

        buffer 从未在多轮消化中途被清空（只有全部批次成功才 remove），所以
        重置只丢弃 turn 分支上的半成品工作区改动，不丢候选。
        """
        import re

        from xskill.skill.git import current_branch, list_turn_branches, run_git

        turn_branches = list_turn_branches(str(self.skill_dir), base_branch=None)
        if not turn_branches:
            return

        cur = current_branch(str(self.skill_dir))
        bases = {re.sub(r"_turn\d+$", "", b) for b in turn_branches}
        reset_target = cur if cur in ("baby", "main") else (
            sorted(bases)[0] if bases else "main"
        )
        logger.warning(
            "SkillEditAgent 发现残留 turn 分支 %s（skill=%s）——判定上次崩溃/"
            "失败，重置到 %r 重新开始整条链条",
            turn_branches, self.skill_dir.name, reset_target,
        )
        run_git(["checkout", reset_target], cwd=str(self.skill_dir))
        run_git(["branch", "-D", *turn_branches], cwd=str(self.skill_dir))

    def _cleanup_turn_branches(self, base_branch: str) -> None:
        """成功收尾后删除该 base 派生的所有遗留 turn 分支（空列表时零开销）。"""
        from xskill.skill.git import list_turn_branches, run_git

        turn_branches = list_turn_branches(str(self.skill_dir), base_branch)
        if turn_branches:
            run_git(["branch", "-D", *turn_branches], cwd=str(self.skill_dir))

    # ───────────────────────────────────────────────────────────────
    # 批次切分
    # ───────────────────────────────────────────────────────────────

    def _make_batches(self, ready: list[dict]) -> list[list[dict]]:
        """按 (weightscore 降序, atom_id 升序) 稳定排序后,切成
        ``SKILL_EDIT_BATCH_SIZE`` 条一批。"""
        ordered = sorted(
            ready,
            key=_candidate_order,
        )
        size = C.SKILL_EDIT_BATCH_SIZE
        return [ordered[i:i + size] for i in range(0, len(ordered), size)]

    def _round_info_lines(self, turn_idx: int, num_batches: int) -> list[str]:
        """单批（num_batches<=1）不插入任何轮次说明——prompt 文本与消化前
        逐字一致,零行为差异。"""
        if num_batches <= 1:
            return []
        if turn_idx == 1:
            note = (
                f"这是渐进式编辑第 1/{num_batches} 轮：candidates buffer 过大，"
                f"已按 {C.SKILL_EDIT_BATCH_SIZE} 条一批切分为 {num_batches} 轮处理，"
                "本轮只处理下面列出的这一批候选。"
            )
        else:
            note = (
                f"这是渐进式编辑第 {turn_idx}/{num_batches} 轮：当前 SKILL.md 已经"
                "融合了前面几批候选的内容，请先读现状（SkillRead / read_file）再"
                "基于本轮候选继续编辑，不要覆盖丢弃前面几轮已经写好的内容。"
            )
        return ["", note]

    def _trace_run(self, agent: Any, user_msg: str) -> None:
        """Append one agent.run() to the skill's single human-readable trace."""
        from xskill.agents.agent_trace import trace_to

        spill_limit, compact_limit = self._trace_limits()
        with trace_to(
            self._trace_path(),
            append=True,
            spill_token_limit=spill_limit,
            compact_token_limit=compact_limit,
        ):
            agent.run(user_msg)

    # ───────────────────────────────────────────────────────────────
    # 多轮消化主循环
    # ───────────────────────────────────────────────────────────────

    def _run(self, ready: list[dict], current_branch_name: str, jam: bool = False) -> bool:
        """跑完整条（可能多轮的）消化链。

        返回 True = 终态 commit 完整跑完（baby→main / main 直接更新走
        staging / jam 强砍合并）且 turn 分支已清理；False/异常 = 某一环节
        失败——调用方保留 candidates，下轮 ``maybe_run`` 触发崩溃恢复重来。
        """
        from xskill.skill import git as skillgit
        from xskill.skill.git import current_branch as _current_branch
        from xskill.skill.git import run_git as _run_git

        batches = self._make_batches(ready)
        num_batches = len(batches)
        if num_batches == 0:
            return False

        for turn_idx, batch in enumerate(batches, start=1):
            is_last = turn_idx == num_batches
            if is_last and num_batches > 1:
                # 最后一轮开跑前把 HEAD 切回原分支（工作区不动，仍是最后一个
                # turn 分支演化出的内容）——终态 commit 工具的分支校验才能过。
                skillgit.checkout_head_ref_only(
                    str(self.skill_dir), current_branch_name,
                )
            if jam:
                self._run_jam_round(
                    batch, turn_idx=turn_idx, num_batches=num_batches, is_last=is_last,
                )
            else:
                self._run_normal_round(
                    batch, current_branch_name=current_branch_name,
                    turn_idx=turn_idx, num_batches=num_batches, is_last=is_last,
                )
            if not is_last:
                turn_branch = f"{current_branch_name}_turn{turn_idx}"
                skillgit.commit_progressive_turn(
                    str(self.skill_dir), turn_branch,
                    f"skilledit progressive turn {turn_idx}/{num_batches} "
                    f"({len(batch)} atoms, not final)",
                )

        if jam:
            # jam 的终态校验（main sha 是否真推进）+ discard_staging + turn 分支
            # 清理都在 maybe_run 里做（它已经拿着 main_sha_before 了）。这里只
            # 负责把多轮循环跑完，不重复判定。
            return True

        final_branch = _current_branch(str(self.skill_dir))
        if current_branch_name == "baby":
            ok = final_branch == "main"
        else:
            staging_ok = _run_git(
                ["rev-parse", "--verify", "staging"], cwd=str(self.skill_dir),
            )[0] == 0
            ok = staging_ok and final_branch == "main"
        if ok:
            self._cleanup_turn_branches(current_branch_name)
        return ok

    def _run_jam_round(
        self, batch: list[dict], *, turn_idx: int, num_batches: int, is_last: bool,
    ) -> None:
        from xskill.agents import agent_tools

        skill_md = self.skill_dir / "SKILL.md"
        staging_body = (
            self.skill_dir.parent / ".canary" / self.skill_dir.name / "SKILL.md"
        )
        if not staging_body.is_file():
            from xskill.canary import materialize_staging
            materialize_staging(self.skill_dir, self.skill_dir.parent / ".canary")
        if not staging_body.is_file():
            raise RuntimeError(
                f"jam-merge: staging body for {self.skill_dir.name} could not be "
                "materialized; refusing to merge and discard"
            )

        lines = [
            MERGE_DISCIPLINE_BLOCK,
            *self._round_info_lines(turn_idx, num_batches),
            "",
            f"skill_name: {self.skill_dir.name}（**main 分支 · 轨迹堰塞强砍合并**）",
            f"现有 main 正文：用 skill_read('{self.skill_dir.name}') 读。",
            f"staging 正文路径（用 read_file 读）：{staging_body}",
            *self._skill_tree_context_lines(),
            "# 待合并候选（按 weightscore 倒序）",
        ]
        for c in sorted(batch, key=_candidate_weight, reverse=True):
            note = c.get("note", "")
            lines.append(
                f"- atom_id={c['atom_id']}  weightscore={c['weightscore']}"
                + (f"  note: {note}" if note else "")
            )
        lines += [
            "",
            f"目标 skill 目录: {self.skill_dir}",
            f"目标 SKILL.md 路径: {skill_md}",
        ]
        scenario_block = "\n".join(lines)
        sysprompt = build_system_prompt(scenario_block=scenario_block, branch_now="main")

        tools = [
            agent_tools.atom_task_read,
            agent_tools.read_traj,
            agent_tools.skill_read,
            agent_tools.read_file,
            agent_tools.list_files,
            agent_tools.grep_files,
            agent_tools.write_file,
        ]
        if is_last:
            tools.append(agent_tools.commit_update_main)

        agent = self.agno_agent_factory(instructions=[sysprompt], tools=tools)
        self._trace_run(agent, scenario_block)

    def _run_normal_round(
        self, batch: list[dict], *, current_branch_name: str,
        turn_idx: int, num_batches: int, is_last: bool,
    ) -> None:
        from xskill.agents import agent_tools
        from xskill.skill.frontmatter import parse as fm_parse

        skill_md = self.skill_dir / "SKILL.md"
        scenario_lines: list[str] = []
        if current_branch_name == "baby":
            scenario_lines.append(
                "skill_name: " + self.skill_dir.name + "（**baby 分支**——首次出版本）"
            )
            scenario_lines.extend(self._round_info_lines(turn_idx, num_batches))
            scenario_lines.append(
                "本轮只处理下面这批候选。写完后必须调 "
                "``commit_baby(skill_name, message)``：它会 checkpoint 当前改动"
                "并消费系统绑定的 atom_id，但保持在 baby；buffer 清空后由框架"
                "自动 graduate 到 main。"
            )
        else:
            scenario_lines.append(
                "skill_name: " + self.skill_dir.name + "（**main 分支** —— 更新现有 skill）"
            )
            scenario_lines.extend(self._round_info_lines(turn_idx, num_batches))
            if is_last:
                scenario_lines.append(
                    "写完 SKILL.md 后调 ``commit_to_staging(skill_name, message)`` "
                    "把更新作为灰度候选 commit 到 staging。"
                )
            else:
                scenario_lines.append(
                    "本轮没有提供任何 commit 工具——分支推进由系统在全部批次渐进式"
                    "消化完成后自动处理，你只需要把本轮候选编辑进 SKILL.md。"
                )

        # 现有 SKILL.md 是 stub (baby 时) 或正式版 (main 时)；多轮消化中间态时
        # 是前几轮已经融合过候选的正文。
        if skill_md.is_file():
            try:
                fm, _ = fm_parse(skill_md.read_text(encoding="utf-8"))
                cur_desc = (fm.get("description") or "").strip().replace("\n", " ")
                cur_ver = (fm.get("metadata", {}) or {}).get("version", "?")
                scenario_lines.append("")
                scenario_lines.append(f"现有 SKILL.md description: {cur_desc[:200]}")
                scenario_lines.append(f"现有 SKILL.md version: {cur_ver}")
            except Exception:
                logger.warning("failed to read existing skill metadata: %s",
                               skill_md, exc_info=True)
        scenario_lines.extend(self._skill_tree_context_lines())
        scenario_lines.append("")
        scenario_lines.append("# 待整理 candidates（按 weightscore 倒序）")
        for c in sorted(batch, key=_candidate_weight, reverse=True):
            note = c.get("note", "")
            ext = f"  note: {note}" if note else ""
            scenario_lines.append(
                f"- atom_id={c['atom_id']}  weightscore={c['weightscore']}{ext}"
            )
        scenario_lines.append("")
        scenario_lines.append(f"目标 skill 目录: {self.skill_dir}")
        scenario_lines.append(f"目标 SKILL.md 路径: {skill_md}")

        scenario_block = "\n".join(scenario_lines)
        sysprompt = build_system_prompt(
            scenario_block=scenario_block,
            branch_now=current_branch_name,
        )
        user_msg = scenario_block  # 同时也作为 user 消息（agno 两端都看）

        tools = [
            agent_tools.atom_task_read,
            agent_tools.read_traj,
            agent_tools.skill_read,
            agent_tools.read_file,
            agent_tools.list_files,
            agent_tools.grep_files,
            agent_tools.write_file,
        ]
        if is_last:
            if current_branch_name == "baby":
                tools.append(agent_tools.commit_baby)
            else:
                tools.append(agent_tools.commit_to_staging)

        agent = self.agno_agent_factory(instructions=[sysprompt], tools=tools)
        self._trace_run(agent, user_msg)
