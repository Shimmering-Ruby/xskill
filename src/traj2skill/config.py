"""
config.py — 路径管理 + 配置加载
═══════════════════════════════════
统一管理所有路径，优先级: CLI flag > 环境变量 > config.yaml > 默认值
"""

import os, logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("t2s.config")

# 全局配置单例
_config: dict = {}
_overrides: dict = {}  # CLI flag 覆盖


def set_overrides(**kwargs):
    """CLI flag 设置覆盖值"""
    for k, v in kwargs.items():
        if v is not None:
            _overrides[k] = v


def get_config_path() -> Path:
    """配置文件路径"""
    if "config" in _overrides:
        return Path(_overrides["config"])
    env = os.environ.get("T2S_CONFIG")
    if env:
        return Path(env)
    return Path.cwd() / "config.yaml"


def load_config(path: Optional[Path] = None) -> dict:
    """加载配置文件"""
    global _config
    cfg_path = path or get_config_path()
    if cfg_path.exists():
        with open(cfg_path) as f:
            _config = yaml.safe_load(f) or {}
    else:
        _config = {}
    return _config


def get_config() -> dict:
    """获取已加载的配置"""
    if not _config:
        load_config()
    return _config


def get_traj_dir() -> Path:
    """轨迹目录: --traj-dir > T2S_TRAJ_DIR > config.yaml traj_dir > cwd/data"""
    if "traj_dir" in _overrides:
        return Path(_overrides["traj_dir"])
    env = os.environ.get("T2S_TRAJ_DIR")
    if env:
        return Path(env)
    cfg = get_config()
    if cfg.get("traj_dir"):
        return Path(cfg["traj_dir"])
    return Path.cwd() / "data"


def get_skill_dir() -> Path:
    """Skill 目录: --skill-dir > T2S_SKILL_DIR > config.yaml skill_dir > cwd/skill"""
    if "skill_dir" in _overrides:
        return Path(_overrides["skill_dir"])
    env = os.environ.get("T2S_SKILL_DIR")
    if env:
        return Path(env)
    cfg = get_config()
    if cfg.get("skill_dir"):
        return Path(cfg["skill_dir"])
    return Path.cwd() / "skill"


def get_output_dir() -> Path:
    """日志输出目录"""
    return Path.cwd() / "output"


def resolve_traj_path(path_or_dataset: str) -> Path:
    """
    解析轨迹路径参数：
    - 绝对/相对路径直接返回
    - --dataset name → get_traj_dir() / name
    """
    p = Path(path_or_dataset)
    if p.is_absolute() or p.exists():
        return p
    # 可能是 dataset 名称
    return get_traj_dir() / path_or_dataset


def is_debug() -> bool:
    return _overrides.get("debug", False)


def is_quiet() -> bool:
    return _overrides.get("quiet", False)
