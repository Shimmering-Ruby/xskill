"""
ux_score.py -- 用户体验分打分器
=================================

灰度期间对每条使用过某 skill 的轨迹异步打分。读入：
  - 轨迹 Markdown（完整对话）
  - 该轨迹命中的 skill_name / side (main|staging) / commit_sha

输出：
  - score:   1–10 整数
  - reasons: 简短中文归因

信号（见设计文档 §3.3）：
  - 用户负面情绪 / 纠正性追问   → 扣分
  - 用户沉默 / 放弃              → 扣分
  - agent 在 skill 指导下反复绕弯 / 触发 blocker → 扣分
  - 一次性解决 + 用户正向反馈    → 加分

落盘通过 :func:`traj2skill.canary.append_ux_score` 完成幂等追加。判定由
:func:`traj2skill.canary.check_and_decide` 在每次入库后事件触发。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from traj2skill import canary
from traj2skill.llm_client import LLMClient

logger = logging.getLogger("ux_score")


SYSTEM_PROMPT = """你是一个用户体验评审员，读一条 agent 与用户的对话，为 agent 的表现打分。

评分标准（1–10 整数）：
  10  一次命中，用户满意，无纠正
   8  正确完成任务，但用户有一次澄清或小修正
   6  基本可用，但 agent 绕弯或漏掉细节，用户明显感到不耐烦
   4  多次错误，用户反复纠正，出现明显负面情绪
   2  任务未完成 / agent 触发 blocker / 用户放弃
   1  完全失败或引发严重后果

扣分信号（检测到即降档）：
  - 用户负面情绪词（"不对"、"错了"、"别这样"、"？？？"、"傻"等）
  - 用户多次打断纠正
  - agent 照着 skill 执行却出错，导致用户重做
  - agent 在 skill 指导下重复尝试失败
  - 对话突然中断 / 用户不再回复

加分信号：
  - 用户一次性接受结果，仅表示感谢或结束
  - agent 一步到位给出正确命令/路径/代码

输出必须是**纯 JSON**，形如：
  {"score": 7, "reasons": "用户做了一次澄清，agent 随后一次通过；无明显负面情绪"}
不要输出任何 JSON 以外的文字。
"""


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


def score_trajectory(
    llm: LLMClient,
    *,
    traj_md: str,
    skill_name: str,
    side: str,
) -> dict:
    """调一次 LLM，返回 {'score': int 1-10, 'reasons': str}。解析失败返回 None 字段。"""
    prompt = (
        f"本次对话使用了 skill: {skill_name}（来自 {side} 分支）。\n"
        f"以下是完整对话：\n\n{_truncate(traj_md)}\n\n"
        "请按评分标准给出 JSON。"
    )
    raw = llm.chat(prompt, system=SYSTEM_PROMPT)
    data = _parse_score(raw)
    score = data.get("score")
    reasons = data.get("reasons", "")
    # 清洗
    try:
        score = int(score)
    except Exception:
        score = None
    if score is None or not (1 <= score <= 10):
        logger.warning(f"ux_score 结果非法 (score={score}); raw={raw[:200]}")
        return {"score": None, "reasons": reasons or raw[:200]}
    return {"score": score, "reasons": (reasons or "").strip()}


# ═══════════════════════════════════════════════════════════════════
# 组合流程：打分 + 幂等落盘 + 事件触发判定
# ═══════════════════════════════════════════════════════════════════

def score_and_record(
    *,
    llm: LLMClient,
    skill_dir: Path,
    skill_name: str,
    traj_id: str,
    traj_md: str,
    side: str,
    commit_sha: str,
    canary_config: canary.CanaryConfig | None = None,
) -> dict:
    """端到端：LLM 打分 → 落盘 .ux_scores.jsonl → 触发 controller 判定。

    返回：
      {
        "scored":   bool,   # 是否成功打分并落盘（幂等：已存在返回 False）
        "score":    int|None,
        "reasons":  str,
        "decision": dict,   # canary.check_and_decide 的返回
      }
    """
    skill_dir = Path(skill_dir)
    result = score_trajectory(llm, traj_md=traj_md, skill_name=skill_name, side=side)
    score = result["score"]
    reasons = result["reasons"]

    if score is None:
        return {"scored": False, "score": None, "reasons": reasons, "decision": {"action": "score_failed"}}

    written = canary.append_ux_score(
        skill_dir,
        traj_id=traj_id,
        skill_name=skill_name,
        side=side,
        commit_sha=commit_sha,
        score=score,
        reasons=reasons,
    )

    if not written:
        return {
            "scored": False,
            "score": score,
            "reasons": reasons,
            "decision": {"action": "duplicate_skipped"},
        }

    decision = canary.check_and_decide(skill_dir, config=canary_config)
    return {
        "scored": True,
        "score": score,
        "reasons": reasons,
        "decision": decision,
    }
