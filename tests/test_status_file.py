"""块 5 跨进程状态文件:原子写/读回、缺失返回 None、写失败落日志不抛。"""
from __future__ import annotations

from xskill.utils.status_file import read_status_file, write_status_file


def test_write_then_read_roundtrip(tmp_path):
    path = tmp_path / "watcher_status.json"
    write_status_file(path, {"polls": 3, "new_trajs": 1}, ok=True)
    loaded = read_status_file(path)
    assert loaded["ok"] is True
    assert loaded["error"] is None
    assert loaded["stats"] == {"polls": 3, "new_trajs": 1}
    assert isinstance(loaded["ended_at"], (int, float))


def test_write_records_error(tmp_path):
    path = tmp_path / "profile_refresh_status.json"
    write_status_file(path, {}, ok=False, error="boom")
    loaded = read_status_file(path)
    assert loaded["ok"] is False
    assert loaded["error"] == "boom"


def test_read_missing_returns_none(tmp_path):
    assert read_status_file(tmp_path / "never_written.json") is None


def test_write_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "watcher_status.json"
    write_status_file(path, {"ok": 1}, ok=True)
    assert path.is_file()
    assert not (tmp_path / "watcher_status.json.tmp").exists()


def test_write_failure_logs_and_does_not_raise(tmp_path, caplog):
    # 目标路径的父目录不存在 → os 层写失败;必须落 warning、不抛(状态非关键路径)。
    bad_path = tmp_path / "missing_dir" / "watcher_status.json"
    with caplog.at_level("WARNING"):
        write_status_file(bad_path, {"x": 1}, ok=True)
    assert any("写状态文件失败" in record.getMessage() for record in caplog.records)
    assert not bad_path.exists()
