"""
types.py — SDK dataclass 集中地
═══════════════════════════════════════
所有跨模块共享的 dataclass。**只放数据，不放行为。**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:  # 避免循环 import
    from traj2skill.entities.skill import Skill
    from traj2skill.entities.trajectory import Trajectory


# ─── Registry / Watch ─────────────────────────────────────────────
@dataclass
class WatchDir:
    id: int
    path: Path
    label: str
    auto_index: bool
    traj_count: int
    indexed_count: int


# ─── Search ───────────────────────────────────────────────────────
@dataclass
class SkillHit:
    skill: "Skill"
    similarity: float


@dataclass
class TrajectoryHit:
    trajectory: "Trajectory"
    similarity: float


# ─── Skill candidates ─────────────────────────────────────────────
@dataclass
class Candidate:
    pattern: str
    kind: Literal["step", "warning", "decision_branch"]
    attach_to: Optional[str]
    supporting_trajs: list[str]
    first_seen: Optional[date]
    promoted: bool


# ─── Evaluator ────────────────────────────────────────────────────
@dataclass
class EvalScore:
    """两种 tier 共用。tier 决定哪些字段有值。"""
    tier: Literal["llm", "sandbox"]
    eval_score: float

    # tier="llm"
    scores: Optional[dict[str, int]] = None
    runs: Optional[int] = None

    # tier="sandbox"
    baseline_pass_rate: Optional[float] = None
    skill_pass_rate: Optional[float] = None
    delta: Optional[float] = None
    instances: Optional[list[str]] = None


# ─── UX Score ─────────────────────────────────────────────────────
@dataclass
class UxScoreResult:
    scored: bool                      # False = 已存在（幂等跳过）
    score: Optional[int]              # 1-10
    reasons: str
    decision: dict                    # canary.check_and_decide 输出


# ─── Pipeline ─────────────────────────────────────────────────────
@dataclass
class DistillResult:
    """PipelineRunner.run_distill 返回值（本期暂不创建 PipelineRunner，
    保留 dataclass 给将来 watcher 改造时用）。"""
    action: Literal[
        "merged", "staged", "rejected", "skip",
        "updated_metadata", "dry_run", "error",
    ]
    changed_skills: list[str] = field(default_factory=list)
    eval_scores: dict[str, "EvalScore"] = field(default_factory=dict)
    error: Optional[str] = None
