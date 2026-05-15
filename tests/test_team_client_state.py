import pytest

from xskill.team.client.state import ClientState, save_client_state, load_client_state


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "team_client.json"
    st = ClientState(server_url="http://1.2.3.4:8000", client_id="cid-1",
                     join_token="tok")
    save_client_state(st, p)
    back = load_client_state(p)
    assert back == st


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_client_state(tmp_path / "absent.json")
