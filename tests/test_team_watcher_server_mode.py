from xskill.pipeline.runner import DirectoryWatcher


def test_server_mode_flag_defaults_false():
    w = DirectoryWatcher()
    assert w.server_mode is False


def test_server_mode_skips_user_edit_and_reconcile(monkeypatch):
    w = DirectoryWatcher(server_mode=True)
    calls = []
    monkeypatch.setattr(w, "_harvest", lambda: calls.append("harvest"))
    monkeypatch.setattr(w, "_check_pending_skill_edits", lambda: calls.append("edits"))
    monkeypatch.setattr(w, "_check_canary_decisions", lambda: calls.append("canary"))
    monkeypatch.setattr(w, "_check_user_edits", lambda: calls.append("user_edits"))
    monkeypatch.setattr(w, "_reconcile_skill_sides", lambda: calls.append("reconcile"))
    # list_watch_dirs 返回空，跳过 _scan_dir
    monkeypatch.setattr("xskill.pipeline.runner.list_watch_dirs", lambda **kw: [])
    w._scan_once()
    assert "harvest" in calls and "edits" in calls and "canary" in calls
    assert "user_edits" not in calls   # server 模式跳过
    assert "reconcile" not in calls    # server 模式跳过


def test_server_mode_install_is_noop(tmp_path):
    w = DirectoryWatcher(server_mode=True)
    result = w._install_skill_to_all_detected(tmp_path / "any-skill")
    assert result == {}


def test_standalone_mode_runs_all(monkeypatch):
    w = DirectoryWatcher(server_mode=False)
    calls = []
    for m in ("_harvest", "_check_pending_skill_edits", "_check_canary_decisions",
              "_check_user_edits", "_reconcile_skill_sides"):
        monkeypatch.setattr(w, m, lambda m=m: calls.append(m))
    monkeypatch.setattr("xskill.pipeline.runner.list_watch_dirs", lambda **kw: [])
    w._scan_once()
    assert "_check_user_edits" in calls and "_reconcile_skill_sides" in calls
