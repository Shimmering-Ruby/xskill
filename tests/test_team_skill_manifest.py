from __future__ import annotations

import subprocess
from pathlib import Path

from xskill.team.server.skill_manifest import build_manifest


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(root: Path, name: str, *, with_staging: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d)
    _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d)
    _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8",
    )
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "v1"], d)
    if with_staging:
        _git(["checkout", "-q", "-b", "staging"], d)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d2\nmetadata:\n  version: 2\n---\n# {name} v2\n",
            encoding="utf-8",
        )
        _git(["commit", "-q", "-am", "v2"], d)
        _git(["checkout", "-q", "main"], d)
    return d


def _make_baby_skill(root: Path, name: str) -> Path:
    """造一个还在 baby 分支的 stub skill——cluster 建了但 SkillEditAgent
    还没跑过，没有 main 分支。"""
    d = root / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d)
    _git(["checkout", "-q", "-b", "baby"], d)
    _git(["config", "user.email", "t@t"], d)
    _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: stub\nmetadata:\n  version: 0\n---\n# {name}\n",
        encoding="utf-8",
    )
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "init baby"], d)
    return d


def test_manifest_skips_baby_skill_without_main(tmp_path):
    """baby-state stub 没有 main 分支 → 不进 manifest，不抛错。"""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "graduated")
    _make_baby_skill(skill_dir, "still-baby")
    resp = build_manifest(client_id="cid-1", skill_dir=skill_dir,
                          probability=0.2, ranked_slots=80, total_slots=100)
    names = [s.skill_name for s in resp.slots]
    assert names == ["graduated"]            # 只发已 graduate 的
    assert "still-baby" not in names


def test_manifest_caps_total_slots(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    for i in range(150):
        _make_skill(skill_dir, f"skill-{i:03d}")
    resp = build_manifest(client_id="cid-1", skill_dir=skill_dir,
                          probability=0.2, ranked_slots=80, total_slots=100)
    assert len(resp.slots) == 100
    ranked = [s for s in resp.slots if s.bucket == "ranked"]
    recommended = [s for s in resp.slots if s.bucket == "recommended"]
    assert len(ranked) == 80 and len(recommended) == 20


def test_manifest_main_only_skill_has_main_side(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "no-staging")
    resp = build_manifest(client_id="cid-1", skill_dir=skill_dir,
                          probability=0.2, ranked_slots=80, total_slots=100)
    assert len(resp.slots) == 1
    assert resp.slots[0].side == "main"
    assert resp.slots[0].sha   # 非空


def test_manifest_staging_side_is_deterministic_per_client(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "graying", with_staging=True)
    s1 = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                        probability=0.2, ranked_slots=80, total_slots=100).slots[0]
    s2 = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                        probability=0.2, ranked_slots=80, total_slots=100).slots[0]
    assert s1.side == s2.side          # 同 client 同 skill 永远同 side
    assert s1.side in ("main", "staging")
    # probability=1.0 → 必 staging；sha 必须是 staging HEAD
    forced = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                            probability=1.0, ranked_slots=80, total_slots=100).slots[0]
    assert forced.side == "staging"
