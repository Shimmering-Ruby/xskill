"""
ux_score.py -- AtomTask 用户体验分打分器
==========================================

灰度期间对每个 AtomTask（一段完整用户意图的对话片段）打分。读入：
  - AtomTask 数据（context_prefix + raw_segment）
  - 该 atom 用过的 skills，以及当前 side (main|staging) / commit_sha

输出：
  - score:   1–10 整数（按严格分档表）
  - reasons: 简短中文归因

落盘通过 :class:`xskill.atom_canary.AtomCanary` 完成幂等追加（``atom_id``
为主键）。判定由 ``canary.check_and_decide`` 在每次入库后事件触发。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from xskill import canary
from xskill.llm_client import LLMClient

logger = logging.getLogger("ux_score")


def _truncate(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + "\n\n...（中间省略）...\n\n" + tail


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_score(raw: str) -> dict:
    """从 LLM 输出提取 JSON；容错：抽第一个 {...} 块再 json.loads。"""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = _JSON_RE.search(raw)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception as e:
            logger.warning(f"ux_score JSON 解析失败: {e}; raw={raw[:200]}")
    return {}


# ═══════════════════════════════════════════════════════════════════
# v2: atom 粒度打分（替代 score_trajectory）
# ═══════════════════════════════════════════════════════════════════

from xskill.atom_task import AtomTask  # noqa: E402 (循环导入安全，xskill.atom_task 不 import ux_score)


SYSTEM_PROMPT_ATOM = """你是用户体验评审员。给你一段 AtomTask（一个完整用户意图下的
对话片段 + 在哪些 skill 加载下做的）。请按下面的严格分档表打 1-10 分。

# 严格分档表（永远质量驱动，不要凭"做了多少事"打）
  10 一次到位：用户提需求 → agent 一步给出正确产出 → 用户接受无澄清。
   9 接近一次到位：仅一处细节澄清。
   8 正确完成但绕了 1 个小弯。
   7 正确完成，2-3 次澄清/修正；无明显不耐烦。
   6 完成度边界（"这就行吧"）。
   5 核心需求达成但遗漏明显细节。
   4 多次错误后才接近正确，用户≥2 次否定词。
   3 任务勉强完成但用户明显失望。
   2 任务未完成 / 反复 blocker / 用户放弃。
   1 完全失败或副作用。

# 如果 used_skills 非空（agent 调过 skill）
- skill 一步到位 → 起步 8 分。
- skill 调了但导致绕弯/错误 → 直接降到 ≤5。

# 输出格式（严格 JSON，不要任何 JSON 以外的文字）
{"score": 7, "reasons": "<简短中文归因>"}
"""


def score_atom(llm: LLMClient, *, atom: AtomTask, side: str) -> dict:
    """调一次 LLM，按严格分档表给 atom 打分。

    返回 ``{"score": int|None, "reasons": str}``。
    解析失败或越界时 score=None，让上层（watcher / AtomCanary）跳过记录。

    与旧 ``score_trajectory`` 的差异：
    - 输入对象从整条 traj.md 变成 AtomTask 片段（context_prefix + raw_segment）
    - prompt 显式告诉 LLM ``used_skills``，触发 used-skill 降档/起步规则
    - 仍走 ``_truncate`` + ``_parse_score`` 复用旧的容错解析
    """
    body = _truncate((atom.context_prefix or "") + "\n\n" + (atom.raw_segment or ""))
    prompt = (
        f"side={side}\n"
        f"used_skills={atom.used_skills}\n"
        f"intent={atom.intent}\n"
        f"summary={atom.summary}\n\n"
        f"# 对话片段\n{body}\n\n请按系统指令打分。"
    )
    raw = llm.chat(prompt, system=SYSTEM_PROMPT_ATOM)
    data = _parse_score(raw)
    score = data.get("score")
    reasons = (data.get("reasons") or "").strip()
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = None
    if score is None or not (1 <= score <= 10):
        logger.warning(f"score_atom 非法分数 ({score})；raw={raw[:200]}")
        return {"score": None, "reasons": reasons or raw[:200]}
    return {"score": score, "reasons": reasons}


# ═══════════════════════════════════════════════════════════════════
# v2 组合流程：对一条 traj 的所有 atom 打分 + 落盘 + 翻牌
# ═══════════════════════════════════════════════════════════════════

def score_and_record_atoms(*, llm, skill_dir, store, traj_id, skill_name,
                           side, commit_sha, canary_config=None) -> dict:
    """对 store 中该 traj 的所有 atom 端到端打分并按 atom_id 落盘。

    每个 atom 独立调 ``score_atom``；幂等去重交给 ``AtomCanary.append``。
    所有 atom 处理完后调一次 ``check_and_decide`` 触发翻牌判定。

    返回：
      {
        "scored":   int,    # 本次实际新落盘的分数条数
        "skipped":  int,    # 因幂等跳过 / 越界 / LLM 失败跳过
        "decision": dict,   # 最后一次 check_and_decide 返回；无 atom 时空 dict
      }
    """
    from xskill.atom_canary import AtomCanary

    skill_dir = Path(skill_dir)
    ac = AtomCanary(skill_dir=skill_dir)
    atoms = store.list_by_traj(traj_id)
    scored = 0
    skipped = 0
    for atom in atoms:
        result = score_atom(llm=llm, atom=atom, side=side)
        if result["score"] is None:
            skipped += 1
            continue
        written = ac.append(
            atom_id=atom.atom_id, skill_name=skill_name,
            side=side, commit_sha=commit_sha,
            score=result["score"], reasons=result["reasons"],
        )
        if written:
            scored += 1
        else:
            skipped += 1
    decision = ac.check_and_decide(config=canary_config) if atoms else {}
    return {"scored": scored, "skipped": skipped, "decision": decision}
