"""traj2skill — 从 AI Agent 执行轨迹自动蒸馏可复用 Skill。

公开 SDK：
    from traj2skill import T2S, Skill, Trajectory, Evaluator
    from traj2skill.types import (
        WatchDir, SkillHit, TrajectoryHit,
        EvalScore, Candidate, UxScoreResult,
    )

进阶（少数场景，例如单测直接拿子系统）：
    from traj2skill import Registry, SkillRepo
"""

__version__ = "0.3.0"

# 顶级公开面：4 个核心类
from traj2skill.t2s import T2S
from traj2skill.entities.skill import Skill
from traj2skill.entities.trajectory import Trajectory
from traj2skill.entities.evaluator import Evaluator

# 进阶：子系统类（不必常用）
from traj2skill.entities.registry import Registry
from traj2skill.entities.skill_repo import SkillRepo

__all__ = [
    "T2S", "Skill", "Trajectory", "Evaluator",
    "Registry", "SkillRepo",
]
