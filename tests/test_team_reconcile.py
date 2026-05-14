import os
import subprocess
from pathlib import Path

from xskill.team.reconcile import reconcile_skill_side
from xskill.install_history import InstallHistory


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def _backdate_worktree(root: Path) -> None:
    """把工作树文件 mtime 压到 epoch 0——freshly-seeded 仓库没有真实用户
    手改，但末尾的 git checkout 会把 mtime 抬到 commit_ts 之后，负载高时
    可能 ≥1s 触发 has_pending_user_edit 误判。压低 mtime 让判定稳定为 False。
    """
    for f in root.rglob("*"):
        if ".git" in f.parts or not f.is_file():
            continue
        os.utime(f, (0, 0))


def _seed(root: Path) -> tuple[Path, str, str]:
    root.mkdir(parents=True)
    _git(["init", "-q"], root); _git(["checkout", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root); _git(["config", "user.name", "t"], root)
    (root / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], root); _git(["commit", "-q", "-m", "v1"], root)
    main_sha = _git(["rev-parse", "HEAD"], root)
    _git(["checkout", "-q", "-b", "staging"], root)
    (root / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], root)
    staging_sha = _git(["rev-parse", "HEAD"], root)
    _git(["checkout", "-q", "main"], root)
    _backdate_worktree(root)
    return root, main_sha, staging_sha


def test_checks_out_target_and_records(tmp_path):
    repo, main_sha, staging_sha = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    changed = []
    res = reconcile_skill_side(repo_dir=repo, target_side="staging",
                               target_sha=staging_sha, history=hist,
                               on_changed=lambda p: changed.append(p))
    assert res == "checked_out"
    assert _git(["rev-parse", "HEAD"], repo) == staging_sha
    assert (repo / "SKILL.md").read_text() == "v2"
    assert changed == [repo]
    assert hist.count_by_side()["staging"] == 1


def test_already_aligned_records_history_but_no_checkout(tmp_path):
    """已对齐 → 不 checkout 工作区，但仍记一条 install_history（时间序列）。"""
    repo, main_sha, _ = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    res = reconcile_skill_side(repo_dir=repo, target_side="main",
                               target_sha=main_sha, history=hist, on_changed=None)
    assert res == "already_aligned"
    # 不 checkout：HEAD 仍是原 main 分支（没切到 _active）
    assert _git(["branch", "--show-current"], repo) == "main"
    # 但 install_history 记了一条——下游 lookup(t) 反查需要
    assert hist.count_by_side()["main"] == 1


def test_skips_pending_user_edit(tmp_path, monkeypatch):
    repo, main_sha, staging_sha = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    monkeypatch.setattr("xskill.team.reconcile.has_pending_user_edit", lambda d: True)
    res = reconcile_skill_side(repo_dir=repo, target_side="staging",
                               target_sha=staging_sha, history=hist, on_changed=None)
    assert res == "skipped_user_edit"
    assert _git(["rev-parse", "HEAD"], repo) == main_sha   # 没动
