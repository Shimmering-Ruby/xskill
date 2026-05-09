"""
git_lock.py — Skill 仓库 git 操作
════════════════════════════════════
仅做 git 命令封装。所有操作直接在 main 分支上进行，不切分支。
原文件锁机制（acquire_lock / release_lock）已删，watcher 进程内串行调度。
"""

import os, subprocess, logging
from pathlib import Path

logger = logging.getLogger("git_lock")


def run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def ensure_repo(skill_dir: str):
    """确保 skill_dir 是一个 git 仓库，在 main 分支上"""
    p = Path(skill_dir)
    p.mkdir(parents=True, exist_ok=True)
    if not (p / ".git").exists():
        run_git(["init"], cwd=skill_dir)
        run_git(["checkout", "-b", "main"], cwd=skill_dir)
        run_git(["config", "user.email", "traj2skill@local"], cwd=skill_dir)
        run_git(["config", "user.name", "traj2skill"], cwd=skill_dir)
        (p / ".gitkeep").touch()
        (p / ".gitignore").write_text(
            "# canary runtime data — NOT versioned\n.ux_scores.jsonl\n.lock\n",
            encoding="utf-8",
        )
        run_git(["add", "."], cwd=skill_dir)
        run_git(["commit", "-m", "init skill repo"], cwd=skill_dir)
        logger.info(f"初始化 skill git 仓库: {skill_dir}")
    else:
        # 确保在 main 上
        _, cur, _ = run_git(["branch", "--show-current"], cwd=skill_dir)
        if cur != "main":
            run_git(["checkout", "--", "."], cwd=skill_dir)
            run_git(["clean", "-fd"], cwd=skill_dir)
            code, _, _ = run_git(["checkout", "main"], cwd=skill_dir)
            if code != 0:
                run_git(["checkout", "-b", "main"], cwd=skill_dir)
                (p / ".gitkeep").touch()
                run_git(["add", "."], cwd=skill_dir)
                run_git(["commit", "--allow-empty", "-m", "init"], cwd=skill_dir)
            if cur:
                run_git(["branch", "-D", cur], cwd=skill_dir)
            logger.info(f"🧹 修复: 回到 main，清理残留分支 {cur}")


def has_changes(skill_dir: str) -> bool:
    code, out, _ = run_git(["status", "--porcelain"], cwd=skill_dir)
    return bool(out)


def commit_changes(skill_dir: str, message: str) -> bool:
    run_git(["add", "-A"], cwd=skill_dir)
    code, out, _ = run_git(["diff", "--cached", "--name-only"], cwd=skill_dir)
    if not out and not has_changes(skill_dir):
        return False
    code, _, err = run_git(["commit", "-m", message], cwd=skill_dir)
    if code == 0:
        logger.info(f"📝 commit: {message}")
        return True
    logger.warning(f"commit 失败: {err}")
    return False


def current_branch(skill_dir: str) -> str:
    _, out, _ = run_git(["branch", "--show-current"], cwd=skill_dir)
    return out


def is_on_main(skill_dir: str) -> bool:
    return current_branch(skill_dir) == "main"


