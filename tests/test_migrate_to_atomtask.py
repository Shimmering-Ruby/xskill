"""scripts/migrate_to_atomtask.py 单测"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ 不在 sys.path 默认；测试时把它加进去
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_to_atomtask import nuke_legacy, list_legacy  # noqa: E402


class TestNukeLegacy:
    def test_removes_meta_and_index_keeps_md(self, tmp_path):
        d = tmp_path / "cc-sessions"
        d.mkdir()
        (d / "traj_x.md").write_text("real traj", encoding="utf-8")
        (d / "traj_x.md.meta").write_text("legacy meta", encoding="utf-8")
        (d / "index.pkl").write_bytes(b"legacy index")
        # 也建一个子目录里的 .md.meta（应一并删）
        sub = d / "sub"
        sub.mkdir()
        (sub / "traj_y.md.meta").write_text("legacy", encoding="utf-8")

        out = nuke_legacy(d)
        assert out["meta"] == 2
        assert out["index"] == 1
        assert (d / "traj_x.md").is_file()  # md 保留
        assert not (d / "traj_x.md.meta").exists()
        assert not (d / "index.pkl").exists()
        assert not (sub / "traj_y.md.meta").exists()

    def test_keeps_traj_json(self, tmp_path):
        """traj_*.json 保留（含 session_id / instance_id 等业务元数据）"""
        d = tmp_path / "cc-sessions"
        d.mkdir()
        (d / "traj_x.json").write_text('{"session_id":"abc"}', encoding="utf-8")
        (d / "traj_x.md.meta").write_text("legacy", encoding="utf-8")
        nuke_legacy(d)
        assert (d / "traj_x.json").is_file()
        assert not (d / "traj_x.md.meta").exists()

    def test_missing_root_returns_zeros(self, tmp_path):
        out = nuke_legacy(tmp_path / "does-not-exist")
        assert out == {"meta": 0, "index": 0}


class TestListLegacy:
    def test_counts_without_deleting(self, tmp_path):
        d = tmp_path / "x"; d.mkdir()
        (d / "traj_a.md.meta").write_text("x", encoding="utf-8")
        (d / "traj_b.md.meta").write_text("x", encoding="utf-8")
        (d / "index.pkl").write_bytes(b"x")
        out = list_legacy(d)
        assert out == {"meta": 2, "index": 1}
        # 文件仍在
        assert (d / "traj_a.md.meta").is_file()
        assert (d / "index.pkl").is_file()
