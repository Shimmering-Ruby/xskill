from __future__ import annotations

from xskill.pipeline.runner import DirectoryWatcher


def test_server_mode_flag_defaults_false(tmp_path):
    watcher = DirectoryWatcher(home_root=tmp_path)
    assert watcher.server_mode is False


def test_server_mode_skips_user_edit_and_reconcile(monkeypatch, tmp_path):
    watcher = DirectoryWatcher(server_mode=True, home_root=tmp_path)
    calls = []
    monkeypatch.setattr(watcher, "_harvest", lambda: calls.append("harvest"))
    monkeypatch.setattr(
        watcher,
        "_check_pending_skill_edits",
        lambda **_ignored_keyword_arguments: calls.append("edits"),
    )
    monkeypatch.setattr(watcher, "_check_canary_decisions", lambda: calls.append("canary"))
    monkeypatch.setattr(watcher, "_check_user_edits", lambda: calls.append("user_edits"))
    monkeypatch.setattr(watcher, "_reconcile_skill_sides", lambda: calls.append("reconcile"))
    # list_watch_dirs 返回空，跳过 _scan_dir
    monkeypatch.setattr(
        "xskill.pipeline.runner.list_watch_dirs",
        lambda **_ignored_keyword_arguments: [],
    )
    watcher._scan_once()
    assert "harvest" in calls and "edits" in calls and "canary" in calls
    assert "user_edits" not in calls   # server 模式跳过
    assert "reconcile" not in calls    # server 模式跳过


def test_server_mode_install_is_noop(tmp_path):
    watcher = DirectoryWatcher(server_mode=True, home_root=tmp_path)
    result = watcher._install_skill_to_all_detected(tmp_path / "any-skill")
    assert result == {}


def test_standalone_mode_runs_all(monkeypatch, tmp_path):
    watcher = DirectoryWatcher(server_mode=False, home_root=tmp_path)
    calls = []
    for method_name in (
        "_harvest",
        "_check_pending_skill_edits",
        "_check_canary_decisions",
        "_check_user_edits",
        "_reconcile_skill_sides",
    ):
        monkeypatch.setattr(
            watcher,
            method_name,
            lambda method_name=method_name, **_ignored_keyword_arguments: calls.append(method_name),
        )
    monkeypatch.setattr(
        "xskill.pipeline.runner.list_watch_dirs",
        lambda **_ignored_keyword_arguments: [],
    )
    watcher._scan_once()
    assert "_check_user_edits" in calls and "_reconcile_skill_sides" in calls
