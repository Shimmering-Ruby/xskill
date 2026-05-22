"""git_lock 并发串行化回归测试。

背景：git_lock.py 名为 lock 实则零加锁。watcher 线程（SkillEditAgent）
与线程池（cluster → init_skill_repo_on_baby）会并发对同一个 skill 的 .git
跑 git 命令，撞坏 .git/index 和 refs（实跑遇到：refs/heads/main 指向
empty-blob、.git/index 0 字节、3 个 skill 仓损坏）。

修复：run_git 对每个 cwd 取 per-repo RLock，任意两个 git 进程不会同时
操作同一个 repo；skill_repo_lock 给复合操作（add+commit+branch）用，
RLock 保证内部 run_git 可重入。
"""
from __future__ import annotations

import threading
import time
import types

from xskill.skill.git import run_git, skill_repo_lock


def test_run_git_serializes_same_repo(tmp_path, monkeypatch):
    """同一个 repo 的 run_git 调用必须串行——不能有两个 git 进程同时跑。"""
    active = {"count": 0, "max": 0}
    lk = threading.Lock()

    def fake_run(*args, **kwargs):
        with lk:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(0.02)
        with lk:
            active["count"] -= 1
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("xskill.skill.git.subprocess.run", fake_run)
    repo = str(tmp_path / "one-repo")
    threads = [threading.Thread(target=lambda: run_git(["status"], cwd=repo))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1, (
        f"同一 repo 同时有 {active['max']} 个 git 进程——run_git 没串行化")


def test_run_git_different_repos_run_in_parallel(tmp_path, monkeypatch):
    """不同 repo 可以并发——锁是 per-repo 的，不是全局大锁。"""
    active = {"count": 0, "max": 0}
    lk = threading.Lock()

    def fake_run(*args, **kwargs):
        with lk:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(0.05)
        with lk:
            active["count"] -= 1
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("xskill.skill.git.subprocess.run", fake_run)
    threads = [
        threading.Thread(target=lambda i=i: run_git(["status"],
                                                    cwd=str(tmp_path / f"repo-{i}")))
        for i in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] > 1, "不同 repo 也被串行了——锁粒度错了，应是 per-repo"


def test_skill_repo_lock_reentrant_and_allows_inner_run_git(tmp_path, monkeypatch):
    """skill_repo_lock 可重入；持锁时内部 run_git 不死锁（同一把 RLock）。"""
    monkeypatch.setattr(
        "xskill.skill.git.subprocess.run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    repo = str(tmp_path / "r")
    with skill_repo_lock(repo):
        with skill_repo_lock(repo):          # 重入不死锁
            code, _, _ = run_git(["status"], cwd=repo)   # 持锁时调 run_git 不死锁
            assert code == 0
