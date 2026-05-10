"""
sandbox — 可扩展沙箱评测框架
"""

from .base import Sandbox, TaskSpec, TrialResult, ABResult, SandboxResult
from .registry import register, get_sandbox, available_sandboxes

# 导入具体实现，触发 @register 注册
from . import swe_smith  # noqa: F401

__all__ = [
    "Sandbox", "TaskSpec", "TrialResult", "ABResult", "SandboxResult",
    "register", "get_sandbox", "available_sandboxes",
]
