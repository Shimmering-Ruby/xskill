"""xskill.ecosystems — 把蒸馏出的 Skill 装进各 AI-agent 生态 + 轨迹格式适配。

包结构：
  dispatch.py   — 跨平台共享件 + 安装/ingest 实现 + EcosystemSpec / JsonlIngester
  adapt.py      — 轨迹格式适配层（adapt_trajectory / submit_trajectory / 各 _adapt_*）
  _fallback.py  — 跨平台目录安装的三阶 fallback
  _history.py   — daemon 自己装到 ~/.claude/skills/ 的 side 历史

本 ``__init__`` 只做 re-export：保持 ``from xskill.ecosystems import X``
对历史调用方（watcher / server / team / 测试）一致可用。
"""

from xskill.ecosystems.dispatch import (
    # specs
    EcosystemSpec,
    SqliteEcosystemSpec,
    CC_SPEC,
    CODEX_SPEC,
    OPENCLAW_SPEC,
    CURSOR_SPEC,
    OPENCODE_SPEC,
    # ingesters
    JsonlIngester,
    SqliteIngester,
    CCSessionIngester,
    # detection
    detect_known_ecosystems,
    # install (single)
    install_to_claude_code,
    install_to_codex,
    install_to_cursor,
    install_to_openclaw,
    install_to_opencode,
    # install (all)
    install_all_to_claude_code,
    install_all_to_codex,
    install_all_to_cursor,
    install_all_to_openclaw,
    install_all_to_opencode,
    # ingest helpers
    ingest_claude_code_sessions,
    ingest_codex_sessions,
    ingest_cursor_sessions,
    ingest_openclaw_sessions,
    # canary hook
    make_openclaw_canary_flip_hook,
    # path helpers (used cross-package by watcher / team / tests)
    _cc_projects_path,
    _cc_skills_path,
    _codex_sessions_path,
    _agents_skills_path,
    _openclaw_agents_path,
    _cursor_projects_path,
    _cursor_skills_path,
    _opencode_db_path,
    # session-id / cwd / traj-id helpers (used by tests)
    _cc_traj_id,
    _codex_session_id_from_path,
    _openclaw_session_id_from_path,
    _cursor_session_id_from_path,
    _read_cwd_from_codex_jsonl,
    _read_cwd_from_cursor_jsonl,
    _read_workspace_dir_from_openclaw_jsonl,
    _sanitize_for_filename,
)
from xskill.ecosystems.adapt import (
    adapt_trajectory,
    submit_trajectory,
    generate_traj_id,
)

__all__ = [
    "EcosystemSpec", "SqliteEcosystemSpec",
    "CC_SPEC", "CODEX_SPEC", "OPENCLAW_SPEC", "CURSOR_SPEC", "OPENCODE_SPEC",
    "JsonlIngester", "SqliteIngester", "CCSessionIngester",
    "detect_known_ecosystems",
    "install_to_claude_code", "install_to_codex", "install_to_cursor",
    "install_to_openclaw", "install_to_opencode",
    "install_all_to_claude_code", "install_all_to_codex",
    "install_all_to_cursor", "install_all_to_openclaw",
    "install_all_to_opencode",
    "ingest_claude_code_sessions", "ingest_codex_sessions",
    "ingest_cursor_sessions", "ingest_openclaw_sessions",
    "make_openclaw_canary_flip_hook",
    "adapt_trajectory", "submit_trajectory", "generate_traj_id",
]
