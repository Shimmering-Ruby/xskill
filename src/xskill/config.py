"""
config.py — 全局路径与配置加载
═════════════════════════════════════
统一从 ~/.xskill/ 读取；无 cwd fallback、无环境变量 fallback、无 ~/.aikey fallback。
缺失即抛异常。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("xskill.config")

# ─── 默认根路径 ─────────────────────────────────────────────────
XSKILL_HOME = Path.home() / ".xskill"
CONFIG_PATH = XSKILL_HOME / "config.yaml"
REGISTRY_DB = XSKILL_HOME / "registry.db"
CHAT_DB = XSKILL_HOME / "chat_sessions.db"
LOGS_DIR = XSKILL_HOME / "logs"

_config: dict = {}
_overrides: dict = {}


def set_overrides(**kwargs):
    """CLI flag 覆盖。仅 debug / quiet 两个保留。"""
    for k, v in kwargs.items():
        if v is not None:
            _overrides[k] = v


def load_config(path: Optional[Path] = None) -> dict:
    """加载 ~/.xskill/config.yaml；不存在直接抛 FileNotFoundError。"""
    global _config
    cfg_path = Path(path) if path else CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"xskill config not found: {cfg_path}\n"
            f"Create it manually (see docs)."
        )
    with open(cfg_path) as f:
        _config = yaml.safe_load(f) or {}
    if not _config.get("llm", {}).get("api_key"):
        raise KeyError(f"llm.api_key missing in {cfg_path}")
    if not _config.get("embedding", {}).get("api_key"):
        raise KeyError(f"embedding.api_key missing in {cfg_path}")
    return _config


def get_config() -> dict:
    if not _config:
        load_config()
    return _config


def get_skill_dir() -> Path:
    """skill_dir: config.yaml 字段；默认 ~/.xskill/skill/"""
    cfg = get_config()
    raw = cfg.get("skill_dir") or str(XSKILL_HOME / "skill")
    return Path(raw).expanduser()


def get_logs_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def get_chat_archive_dir() -> Path:
    """chat 归档轨迹基目录。serve 启动时会自动 register_dir 进 watcher。"""
    p = XSKILL_HOME / "chat_archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_traj_dir() -> Path:
    """**已废弃**：dataset 应通过 ``xskill registry add <abs-path>`` 注册。

    保留这个函数仅为给"无显式路径"的内部调用兜底——返回第一个已注册的 watch_dir，
    或退回 chat_archive_dir。新代码不要用；改为通过 Registry / explicit path。"""
    # 避免 import 循环
    try:
        from xskill.registry import list_watch_dirs
        dirs = list_watch_dirs()
        if dirs:
            return Path(dirs[0]["path"])
    except Exception:
        pass
    return get_chat_archive_dir()


def get_registry_db_path() -> Path:
    return REGISTRY_DB


def get_chat_db_path() -> Path:
    return CHAT_DB


# ─── team (C/S 模式) 路径 ───────────────────────────────────────
# 纯路径运算，不读 config.yaml——client 瘦客户端无 llm.api_key，
# get_config() 会抛 KeyError。get_team_trajectories_dir() 是唯一例外
# （只 server 调，server 一定有 key）。

def get_team_server_state_path() -> Path:
    """server join token 落盘位置（~/.xskill/team_server.json，0600）。"""
    return XSKILL_HOME / "team_server.json"


def get_team_clients_db_path() -> Path:
    """server 端 client 注册表 SQLite。"""
    return XSKILL_HOME / "team_clients.db"


def get_team_client_state_path() -> Path:
    """client 端连接信息（server_url / client_id / join_token）。"""
    return XSKILL_HOME / "team_client.json"


def get_team_skills_dir() -> Path:
    """client 端 skill working copies 根目录。"""
    p = XSKILL_HOME / "team_skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_team_outbox_dir() -> Path:
    """client 端生态轨迹镜像 outbox 根目录。"""
    p = XSKILL_HOME / "team_outbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_team_trajectories_dir() -> Path:
    """server 端收下的 client 上传轨迹根目录。

    读 config.yaml ``team.server.traj_root``，缺省 ~/.xskill/team_trajectories。
    仅 server 调用。
    """
    cfg = get_config()
    raw = (cfg.get("team", {}).get("server", {}).get("traj_root")
           or str(XSKILL_HOME / "team_trajectories"))
    p = Path(raw).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ─── 调试 flag ──────────────────────────────────────────────────
def is_debug() -> bool:
    return _overrides.get("debug", False)


def is_quiet() -> bool:
    return _overrides.get("quiet", False)


# ─── 兼容旧 API（仅为过渡期保留，下期清掉）───────────────────────
def get_registry_dir() -> Path:
    """旧 API。返回 XSKILL_HOME。新代码请用 get_registry_db_path。"""
    return XSKILL_HOME




def get_output_dir() -> Path:
    """旧 API → 转 logs_dir"""
    return get_logs_dir()


def resolve_traj_path(path_or_dataset: str) -> Path:
    """旧 API。新代码：直接用绝对路径。"""
    p = Path(path_or_dataset).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"trajectory path not found: {p}")
    return p
