"""tests/test_db_read.py — `xskill read <PATH>` 批量 db 入库（子项目 B-1）"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.pipeline.db_ingest import read_db_files

FIXTURE_DB = Path(__file__).parent / "fixtures" / "opencode" / "sample.db"


def test_read_single_db_bridges_into_eco_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "ngagent.db"
    db.write_bytes(FIXTURE_DB.read_bytes())

    summary = read_db_files(db, eco="ngagent", home_root=home, register=False)

    assert summary["bridged"] >= 1
    bridge = home / ".xskill" / "ngagent_sessions"
    mds = list(bridge.glob("traj_ng_*.md"))
    assert mds, "no bridged traj_ng_*.md produced"


def test_read_directory_picks_up_all_db_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    updir = tmp_path / "uploads"
    updir.mkdir()
    (updir / "a.db").write_bytes(FIXTURE_DB.read_bytes())
    (updir / "b.db").write_bytes(FIXTURE_DB.read_bytes())

    summary = read_db_files(updir, eco="ngagent", home_root=home, register=False)

    assert len(summary["db_files"]) == 2


def test_read_idempotent_same_db_twice(tmp_path):
    """同一 db 读两次：第二次不应重复桥接（_seen 按 bridge dir 去重）。"""
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "ngagent.db"
    db.write_bytes(FIXTURE_DB.read_bytes())

    first = read_db_files(db, eco="ngagent", home_root=home, register=False)
    second = read_db_files(db, eco="ngagent", home_root=home, register=False)

    assert first["bridged"] >= 1
    assert second["bridged"] == 0, "再读同一 db 不应重复桥接"


def test_read_rejects_non_sqlite_eco(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(FIXTURE_DB.read_bytes())
    with pytest.raises(ValueError):
        read_db_files(db, eco="claude_code", home_root=tmp_path, register=False)


def test_read_empty_dir_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        read_db_files(empty, eco="ngagent", home_root=tmp_path, register=False)
