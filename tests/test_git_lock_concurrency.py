"""git_lock 并发串行化回归测试。

背景：git_lock.py 名为 lock 实则零加锁。watcher 线程（SkillEditAgent）
与线程池（cluster → init_skill_repo_on_baby）会并发对同一个 skill 的 .git
跑 git 命令，撞坏 .git/index 和 refs（实跑遇到：refs/heads/main 指向
empty-blob、.git/index 0 字节、3 个 skill 仓损坏）。

修复：run_git 对每个 cwd 取 per-repo RLock，任意两个 git 操作不会同时
操作同一个 repo；skill_repo_lock 给复合操作（add+commit+branch）用，
RLock 保证内部 run_git 可重入。

dulwich 迁移后 git.py 不再走 subprocess——这套测试通过 monkeypatch
dispatch handler 来观察并发情况：替换 ``status`` 的 handler 为有人为
延迟的探针，验证锁的串行化与 per-repo 粒度。
"""
from __future__ import annotations

import threading
import time

import xskill.skill.git as gitmod
from xskill.skill.git import run_git, skill_repo_lock


def _install_probe_handler(monkeypatch, sleep_s: float):
    """把 ``status`` 子命令换成一个能数并发的探针。"""
    active = {"count": 0, "max": 0}
    lk = threading.Lock()

    def fake_handler(args, cwd):
        with lk:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(sleep_s)
        with lk:
            active["count"] -= 1
        return 0, "", ""

    new_dispatch = dict(gitmod._DISPATCH)
    new_dispatch["status"] = fake_handler
    monkeypatch.setattr(gitmod, "_DISPATCH", new_dispatch)
    return active


def test_run_git_serializes_same_repo(tmp_path, monkeypatch):
    """同一个 repo 的 run_git 调用必须串行——不能有两个 git 操作同时跑。"""
    active = _install_probe_handler(monkeypatch, sleep_s=0.02)
    repo = str(tmp_path / "one-repo")
    threads = [threading.Thread(target=lambda: run_git(["status", "--porcelain"], cwd=repo))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1, (
        f"同一 repo 同时有 {active['max']} 个 git 操作——run_git 没串行化")


def test_run_git_different_repos_run_in_parallel(tmp_path, monkeypatch):
    """不同 repo 可以并发——锁是 per-repo 的，不是全局大锁。"""
    active = _install_probe_handler(monkeypatch, sleep_s=0.05)
    threads = [
        threading.Thread(target=lambda i=i: run_git(["status", "--porcelain"],
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
    _install_probe_handler(monkeypatch, sleep_s=0.0)
    repo = str(tmp_path / "r")
    with skill_repo_lock(repo):
        with skill_repo_lock(repo):          # 重入不死锁
            code, _, _ = run_git(["status", "--porcelain"], cwd=repo)   # 持锁时调 run_git 不死锁
            assert code == 0
