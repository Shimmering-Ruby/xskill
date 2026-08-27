"""`xskill read traj` 与 `xskill read atom`：按行号读原文。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill import cli
from xskill.pipeline.atom import AtomTask, AtomTaskStore
from xskill.team.server import api as server_api
from xskill.team.server.client_registry import ClientRegistry
from xskill.traj_read import (
    TRAJ_READ_MAX_LINES,
    parse_read_offsets,
    read_atom,
    read_trajectory,
)

TOKEN = "secret-token"


def _args(**overrides) -> SimpleNamespace:
    base = {
        "terms": ["traj", "traj_cc_alice_memleak"],
        "offset_start": None,
        "offset_end": None,
        "json": False,
        "team": False,
        "local": False,
        "name": "",
        "eco": "ngagent",
        "recursive": False,
        "no_register": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_lines(path: Path, count: int, *, prefix: str = "L") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{prefix}{index}\n" for index in range(1, count + 1)),
        encoding="utf-8",
    )
    return path


def _seed_atom(root: Path, *, traj_id: str, atom_id: str,
               offset_start: int, offset_end: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    store = AtomTaskStore(root=root)
    atom = AtomTask(
        atom_id=atom_id,
        traj_id=traj_id,
        offset_start=offset_start,
        offset_end=offset_end,
        intent="diagnose leak",
        summary="found a cache",
        tags=[],
        used_skills=[],
        ux_score=None,
        pre_atom_id=None,
        post_atom_id=None,
        context_prefix="",
        raw_segment="must not be the only source",
    )
    store.save(atom)
    return root


class _Response:
    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _ReadHttp:
    def __init__(self, status_code=200, payload=None, error=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {
            "result": {
                "kind": "traj",
                "traj_id": "traj_cc_alice_memleak",
                "user": "alice",
                "offset_start": 2,
                "offset_end": 4,
                "total_start": 1,
                "total_end": 6,
                "total_lines": 5,
                "file_lines": 5,
                "truncated": False,
                "text": "L2\nL3\n",
            },
            "meta": {"unknown_names": []},
        }
        self.error = error
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path: str, **kwargs):
        self.calls.append((path, kwargs.get("params")))
        if self.error is not None:
            raise self.error
        return _Response(self.status_code, self.payload)


def test_parse_read_offsets_rejects_zero_and_inverted():
    start, end, error = parse_read_offsets(0, 10)
    assert error
    start, end, error = parse_read_offsets(5, 5)
    assert error
    start, end, error = parse_read_offsets(2, 9)
    assert (start, end, error) == (2, 9, None)


def test_read_trajectory_reports_current_and_total(tmp_path, monkeypatch):
    sessions = tmp_path / "alice" / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 5)
    monkeypatch.setattr(
        "xskill.traj_read.watch_session_dirs",
        lambda: [("alice", sessions)],
    )
    payload = read_trajectory("traj_cc_alice_memleak", offset_start=2, offset_end=4)
    assert payload is not None
    assert payload["kind"] == "traj"
    assert payload["offset_start"] == 2
    assert payload["offset_end"] == 4
    assert payload["total_start"] == 1
    assert payload["total_end"] == 6
    assert payload["total_lines"] == 5
    assert payload["text"] == "L2\nL3\n"
    assert "alice" == payload["user"]
    assert str(tmp_path) not in json.dumps(payload)


def test_read_trajectory_caps_at_max_lines(tmp_path, monkeypatch):
    sessions = tmp_path / "alice" / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", TRAJ_READ_MAX_LINES + 20)
    monkeypatch.setattr(
        "xskill.traj_read.watch_session_dirs",
        lambda: [("alice", sessions)],
    )
    payload = read_trajectory("traj_cc_alice_memleak")
    assert payload is not None
    assert payload["offset_start"] == 1
    assert payload["offset_end"] == 1 + TRAJ_READ_MAX_LINES
    assert payload["total_end"] == TRAJ_READ_MAX_LINES + 21
    assert payload["truncated"] is True
    assert payload["text"].count("\n") == TRAJ_READ_MAX_LINES


def test_read_atom_uses_atom_range_as_total(tmp_path, monkeypatch):
    sessions = tmp_path / "alice" / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 12)
    _seed_atom(
        sessions,
        traj_id="traj_cc_alice_memleak",
        atom_id="atom_t_0001",
        offset_start=4,
        offset_end=8,
    )
    monkeypatch.setattr(
        "xskill.traj_read.watch_session_dirs",
        lambda: [("alice", sessions)],
    )
    payload = read_atom("atom_t_0001")
    assert payload is not None
    assert payload["kind"] == "atom"
    assert payload["atom_id"] == "atom_t_0001"
    assert payload["offset_start"] == 4
    assert payload["offset_end"] == 8
    assert payload["total_start"] == 4
    assert payload["total_end"] == 8
    assert payload["text"] == "L4\nL5\nL6\nL7\n"
    assert "must not be the only source" not in payload["text"]


def test_read_atom_offset_stays_inside_atom(tmp_path, monkeypatch):
    sessions = tmp_path / "alice" / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 12)
    _seed_atom(
        sessions,
        traj_id="traj_cc_alice_memleak",
        atom_id="atom_t_0001",
        offset_start=4,
        offset_end=10,
    )
    monkeypatch.setattr(
        "xskill.traj_read.watch_session_dirs",
        lambda: [("alice", sessions)],
    )
    payload = read_atom("atom_t_0001", offset_start=6, offset_end=20)
    assert payload is not None
    assert payload["offset_start"] == 6
    assert payload["offset_end"] == 10
    assert payload["total_start"] == 4
    assert payload["total_end"] == 10
    assert payload["text"] == "L6\nL7\nL8\nL9\n"


def test_cli_read_traj_local_prints_ranges(monkeypatch, capsys, tmp_path):
    sessions = tmp_path / "alice" / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 5)
    monkeypatch.setattr("xskill.runtime.role", lambda: "standalone")
    monkeypatch.setattr(
        "xskill.traj_read.watch_session_dirs",
        lambda: [("alice", sessions)],
    )
    rc = cli.cmd_read(_args(local=True, offset_start=2, offset_end=4))
    assert rc == 0
    out = capsys.readouterr().out
    assert "当前行号：L2-L4" in out
    assert "总行号：L1-L6" in out
    assert "L2" in out
    assert "L3" in out
    assert "L1\n" not in out


def test_cli_read_atom_missing_id_errors(capsys):
    rc = cli.cmd_read(_args(terms=["atom"]))
    assert rc == 2
    assert "xskill read atom <atom_id>" in capsys.readouterr().err


def test_cli_read_traj_team_uses_read_path(capsys):
    http = _ReadHttp()
    rc = cli.cmd_read_traj(
        _args(team=True, offset_start=2, offset_end=4),
        http=http,
        headers={"X-Xskill-Token": TOKEN},
    )
    assert rc == 0
    assert http.calls[0][0] == "/api/v1/team/trajectories/read"
    assert http.calls[0][1]["traj_id"] == "traj_cc_alice_memleak"
    out = capsys.readouterr().out
    assert "当前行号：L2-L4" in out
    assert "总行号：L1-L6" in out


def test_cli_read_atom_team_uses_atoms_read(capsys):
    http = _ReadHttp(payload={
        "result": {
            "kind": "atom",
            "traj_id": "traj_cc_alice_memleak",
            "atom_id": "atom_t_0001",
            "user": "alice",
            "offset_start": 4,
            "offset_end": 8,
            "total_start": 4,
            "total_end": 8,
            "total_lines": 4,
            "file_lines": 12,
            "truncated": False,
            "text": "L4\nL5\n",
        },
        "meta": {"unknown_names": []},
    })
    rc = cli.cmd_read_atom(
        _args(team=True, terms=["atom", "atom_t_0001"]),
        http=http,
        headers={"X-Xskill-Token": TOKEN},
    )
    assert rc == 0
    assert http.calls[0][0] == "/api/v1/team/atoms/read"
    assert "当前行号：L4-L8" in capsys.readouterr().out


def test_cli_read_traj_team_old_server(capsys):
    http = _ReadHttp(status_code=404, payload={"detail": "Not Found"})
    rc = cli.cmd_read_traj(_args(team=True), http=http, headers={})
    assert rc == 1
    assert "升级 server" in capsys.readouterr().err


def _make_team_app(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    traj_root = tmp_path / "team_traj"
    registry = ClientRegistry(tmp_path / "clients.db")
    server_api.init_team_context(
        join_token=TOKEN,
        client_registry=registry,
        skill_dir=skill_dir,
        traj_root=traj_root,
        register_dir=lambda path, label: None,
    )
    app = FastAPI()
    app.include_router(server_api.router)
    return TestClient(app), traj_root, registry


def _register(client: TestClient, user_name: str) -> dict:
    response = client.post(
        "/api/v1/team/register",
        json={"token": TOKEN, "user_name": user_name, "hostname": "h"},
    )
    assert response.status_code == 200
    return {
        "X-Xskill-Token": TOKEN,
        "X-Xskill-Client": response.json()["client_id"],
    }


def test_team_traj_read_returns_window(tmp_path):
    client, traj_root, registry = _make_team_app(tmp_path)
    headers = _register(client, "alice")
    client_id = registry.find_by_user_name("alice")
    sessions = traj_root / "clients" / registry.dir_name_for(client_id) / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 5)
    response = client.get(
        "/api/v1/team/trajectories/read",
        params={
            "traj_id": "traj_cc_alice_memleak",
            "offset_start": 2,
            "offset_end": 4,
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["offset_start"] == 2
    assert payload["offset_end"] == 4
    assert payload["total_start"] == 1
    assert payload["total_end"] == 6
    assert payload["text"] == "L2\nL3\n"
    assert str(traj_root) not in json.dumps(response.json())


def test_team_atom_read_returns_atom_total_range(tmp_path):
    client, traj_root, registry = _make_team_app(tmp_path)
    headers = _register(client, "alice")
    client_id = registry.find_by_user_name("alice")
    sessions = traj_root / "clients" / registry.dir_name_for(client_id) / "sessions"
    _write_lines(sessions / "traj_cc_alice_memleak.md", 12)
    _seed_atom(
        sessions,
        traj_id="traj_cc_alice_memleak",
        atom_id="atom_t_0001",
        offset_start=4,
        offset_end=8,
    )
    response = client.get(
        "/api/v1/team/atoms/read",
        params={"atom_id": "atom_t_0001"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["kind"] == "atom"
    assert payload["offset_start"] == 4
    assert payload["offset_end"] == 8
    assert payload["total_start"] == 4
    assert payload["total_end"] == 8
    assert payload["text"] == "L4\nL5\nL6\nL7\n"
