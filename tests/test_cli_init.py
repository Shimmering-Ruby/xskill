"""`xskill init` 本机引导：扫描 harness、转换轨迹、可选装 helper。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import xskill.cli as cli  # noqa: E402


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        yes=False, skills_only=False, no_skill=False, force=False,
        harness=[], target_root=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def detections():
    return [{"ecosystem": "claude_code", "source": "/x", "bridge": "/y"},
            {"ecosystem": "cursor", "source": "/c", "bridge": "/d"}]


@pytest.fixture
def install_recorder(monkeypatch):
    installed = []
    monkeypatch.setattr(
        "xskill.ecosystems.bundled_guide.install_bundled_xskill_guide",
        lambda target_root=None, ecosystems=None: installed.extend(
            ecosystems or [],
        ) or list(ecosystems or []),
    )
    return installed


def test_yes_scans_and_installs_all_detected(
        detections, install_recorder, monkeypatch, capsys):
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda home_root=None: detections,
    )
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: Path("/nonexistent/team_client.json"),
    )
    scanned = []
    monkeypatch.setattr(
        "xskill.ecosystems.local_bootstrap.ensure_local_sessions",
        lambda home_root=None, force=False, skip_if_server=True: (
            scanned.append({"force": force, "home": home_root})
            or {"ran": True, "bridged": {"claude_code": 2, "cursor": 1},
                "errors": {}}
        ),
    )

    code = cli.cmd_init(_args(yes=True))

    assert code == 0
    assert scanned == [{"force": False, "home": None}]
    assert install_recorder == ["claude_code", "cursor"]
    out = capsys.readouterr().out
    assert "claude_code" in out
    assert "xskill connect" in out
    assert "xskill serve --server" in out
    assert "xskill traj search" in out


def test_skills_only_installs_without_scanning(
        detections, install_recorder, monkeypatch):
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda home_root=None: detections,
    )
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: Path("/nonexistent/team_client.json"),
    )
    scanned = []
    monkeypatch.setattr(
        "xskill.ecosystems.local_bootstrap.ensure_local_sessions",
        lambda **kwargs: scanned.append(kwargs) or {"ran": False},
    )

    code = cli.cmd_init(_args(yes=True, skills_only=True))

    assert code == 0
    assert scanned == []
    assert install_recorder == ["claude_code", "cursor"]


def test_no_skill_scans_without_installing(
        detections, install_recorder, monkeypatch):
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda home_root=None: detections,
    )
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: Path("/nonexistent/team_client.json"),
    )
    scanned = []
    monkeypatch.setattr(
        "xskill.ecosystems.local_bootstrap.ensure_local_sessions",
        lambda **kwargs: scanned.append(True) or {"ran": True, "bridged": {}},
    )

    code = cli.cmd_init(_args(yes=True, no_skill=True))

    assert code == 0
    assert scanned == [True]
    assert install_recorder == []


def test_harness_flag_installs_subset(
        detections, install_recorder, monkeypatch):
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda home_root=None: detections,
    )
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: Path("/nonexistent/team_client.json"),
    )
    monkeypatch.setattr(
        "xskill.ecosystems.local_bootstrap.ensure_local_sessions",
        lambda **kwargs: {"ran": False, "reason": "already"},
    )

    code = cli.cmd_init(_args(yes=True, harness=["cursor"]))

    assert code == 0
    assert install_recorder == ["cursor"]


def test_force_passed_to_ensure(detections, install_recorder, monkeypatch):
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda home_root=None: detections,
    )
    monkeypatch.setattr(
        "xskill.config.get_team_client_state_path",
        lambda: Path("/nonexistent/team_client.json"),
    )
    seen = []
    monkeypatch.setattr(
        "xskill.ecosystems.local_bootstrap.ensure_local_sessions",
        lambda **kwargs: seen.append(kwargs) or {"ran": True, "bridged": {}},
    )

    code = cli.cmd_init(_args(yes=True, force=True, no_skill=True))

    assert code == 0
    assert seen[0]["force"] is True
