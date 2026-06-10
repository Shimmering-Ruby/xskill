"""tests/test_rebuild.py — `xskill rebuild` 重置轨迹 + --force 清仓清原子（子项目 A）"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xskill.pipeline.registry import (
    register_dir,
    discover_trajectories,
    update_traj_status,
    update_traj_offset,
    get_trajs_by_status,
    reset_trajectories,
)
from xskill.skill.repo import SkillRepo


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "reg.db"


def _seed_done_traj(tmp_path, db_path, *, with_atoms=False):
    d = tmp_path / "ngagent_sessions"
    d.mkdir()
    (d / "traj_ng_x.md").write_text("# traj\n## User\nhi\n", encoding="utf-8")
    wid = register_dir(d, label="ngagent", ecosystem="ngagent", db_path=db_path)
    discover_trajectories(wid, d, db_path=db_path)
    update_traj_status(wid, "traj_ng_x.md", "done", db_path=db_path)
    update_traj_offset(wid, "traj_ng_x.md", last_offset=500,
                       last_atom_id="atom_traj_ng_x_0002", tasks_extracted=2,
                       db_path=db_path)
    if with_atoms:
        tasks = d / "traj_ng_x" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "atom_traj_ng_x_0001.json").write_text(
            json.dumps({"atom_id": "atom_traj_ng_x_0001"}), encoding="utf-8")
        (tasks / "atom_traj_ng_x_0002.json").write_text(
            json.dumps({"atom_id": "atom_traj_ng_x_0002"}), encoding="utf-8")
    return d, wid


def test_reset_flips_status_to_discovered_and_zeros_offset(tmp_path, db_path):
    _d, wid = _seed_done_traj(tmp_path, db_path)
    assert "traj_ng_x.md" not in get_trajs_by_status(wid, "discovered", db_path=db_path)

    n = reset_trajectories(eco="ngagent", db_path=db_path)

    assert n == 1
    assert "traj_ng_x.md" in get_trajs_by_status(wid, "discovered", db_path=db_path)


def test_reset_eco_filter_skips_other_ecosystems(tmp_path, db_path):
    _seed_done_traj(tmp_path, db_path)
    # 另一个生态的轨迹不应被 eco=ngagent 的重置波及
    other = tmp_path / "cc_sessions"
    other.mkdir()
    (other / "traj_cc_y.md").write_text("# t", encoding="utf-8")
    wid2 = register_dir(other, ecosystem="claude_code", db_path=db_path)
    discover_trajectories(wid2, other, db_path=db_path)
    update_traj_status(wid2, "traj_cc_y.md", "done", db_path=db_path)

    n = reset_trajectories(eco="ngagent", db_path=db_path)

    assert n == 1
    assert "traj_cc_y.md" not in get_trajs_by_status(wid2, "discovered", db_path=db_path)


def test_force_wipe_atoms_deletes_atom_files(tmp_path, db_path):
    d, _wid = _seed_done_traj(tmp_path, db_path, with_atoms=True)
    tasks = d / "traj_ng_x" / "tasks"
    assert list(tasks.glob("atom_*.json"))

    reset_trajectories(eco="ngagent", wipe_atoms=True, db_path=db_path)

    assert not list(tasks.glob("atom_*.json")), "wipe_atoms 应删光原子文件"


def test_reset_without_wipe_keeps_atoms(tmp_path, db_path):
    d, _wid = _seed_done_traj(tmp_path, db_path, with_atoms=True)
    tasks = d / "traj_ng_x" / "tasks"

    reset_trajectories(eco="ngagent", wipe_atoms=False, db_path=db_path)

    assert list(tasks.glob("atom_*.json")), "不带 force 不应删原子"


def test_wipe_all_skills_removes_skill_dirs_keeps_references(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    foo = root / "foo"
    foo.mkdir()
    (foo / "SKILL.md").write_text("---\nname: foo\ndescription: d\n---\n", encoding="utf-8")
    bar = root / "bar"
    (bar / ".git").mkdir(parents=True)  # baby 态：只有 .git 还没 SKILL.md
    refs = root / "references"
    refs.mkdir()
    (refs / "x.md").write_text("keep me", encoding="utf-8")

    n = SkillRepo(root).wipe_all_skills()

    assert n == 2
    assert not foo.exists() and not bar.exists()
    assert refs.exists(), "references 不应被删"
