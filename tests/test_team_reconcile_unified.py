"""单机 watcher reconcile 收敛到共享 reconcile_skill_side 的回归测试。

单机 _reconcile_skill_sides 现在：步骤 1（节流 + 时间窗 pick_side）保留，
步骤 2/3/4 委托 team.reconcile.reconcile_skill_side——checkout 到 _active
分支（指向 resolved sha），而非直接 checkout main/staging 分支名。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from xskill.pipeline.runner import DirectoryWatcher


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def _seed_skill_with_staging(skill_dir: Path, name: str) -> Path:
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    _git(["checkout", "-q", "-b", "staging"], d)
    (d / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], d)
    _git(["checkout", "-q", "main"], d)
    # 工作树 mtime 压到 epoch 0——末尾 git checkout 抬升的 mtime 在负载高时
    # 可能 ≥1s 触发 has_pending_user_edit 误判；freshly-seeded 仓库无真实手改。
    for f in d.rglob("*"):
        if ".git" not in f.parts and f.is_file():
            os.utime(f, (0, 0))
    return d


def test_single_machine_reconcile_checks_out_active_branch(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    d = _seed_skill_with_staging(skill_dir, "graying")
    w = DirectoryWatcher(skill_dir=skill_dir,
                         config={"canary": {"probability": 1.0, "rotate_interval": 1}},
                         install_history_path=tmp_path / "history.jsonl")
    # probability=1.0 → 必选 staging
    w._reconcile_skill_sides()
    # 工作树应是 staging 内容，HEAD 在 _active 分支
    assert (d / "SKILL.md").read_text() == "v2"
    assert _git(["branch", "--show-current"], d) == "_active"


def test_single_machine_reconcile_records_history(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _seed_skill_with_staging(skill_dir, "graying")
    hist_path = tmp_path / "history.jsonl"
    w = DirectoryWatcher(skill_dir=skill_dir,
                         config={"canary": {"probability": 1.0, "rotate_interval": 1}},
                         install_history_path=hist_path)
    w._reconcile_skill_sides()
    from xskill.ecosystems._history import InstallHistory
    hist = InstallHistory(hist_path)
    assert hist.count_by_side(skill="graying")["staging"] >= 1
