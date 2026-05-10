"""xskill — 从 AI Agent 执行轨迹自动蒸馏可复用 Skill。

公开 SDK：
    from xskill import XSkill, Skill, Trajectory, Evaluator
    from xskill.types import (
        WatchDir, SkillHit, TrajectoryHit,
        EvalScore, Candidate, UxScoreResult,
    )

进阶（少数场景，例如单测直接拿子系统）：
    from xskill import Registry, SkillRepo
"""

__version__ = "0.3.0"

# 顶级公开面：4 个核心类
from xskill.core import XSkill
from xskill.entities.skill import Skill
from xskill.entities.trajectory import Trajectory
from xskill.entities.evaluator import Evaluator

# 进阶：子系统类（不必常用）
from xskill.entities.registry import Registry
from xskill.entities.skill_repo import SkillRepo

__all__ = [
    "XSkill", "Skill", "Trajectory", "Evaluator",
    "Registry", "SkillRepo",
]
