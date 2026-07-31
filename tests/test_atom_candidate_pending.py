"""atom_candidate_pending 投影：写出口同步 + 看板读路径不扫盘。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from xskill.dashboard.explore import TrajExplorer
from xskill.pipeline import registry as reg
from xskill.skill.candidates import (
    CANDIDATES_FILENAME,
    add_atom_contributions,
    remove_candidates,
)


def _write_skill(skill_dir: Path, name: str) -> Path:
    path = skill_dir / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: t\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return path


def test_candidates_save_syncs_pending_projection(tmp_path: Path) -> None:
    db = tmp_path / "registry.db"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    foo = _write_skill(skill_dir, "foo")
    bar = _write_skill(skill_dir, "bar")

    with mock.patch(
        "xskill.skill.catalog_store.resolve_catalog_db_path",
        return_value=db,
    ):
        add_atom_contributions(foo, [("atom_t1_0001", 7, "")])
        add_atom_contributions(bar, [("atom_t1_0002", 3, "")])

    with reg.pooled_connection(db) as conn:
        rows = {
            r["atom_id"]: (r["skill"], r["weightscore"])
            for r in conn.execute(
                "SELECT atom_id, skill, weightscore FROM atom_candidate_pending",
            )
        }
    assert rows["atom_t1_0001"] == ("foo", 7)
    assert rows["atom_t1_0002"] == ("bar", 3)

    with mock.patch(
        "xskill.skill.catalog_store.resolve_catalog_db_path",
        return_value=db,
    ):
        remove_candidates(foo, {"atom_t1_0001"})

    with reg.pooled_connection(db) as conn:
        left = [
            r["atom_id"]
            for r in conn.execute(
                "SELECT atom_id FROM atom_candidate_pending",
            )
        ]
    assert left == ["atom_t1_0002"]


def test_atom_destinations_reads_pending_from_db_without_iterdir(
    tmp_path: Path,
) -> None:
    db = tmp_path / "registry.db"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _write_skill(skill_dir, "foo")
    reg.sync_atom_candidate_pending_for_skill(
        "foo",
        [{"atom_id": "atom_t9_0001", "weightscore": 5}],
        db_path=db,
    )
    # 标记已 backfill，避免读路径再扫盘
    with reg.pooled_connection(db) as conn:
        conn.execute(
            "INSERT INTO atom_candidate_pending_meta(root_key, backfilled_at)"
            " VALUES (?, datetime('now'))",
            (reg._atom_pending_root_key(skill_dir),),
        )
        conn.commit()

    explorer = TrajExplorer(db_path=db, skill_dir=skill_dir)
    with mock.patch.object(Path, "iterdir", side_effect=AssertionError("扫盘")):
        dests = explorer._atom_destinations("atom_t9_0001")
    assert dests == [{
        "skill": "foo",
        "weightscore": 5,
        "state": "pending",
        "ts": "",
    }]
    # 磁盘上甚至可以没有 candidates 文件
    assert not (skill_dir / "foo" / CANDIDATES_FILENAME).exists()
