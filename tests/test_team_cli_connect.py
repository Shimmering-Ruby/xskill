from xskill.cli import build_parser, cmd_connect


def test_connect_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args(["connect", "1.2.3.4:8000", "--token", "tok",
                              "--label", "alice"])
    assert args.address == "1.2.3.4:8000"
    assert args.token == "tok"
    assert args.label == "alice"
    # 无参形式（复用已存连接）
    args2 = parser.parse_args(["connect"])
    assert args2.address is None


def test_connect_no_address_no_saved_state_errors(tmp_path, monkeypatch, capsys):
    # 无 address 且无 team_client.json → 返回非 0
    monkeypatch.setattr("xskill.config.get_team_client_state_path",
                        lambda: tmp_path / "absent.json")
    parser = build_parser()
    args = parser.parse_args(["connect"])
    rc = cmd_connect(args)
    assert rc != 0


def test_connect_with_address_requires_token():
    parser = build_parser()
    args = parser.parse_args(["connect", "1.2.3.4:8000"])  # 没 --token
    rc = cmd_connect(args)
    assert rc != 0
