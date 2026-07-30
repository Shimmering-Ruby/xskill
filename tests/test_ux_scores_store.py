"""ux_scores 盘→库同步与读路径。"""
from __future__ import annotations

import json

import pytest

from xskill.canary import AtomCanary, append_ux_score, recent_scores
from xskill.pipeline.ux_scores_store import (
    avg_scores_for_refs,
    insert_ux_score,
    load_all_usage_records,
    load_ux_scores_for_skill,
    sync_ux_scores_from_skill_dir,
)


@pytest.fixture
def reg_db():
    """使用 conftest 隔离后的 registry.db。"""
    from xskill.pipeline.registry import get_registry_db_path
    return get_registry_db_path()


def test_append_mirrors_to_db(tmp_path, reg_db):
    skill = tmp_path / "skill-a"
    skill.mkdir()
    ok = append_ux_score(
        skill,
        traj_id="traj-1",
        skill_name="skill-a",
        side="main",
        commit_sha="abc",
        score=8.0,
        reasons="ok",
    )
    assert ok is True
    rows = load_ux_scores_for_skill("skill-a", side="main", days=0, db_path=reg_db)
    assert len(rows) == 1
    assert rows[0]["score"] == 8.0
    assert rows[0]["commit_sha"] == "abc"


def test_append_idempotent_disk_and_db(tmp_path, reg_db):
    skill = tmp_path / "skill-a"
    skill.mkdir()
    kwargs = dict(
        traj_id="traj-1",
        skill_name="skill-a",
        side="main",
        commit_sha="abc",
        score=8.0,
        reasons="ok",
    )
    first = append_ux_score(skill, **kwargs)
    second = append_ux_score(skill, **kwargs)
    assert first is True
    assert second is False
    rows = load_ux_scores_for_skill("skill-a", days=0, db_path=reg_db)
    assert len(rows) == 1


def test_atom_canary_mirror_and_recent(tmp_path, reg_db):
    skill = tmp_path / "skill-b"
    skill.mkdir()
    ac = AtomCanary(skill_dir=skill)
    written = ac.append(
        atom_id="atom-1",
        skill_name="skill-b",
        side="main",
        commit_sha="def",
        score=9.0,
        reasons="good",
    )
    assert written is True
    recent = recent_scores(skill, side="main", commit_sha="def", n=5)
    assert len(recent) == 1
    assert recent[0]["score"] == 9.0


def test_sync_from_disk_jsonl(tmp_path, reg_db):
    root = tmp_path / "skills"
    d = root / "skill-c"
    d.mkdir(parents=True)
    rec = {
        "traj_id": "t-sync",
        "skill_name": "skill-c",
        "side": "main",
        "commit_sha": "sha1",
        "score": 7.5,
        "reasons": "",
        "scored_at": "2026-07-30T00:00:00+00:00",
    }
    (d / ".ux_scores.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    stats = sync_ux_scores_from_skill_dir(root, db_path=reg_db)
    assert stats["inserted"] == 1
    stats2 = sync_ux_scores_from_skill_dir(root, db_path=reg_db)
    assert stats2["inserted"] == 0
    assert stats2["skipped"] == 1
    avgs = avg_scores_for_refs(
        {"skill-c": "sha1"}, side="main", days=3650, db_path=reg_db,
    )
    assert avgs["skill-c"] == pytest.approx(7.5)


def test_avg_scores_for_refs_batches_over_1000_names(reg_db):
    """同一 sha 下 >1000 个 skill 名时必须分批 IN，不能踩 SQLite 变量上限。"""
    sha = "same-sha"
    n = 1200
    records = [
        {
            "skill_name": f"s{i:04d}",
            "side": "main",
            "commit_sha": sha,
            "score": 1.0 + (i % 5),
            "scored_at": "2026-07-30T00:00:00+00:00",
            "traj_id": f"t{i:04d}",
        }
        for i in range(n)
    ]
    from xskill.pipeline.ux_scores_store import insert_ux_scores_many
    assert insert_ux_scores_many(records, db_path=reg_db) == n
    refs = {f"s{i:04d}": sha for i in range(n)}
    avgs = avg_scores_for_refs(refs, side="main", days=3650, db_path=reg_db)
    assert len(avgs) == n
    assert avgs["s0000"] == pytest.approx(1.0)
    assert avgs["s0004"] == pytest.approx(5.0)


def test_load_all_usage_records(tmp_path, reg_db):
    insert_ux_score(
        {
            "skill_name": "s1",
            "side": "main",
            "commit_sha": "x",
            "score": 6.0,
            "scored_at": "2026-07-30T01:00:00+00:00",
            "traj_id": "t1",
        },
        db_path=reg_db,
    )
    rows = load_all_usage_records(db_path=reg_db)
    assert any(r["skill"] == "s1" and r["score"] == 6.0 for r in rows)
