"""skill_tools v2 atom-era 工具集单测"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill import skill_tools as ST
from tests.test_atom_task_store import _FakeEmbed


def _setup(tmp_path: Path) -> tuple[Path, AtomTaskStore]:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    store_root = tmp_path / "cc-sessions"
    store_root.mkdir()
    store = AtomTaskStore(root=store_root)
    atom = AtomTask(
        atom_id="atom_x_0001", traj_id="x",
        offset_start=0, offset_end=10,
        intent="修 django migration", summary="跑了 makemigrations 找冲突",
        tags=["django"], used_skills=[], ux_score=7,
        pre_atom_id=None, post_atom_id=None,
        context_prefix="", raw_segment="MIGRATIONS!",
    )
    store.save(atom)
    store.rebuild_vector_index(_FakeEmbed())
    # 一条 traj.md，给 read_traj 测试用
    (store_root / "x.md").write_text("0123456789ABCDEFGHIJ" * 5, encoding="utf-8")
    ST.init_context_v2(
        skill_dir=skill_dir, store=store,
        embed_client=_FakeEmbed(), traj_root=store_root,
    )
    return skill_dir, store


class TestAtomTaskRead:
    def test_returns_atom_json(self, tmp_path):
        _setup(tmp_path)
        out = ST.atom_task_read("atom_x_0001")
        assert "atom_x_0001" in out
        assert "makemigrations" in out

    def test_not_found_returns_error(self, tmp_path):
        _setup(tmp_path)
        out = ST.atom_task_read("atom_nonexistent")
        assert out.startswith("error")


class TestAtomTaskSearch:
    def test_returns_hits_in_json(self, tmp_path):
        _setup(tmp_path)
        out = ST.atom_task_search("makemigrations")
        assert "atom_x_0001" in out


class TestReadTraj:
    def test_returns_slice(self, tmp_path):
        _setup(tmp_path)
        out = ST.read_traj("x", offset_start=0, offset_end=5)
        assert out == "01234"

    def test_invalid_range_returns_error(self, tmp_path):
        _setup(tmp_path)
        out = ST.read_traj("x", offset_start=10, offset_end=5)
        assert out.startswith("error")

    def test_out_of_bounds_returns_error(self, tmp_path):
        _setup(tmp_path)
        out = ST.read_traj("x", offset_start=0, offset_end=999999)
        assert out.startswith("error")

    def test_nonexistent_traj_returns_error(self, tmp_path):
        _setup(tmp_path)
        out = ST.read_traj("doesnt-exist", offset_start=0, offset_end=5)
        assert out.startswith("error")


class TestNewSkillFolder:
    def test_creates_directory_with_skeleton(self, tmp_path):
        skill_dir, _ = _setup(tmp_path)
        msg = ST.new_skill_folder("my-new-skill")
        assert "created" in msg
        assert (skill_dir / "my-new-skill" / "scripts").is_dir()
        assert (skill_dir / "my-new-skill" / "references").is_dir()

    def test_repeated_call_returns_already_exists(self, tmp_path):
        _setup(tmp_path)
        ST.new_skill_folder("dup-skill")
        msg = ST.new_skill_folder("dup-skill")
        assert "already exists" in msg


class TestAddTaskToSkill:
    def test_first_add_creates_entry(self, tmp_path):
        skill_dir, _ = _setup(tmp_path)
        ST.new_skill_folder("auto-skill")
        msg = ST.add_task_to_skill("auto-skill", "atom_x_0001", 6)
        assert "weightscore_total=6" in msg

    def test_repeated_add_sums(self, tmp_path):
        _setup(tmp_path)
        ST.new_skill_folder("auto-skill")
        ST.add_task_to_skill("auto-skill", "atom_x_0001", 4)
        msg = ST.add_task_to_skill("auto-skill", "atom_x_0001", 5)
        assert "weightscore_total=9" in msg

    def test_nonexistent_skill_returns_error(self, tmp_path):
        _setup(tmp_path)
        msg = ST.add_task_to_skill("nonexistent", "atom_x_0001", 5)
        assert msg.startswith("error")

    def test_weightscore_out_of_range_returns_error(self, tmp_path):
        _setup(tmp_path)
        ST.new_skill_folder("auto-skill")
        for bad in [0, 11, -1, 100]:
            msg = ST.add_task_to_skill("auto-skill", "atom_x_0001", bad)
            assert msg.startswith("error")


class TestScoreTask:
    def test_score_task_updates_atom(self, tmp_path):
        _, store = _setup(tmp_path)
        msg = ST.score_task("atom_x_0001", 9)
        assert "9" in msg
        assert store.load("atom_x_0001").ux_score == 9

    def test_invalid_score_returns_error(self, tmp_path):
        _setup(tmp_path)
        for bad in [0, 11, -3]:
            msg = ST.score_task("atom_x_0001", bad)
            assert msg.startswith("error")


class TestSkillRead:
    def test_empty_skill_returns_placeholder(self, tmp_path):
        _setup(tmp_path)
        ST.new_skill_folder("brand-new")
        msg = ST.skill_read("brand-new")
        assert "no SKILL.md" in msg or "placeholder" in msg

    def test_existing_skill_returns_content(self, tmp_path):
        skill_dir, _ = _setup(tmp_path)
        ST.new_skill_folder("has-content")
        (skill_dir / "has-content" / "SKILL.md").write_text(
            "---\nname: has-content\n---\n# body here\n", encoding="utf-8",
        )
        msg = ST.skill_read("has-content")
        assert "body here" in msg


class TestAddTask:
    def test_writes_synthetic_atom(self, tmp_path):
        _, store = _setup(tmp_path)
        msg = ST.add_task(
            atom_id="atom_manual_0001", traj_id="manual",
            offset_start=0, offset_end=5,
            intent="manual intent", summary="manual summary",
            tags=["t1"], used_skills=[], ux_score=8,
        )
        assert "added" in msg
        a = store.load("atom_manual_0001")
        assert a.intent == "manual intent"
        assert a.ux_score == 8
