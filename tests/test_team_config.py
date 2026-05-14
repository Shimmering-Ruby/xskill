from pathlib import Path

from xskill import config as C


def test_team_paths_under_xskill_home():
    assert C.get_team_server_state_path() == C.XSKILL_HOME / "team_server.json"
    assert C.get_team_clients_db_path() == C.XSKILL_HOME / "team_clients.db"
    assert C.get_team_client_state_path() == C.XSKILL_HOME / "team_client.json"
    assert C.get_team_skills_dir() == C.XSKILL_HOME / "team_skills"
    assert C.get_team_outbox_dir() == C.XSKILL_HOME / "team_outbox"


def test_team_dir_helpers_create_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "XSKILL_HOME", tmp_path / ".xskill")
    skills = C.get_team_skills_dir()
    outbox = C.get_team_outbox_dir()
    assert skills.is_dir() and outbox.is_dir()
