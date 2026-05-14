import stat

from xskill.team.server_state import ensure_join_token, load_join_token


def test_ensure_generates_and_persists(tmp_path):
    p = tmp_path / "team_server.json"
    tok = ensure_join_token(p)
    assert isinstance(tok, str) and len(tok) >= 16
    assert p.is_file()
    # 第二次调用返回同一个 token（不重新生成）
    assert ensure_join_token(p) == tok


def test_token_file_is_0600(tmp_path):
    p = tmp_path / "team_server.json"
    ensure_join_token(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_load_returns_none_when_missing(tmp_path):
    assert load_join_token(tmp_path / "absent.json") is None
