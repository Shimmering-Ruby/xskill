"""团队客户端在 import 之后跟到这次导入产生的主干。"""
from __future__ import annotations

import logging
from pathlib import Path

from xskill.ecosystems._history import (
    InstallHistory,
    InstallHistoryCorruptError,
)
from xskill.team.client.daemon import install_skill_to_ecosystems
from xskill.team.shared.git_bundle import apply_repo_bundle
from xskill.team.shared.reconcile import reconcile_skill_side

logger = logging.getLogger("xskill.team.client.import_follow")


def follow_imported_skill(
    *,
    http,
    headers: dict,
    skill_dir: Path,
    name: str,
    sha: str,
    home_root: Path,
    history_path: Path,
) -> None:
    """拉 bundle，把本机 ``_active`` 切到这次导入的主干，并装到编程代理目录。

    安装历史只用于灰度归因。账本坏了也要把技能装进 harness，不能让用户
    看见「纳入失败」。
    """
    if not sha:
        raise RuntimeError(f"import follow missing sha for {name}")
    response = http.get(f"/api/v1/team/skill/{name}/bundle", headers=headers)
    if response.status_code != 200:
        raise RuntimeError(
            f"import follow bundle failed HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    dest = Path(skill_dir) / name
    apply_repo_bundle(response.content, dest)
    result = "bundle_applied"
    try:
        history = InstallHistory(history_path)
        result = reconcile_skill_side(
            repo_dir=dest,
            target_side="main",
            target_sha=sha,
            history=history,
            on_changed=lambda repo: install_skill_to_ecosystems(
                repo, home_root=home_root,
            ),
        )
    except (InstallHistoryCorruptError, OSError) as history_error:
        logger.warning(
            "import follow history skipped for %s: %s",
            name,
            history_error,
        )
        result = "history_skipped"
    if result != "checked_out" and result != "skipped_user_edit":
        install_skill_to_ecosystems(dest, home_root=home_root)
    logger.info("import follow %s -> main %s (%s)", name, sha[:8], result)
