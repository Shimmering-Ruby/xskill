"""本机未 connect 时扫描 harness、转轨迹、建索引。"""
from __future__ import annotations

from xskill.ecosystems import local_bootstrap as lb
from xskill.traj_search import upsert_session_file


def test_ensure_skips_on_server_role(tmp_path, monkeypatch):
    monkeypatch.setattr("xskill.runtime.role", lambda: "server")
    called = []
    monkeypatch.setattr(
        lb, "ingest_detected_sessions_once",
        lambda **kwargs: called.append(True),
    )

    report = lb.ensure_local_sessions(home_root=tmp_path)

    assert report == {"ran": False, "reason": "server"}
    assert called == []


def test_first_ensure_scans_and_writes_state(tmp_path, monkeypatch):
    monkeypatch.setattr("xskill.runtime.role", lambda: "standalone")
    monkeypatch.setattr(
        lb, "ingest_detected_sessions_once",
        lambda home_root=None, ecosystems=None: {
            "harnesses": ["cursor"],
            "bridged": {"cursor": 3},
            "errors": {},
        },
    )

    report = lb.ensure_local_sessions(home_root=tmp_path, skip_if_server=False)

    assert report["ran"] is True
    state = lb.load_local_init_state(tmp_path)
    assert state["harnesses"] == ["cursor"]
    assert state["bridged"] == {"cursor": 3}


def test_second_ensure_skips_when_index_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("xskill.runtime.role", lambda: "standalone")
    sessions = tmp_path / ".xskill" / "cursor_sessions"
    sessions.mkdir(parents=True)
    md = sessions / "traj_cursor_demo.md"
    md.write_text("# t\n\n## User\n\nhello\n", encoding="utf-8")
    upsert_session_file(sessions, md)
    (tmp_path / ".xskill" / "local_init.json").write_text(
        '{"version": 1, "harnesses": ["cursor"]}\n', encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(
        lb, "ingest_detected_sessions_once",
        lambda **kwargs: called.append(True),
    )

    report = lb.ensure_local_sessions(home_root=tmp_path, skip_if_server=False)

    assert report["ran"] is False
    assert report["reason"] == "already"
    assert called == []


def test_empty_index_rescans(tmp_path, monkeypatch):
    monkeypatch.setattr("xskill.runtime.role", lambda: "standalone")
    (tmp_path / ".xskill").mkdir()
    (tmp_path / ".xskill" / "local_init.json").write_text(
        '{"version": 1, "harnesses": []}\n', encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(
        lb, "ingest_detected_sessions_once",
        lambda **kwargs: called.append(True) or {
            "harnesses": ["claude_code"], "bridged": {}, "errors": {},
        },
    )

    report = lb.ensure_local_sessions(home_root=tmp_path, skip_if_server=False)

    assert report["ran"] is True
    assert called == [True]


def test_force_rescans(tmp_path, monkeypatch):
    monkeypatch.setattr("xskill.runtime.role", lambda: "standalone")
    sessions = tmp_path / ".xskill" / "cc_sessions"
    sessions.mkdir(parents=True)
    md = sessions / "traj_cc_demo.md"
    md.write_text("# t\n\n## User\n\nhi\n", encoding="utf-8")
    upsert_session_file(sessions, md)
    (tmp_path / ".xskill" / "local_init.json").write_text(
        '{"version": 1, "harnesses": ["claude_code"]}\n', encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(
        lb, "ingest_detected_sessions_once",
        lambda **kwargs: called.append(True) or {
            "harnesses": ["claude_code"], "bridged": {}, "errors": {},
        },
    )

    report = lb.ensure_local_sessions(
        home_root=tmp_path, force=True, skip_if_server=False,
    )

    assert report["ran"] is True
    assert called == [True]


def test_local_traj_search_bootstraps_once(monkeypatch):
    import xskill.cli as cli

    boot = []
    monkeypatch.setattr(
        cli, "_maybe_bootstrap_local_traj",
        lambda: boot.append(True),
    )
    monkeypatch.setattr(
        "xskill.traj_browse.find_query_hits",
        lambda query, **kwargs: [],
    )
    monkeypatch.setattr(cli, "_write_search_output", lambda *a, **k: None)

    code = cli._cmd_search_kind_local("内存", kind="traj", top_k=5,
                                      json_mode=False, names=[])

    assert code == 0
    assert boot == [True]
