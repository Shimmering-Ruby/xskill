"""Pre-release host migration script config tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "xskill_v0_6_28a1_migrate.py"
SPEC = importlib.util.spec_from_file_location("xskill_v0_6_28a1_migrate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_migration_preserves_secrets_and_creates_four_pools():
    original = {
        "llm": {
            "api_key": "llm-secret",
            "rate_limit": {"rpm": 120, "tpm": 6000, "burst": 12},
        },
        "embedding": {"api_key": "embed-secret"},
        "dashboard": {"password": "admin-secret"},
        "watcher": {
            "poll_interval": 7,
            "max_concurrent": 32,
            "cluster_batch_size": 5,
        },
    }
    migrated = MODULE.migrate_config(original)

    assert migrated["llm"]["api_key"] == "llm-secret"
    assert migrated["embedding"]["api_key"] == "embed-secret"
    assert migrated["dashboard"]["password"] == "admin-secret"
    assert migrated["watcher"] == {"poll_interval": 7}
    assert set(migrated["agent_worker"]["pools"]) == {
        "split", "cluster", "edit", "embed",
    }
    assert migrated["agent_worker"]["pools"]["cluster"]["batch_size"] == 5
    assert migrated["llm"]["rate_limit"] == {
        "rpm": 120,
        "tpm": 6000,
        "request_burst": 12,
        "max_inflight": 8,
        "token_burst": 12,
    }
    assert migrated["embedding"]["rate_limit"] == {"max_inflight": 4}
    assert "agent_worker" not in original
    assert "burst" in original["llm"]["rate_limit"]


def test_migration_uses_release_defaults_for_missing_legacy_values():
    migrated = MODULE.migrate_config({"llm": {}, "embedding": {}})
    pools = migrated["agent_worker"]["pools"]
    assert pools["split"] == {"workers": 24, "llm_weight": 6}
    assert pools["cluster"] == {
        "workers": 8, "batch_size": 8, "llm_weight": 3,
    }
    assert pools["edit"] == {"workers": 4, "llm_weight": 1}
    assert pools["embed"] == {"workers": 4}


def test_install_backs_up_migrates_restarts_and_never_prints_secrets(
    tmp_path, monkeypatch, capsys,
):
    state_dir = tmp_path / ".xskill"
    state_dir.mkdir()
    config_path = state_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "llm": {
            "api_key": "never-print-llm-key",
            "rate_limit": {"rpm": 60, "burst": 4},
        },
        "embedding": {"api_key": "never-print-embed-key"},
        "dashboard": {"password": "never-print-password"},
        "watcher": {"max_concurrent": 4, "cluster_batch_size": 3},
    }), encoding="utf-8")
    rollback_wheel = tmp_path / "xskill-0.6.27-py3-none-any.whl"
    rollback_wheel.write_bytes(b"wheel")
    manifest = state_dir / "migration.json"
    calls = []

    def prepare_rollback_wheel(_directory):
        return rollback_wheel

    def run_pip(*arguments):
        calls.append(arguments)

    def package_version():
        return "0.6.28a1"

    def serve_processes():
        return [{
            "pid": 123,
            "argv": ["python", "-m", "xskill", "serve", "--port", "8123"],
            "cwd": tmp_path,
            "environ": {},
        }]

    def restart_serve(captured, log_path):
        calls.append(("restart", captured, log_path))

    def verify(_port):
        return {
            "pid": 456,
            "pools": {
                name: {"workers": values["workers"]}
                for name, values in MODULE.POOL_DEFAULTS.items()
            },
        }

    monkeypatch.setattr(
        MODULE, "_prepare_rollback_wheel", prepare_rollback_wheel,
    )
    monkeypatch.setattr(MODULE, "_run_pip", run_pip)
    monkeypatch.setattr(MODULE, "_package_version", package_version)
    monkeypatch.setattr(MODULE, "_serve_processes", serve_processes)
    monkeypatch.setattr(MODULE, "_restart_serve", restart_serve)
    monkeypatch.setattr(MODULE, "_verify", verify)
    args = MODULE.parse_args([
        "--wheel", str(tmp_path / "xskill-0.6.28a1.whl"),
        "--config", str(config_path),
        "--manifest", str(manifest),
    ])
    MODULE.install(args)

    migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert set(migrated["agent_worker"]["pools"]) == {
        "split", "cluster", "edit", "embed",
    }
    assert migrated["agent_worker"]["pools"]["cluster"]["batch_size"] == 3
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert Path(payload["config_backup"]).is_file()
    assert payload["port"] == 8123
    assert calls[0][:3] == ("install", "--no-deps", "--force-reinstall")
    output = capsys.readouterr().out
    for secret in (
        "never-print-llm-key", "never-print-embed-key", "never-print-password",
    ):
        assert secret not in output
        assert secret not in manifest.read_text(encoding="utf-8")


def test_rollback_restores_v0627_package_and_original_config(
    tmp_path, monkeypatch,
):
    state_dir = tmp_path / ".xskill"
    state_dir.mkdir()
    config_path = state_dir / "config.yaml"
    backup_path = state_dir / "config.yaml.v0.6.27.bak"
    rollback_wheel = state_dir / "xskill-0.6.27-py3-none-any.whl"
    manifest = state_dir / "migration.json"
    original = "watcher:\n  max_concurrent: 4\n"
    config_path.write_text("agent_worker:\n  pools: {}\n", encoding="utf-8")
    backup_path.write_text(original, encoding="utf-8")
    rollback_wheel.write_bytes(b"wheel")
    manifest.write_text(json.dumps({
        "config_path": str(config_path),
        "config_backup": str(backup_path),
        "rollback_wheel": str(rollback_wheel),
    }), encoding="utf-8")
    calls = []

    def serve_processes():
        return [{"pid": 1}]

    def run_pip(*arguments):
        calls.append(arguments)

    def package_version():
        return "0.6.27"

    def restart_serve(captured, log_path):
        calls.append(("restart", captured, log_path))

    monkeypatch.setattr(MODULE, "_serve_processes", serve_processes)
    monkeypatch.setattr(MODULE, "_run_pip", run_pip)
    monkeypatch.setattr(MODULE, "_package_version", package_version)
    monkeypatch.setattr(MODULE, "_restart_serve", restart_serve)

    args = MODULE.parse_args(["--rollback", "--manifest", str(manifest)])
    MODULE.rollback(args)

    assert config_path.read_text(encoding="utf-8") == original
    assert calls[0] == (
        "install", "--no-deps", "--force-reinstall", str(rollback_wheel),
    )
    assert calls[1][0] == "restart"
