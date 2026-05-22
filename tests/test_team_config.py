from __future__ import annotations

from xskill import config as C


def test_team_paths_under_xskill_home():
    assert C.get_team_server_state_path() == C.XSKILL_HOME / "team_server.json"
    assert C.get_team_clients_db_path() == C.XSKILL_HOME / "team_clients.db"
    assert C.get_team_client_state_path() == C.XSKILL_HOME / "team_client.json"
