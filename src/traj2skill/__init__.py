"""traj2skill — 从 AI Agent 执行轨迹自动蒸馏可复用 Skill"""

__version__ = "0.2.0"

# SDK API — 供外部 import 使用
from traj2skill.config import load_config, get_traj_dir, get_skill_dir, set_overrides
from traj2skill.llm_client import LLMClient, EmbedClient, create_llm_client, create_embed_client
from traj2skill.index import index_dataset, meta_to_index_text, validate_meta, parse_meta
from traj2skill.search import search, search_for_pipeline
from traj2skill.process import process_traj
from traj2skill.skill_eval import run_eval, should_merge
from traj2skill.skill_manager import (
    list_skills, show_skill, skill_log, skill_diff,
    rollback_skill, freeze_skill, unfreeze_skill,
    delete_skill, export_skill, import_skill,
)
from traj2skill.git_lock import ensure_repo
from traj2skill.sandbox import Sandbox, TaskSpec, SandboxResult, get_sandbox, available_sandboxes

__all__ = [
    # config
    "load_config", "get_traj_dir", "get_skill_dir", "set_overrides",
    # llm
    "LLMClient", "EmbedClient", "create_llm_client", "create_embed_client",
    # index
    "index_dataset", "meta_to_index_text", "validate_meta", "parse_meta",
    # search
    "search", "search_for_pipeline",
    # process
    "process_traj",
    # eval
    "run_eval", "should_merge",
    # skill management
    "list_skills", "show_skill", "skill_log", "skill_diff",
    "rollback_skill", "freeze_skill", "unfreeze_skill",
    "delete_skill", "export_skill", "import_skill",
    # git
    "ensure_repo",
    # sandbox
    "Sandbox", "TaskSpec", "SandboxResult", "get_sandbox", "available_sandboxes",
]
