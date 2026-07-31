"""skill_dir 合扫：一轮 iterdir 同时刷 UX + pending，mtime 跳过。"""

from __future__ import annotations

import json
from pathlib import Path

from xskill.pipeline import registry as reg
from xskill.pipeline.skill_dir_sync import sync_skill_disk_projections
from xskill.skill.candidates import CANDIDATES_FILENAME


def _write_skill(skill_dir: Path, name: str) -> Path:
    path = skill_dir / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: t\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return path


def test_one_pass_syncs_ux_and_pending_with_mtime_skip(tmp_path: Path) -> None:
    db = tmp_path / "registry.db"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    foo = _write_skill(skill_dir, "foo")

    (foo / CANDIDATES_FILENAME).write_text(
        "candidates:\n"
        "  - atom_id: atom_t1_0001\n"
        "    weightscore: 4\n",
        encoding="utf-8",
    )
    rec = {
        "traj_id": "t-sync",
        "skill_name": "foo",
        "side": "main",
        "commit_sha": "sha1",
        "score": 6.0,
        "reasons": "",
        "scored_at": "2026-07-30T00:00:00+00:00",
    }
    (foo / ".ux_scores.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8",
    )

    stats = sync_skill_disk_projections(skill_dir, db_path=db)
    assert stats["ux"]["inserted"] == 1
    assert stats["pending"]["synced"] == 1
    assert stats["pending"]["rows"] == 1

    with reg.pooled_connection(db) as conn:
        pending = list(conn.execute(
            "SELECT atom_id, skill, weightscore FROM atom_candidate_pending",
        ))
        ready = conn.execute(
            "SELECT 1 FROM atom_candidate_pending_meta WHERE root_key=?",
            (reg._atom_pending_root_key(skill_dir),),
        ).fetchone()
    assert len(pending) == 1
    assert pending[0]["atom_id"] == "atom_t1_0001"
    assert pending[0]["weightscore"] == 4
    assert ready is not None

    stats2 = sync_skill_disk_projections(skill_dir, db_path=db)
    assert stats2["ux"]["inserted"] == 0
    assert stats2["ux"]["skipped"] == 1
    assert stats2["pending"]["synced"] == 0
    assert stats2["pending"]["skipped"] == 1


def test_reconcile_picks_up_direct_yaml_edit_and_purges_orphan(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry.db"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    foo = _write_skill(skill_dir, "foo")
    bar = _write_skill(skill_dir, "bar")

    reg.sync_atom_candidate_pending_for_skill(
        "foo",
        [{"atom_id": "atom_old_0001", "weightscore": 1}],
        db_path=db,
    )
    reg.sync_atom_candidate_pending_for_skill(
        "gone",
        [{"atom_id": "atom_gone_0001", "weightscore": 9}],
        db_path=db,
    )

    (foo / CANDIDATES_FILENAME).write_text(
        "candidates:\n"
        "  - atom_id: atom_new_0001\n"
        "    weightscore: 8\n",
        encoding="utf-8",
    )
    # bar 无 candidates → 应保持空；gone 盘上不存在 → orphan 清除

    stats = sync_skill_disk_projections(skill_dir, db_path=db)
    assert stats["pending"]["orphans"] == 1

    with reg.pooled_connection(db) as conn:
        rows = {
            r["atom_id"]: r["skill"]
            for r in conn.execute(
                "SELECT atom_id, skill FROM atom_candidate_pending",
            )
        }
    assert rows == {"atom_new_0001": "foo"}
    assert "atom_gone_0001" not in rows
    assert "bar" not in rows.values()
