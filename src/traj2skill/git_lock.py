"""
git_lock.py — Skill 仓库锁管理
════════════════════════════════
文件锁 (.lock) 控制并发，git 只做版本历史。
所有操作直接在 main 分支上进行，不切分支。
"""

import atexit, os, signal, subprocess, time, logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("git_lock")

_lock_file: Path | None = None


def _cleanup_on_exit():
    """进程退出时释放锁"""
    global _lock_file
    if _lock_file and _lock_file.exists():
        try:
            pid = int(_lock_file.read_text().strip())
            if pid == os.getpid():
                _lock_file.unlink()
                logger.info(f"🧹 atexit 清理锁: {_lock_file}")
        except Exception:
            pass
    _lock_file = None


atexit.register(_cleanup_on_exit)

for _sig in (signal.SIGTERM, signal.SIGINT):
    try:
        _prev = signal.getsignal(_sig)
        def _handler(signum, frame, prev=_prev):
            _cleanup_on_exit()
            if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
                prev(signum, frame)
            raise SystemExit(128 + signum)
        signal.signal(_sig, _handler)
    except (OSError, ValueError):
        pass


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


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock(skill_dir: str, name: str = "", timeout: int = 60) -> bool:
    """获取文件锁"""
    global _lock_file
    p = Path(skill_dir)
    lock = p / ".lock"
    start = time.time()

    while True:
        # 检查是否有人持锁
        if lock.exists():
            try:
                content = lock.read_text().strip()
                pid = int(content)
                if not _is_process_alive(pid):
                    logger.warning(f"🧹 死锁清理: pid={pid} 已不存在")
                    lock.unlink()
                else:
                    elapsed = time.time() - start
                    if elapsed > timeout:
                        logger.error(f"⏰ 获取锁超时 ({timeout}s)，持锁 pid={pid}")
                        return False
                    logger.info(f"⏳ 等待锁释放 (pid={pid}, 已等 {elapsed:.0f}s)...")
                    time.sleep(2)
                    continue
            except (ValueError, FileNotFoundError):
                # 锁文件损坏或已被删除
                if lock.exists():
                    lock.unlink()

        # 写入锁
        try:
            lock.write_text(str(os.getpid()))
            _lock_file = lock
            logger.info(f"🔒 获取锁: pid={os.getpid()} ({name})")
            return True
        except Exception as e:
            logger.error(f"写锁失败: {e}")
            return False


def release_lock(skill_dir: str, **kwargs):
    """释放文件锁"""
    global _lock_file
    lock = Path(skill_dir) / ".lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            if pid == os.getpid():
                lock.unlink()
                logger.info(f"🔓 释放锁")
        except Exception:
            lock.unlink()
    _lock_file = None


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


def make_branch_name(skill_name: str = "auto") -> str:
    """保留接口兼容，现在只做日志标识用"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{skill_name}/{ts}"


def merge_to_main(skill_dir: str, branch_name: str = "") -> bool:
    """不再需要 merge，直接在 main 上 commit，保留接口兼容"""
    return True
