import inspect

from xskill.cli import build_parser
from xskill.core import XSkill


def test_serve_subcommand_has_server_flag():
    parser = build_parser()
    args = parser.parse_args(["serve", "--server"])
    assert args.server is True
    args2 = parser.parse_args(["serve"])
    assert args2.server is False


def test_xskill_serve_accepts_server_mode():
    sig = inspect.signature(XSkill.serve)
    assert "server_mode" in sig.parameters
    assert sig.parameters["server_mode"].default is False
