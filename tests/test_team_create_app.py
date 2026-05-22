from __future__ import annotations

import inspect

from xskill.api import create_app


def test_create_app_accepts_team_server_kwarg():
    sig = inspect.signature(create_app)
    assert "team_server" in sig.parameters
    assert sig.parameters["team_server"].default is False
