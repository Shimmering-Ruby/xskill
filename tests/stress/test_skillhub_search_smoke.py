"""Release gate for skill_hub hybrid search and dashboard/sync workloads.

Defaults match ``embed.md`` A5.  ``XSKILL_SEARCH_SMOKE_*`` overrides are only
for smaller local diagnostics; the release workflow intentionally sets none.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "loadtest_skillhub_search.py"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def _artifact_message(root: Path, process: subprocess.CompletedProcess[str] | None) -> str:
    results = sorted(root.glob("run-*/result.json"))
    details = [f"artifact_root={root}", f"result_files={results}"]
    if process is not None:
        details.extend([
            f"returncode={process.returncode}",
            f"stdout_tail={process.stdout[-4000:]}",
            f"stderr_tail={process.stderr[-4000:]}",
        ])
    return "\n".join(details)


@pytest.mark.stress
def test_skillhub_search_smoke(tmp_path: Path) -> None:
    skills = _env_int("XSKILL_SEARCH_SMOKE_SKILLS", 500)
    clients = _env_int("XSKILL_SEARCH_SMOKE_CLIENTS", 30)
    concurrency = _env_int("XSKILL_SEARCH_SMOKE_CONCURRENCY", 50)
    waves = _env_int("XSKILL_SEARCH_SMOKE_WAVES", 4)
    distractors = _env_int("XSKILL_SEARCH_SMOKE_DISTRACTORS", 5000)
    junk_depth = _env_int("XSKILL_SEARCH_SMOKE_JUNK_DEPTH", 8)
    embed_delay = _env_float("XSKILL_SEARCH_SMOKE_EMBED_DELAY", 0.2)
    panel_duration = _env_float("XSKILL_SEARCH_SMOKE_PANEL_DURATION", 20.0)
    timeout_s = _env_float("XSKILL_SEARCH_SMOKE_TIMEOUT", 1200.0)

    command = [
        sys.executable,
        str(HARNESS),
        "--skills", str(skills),
        "--clients", str(clients),
        "--concurrency", str(concurrency),
        "--waves", str(waves),
        "--distractors", str(distractors),
        "--junk-depth", str(junk_depth),
        "--embed-delay-s", str(embed_delay),
        "--panel-duration-s", str(panel_duration),
        "--artifact-root", str(tmp_path),
    ]
    runner = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = runner.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _signal_process_group(runner.pid, signal.SIGTERM)
        try:
            stdout, stderr = runner.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            _signal_process_group(runner.pid, signal.SIGKILL)
            stdout, stderr = runner.communicate(timeout=10)
        pytest.fail(
            f"skillhub search harness timed out after {timeout_s}s\n"
            f"stdout_tail={(stdout or exc.stdout or '')[-4000:]}\n"
            f"stderr_tail={(stderr or exc.stderr or '')[-4000:]}\n"
            f"{_artifact_message(tmp_path, None)}",
        )
    finally:
        _signal_process_group(runner.pid, signal.SIGTERM)
    process = subprocess.CompletedProcess(command, runner.returncode, stdout, stderr)

    result_files = sorted(tmp_path.glob("run-*/result.json"))
    assert len(result_files) == 1, _artifact_message(tmp_path, process)
    result_path = result_files[0]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    artifact = f"stress artifact: {result_path}"
    assert process.returncode == 0, _artifact_message(tmp_path, process)
    assert result["success"] is True, (
        f"validation_failures={result.get('validation_failures')}\n"
        f"result={result_path}\n{_artifact_message(tmp_path, process)}"
    )

    config = result["config"]
    assert config["max_embed_low"] == 1, artifact
    assert config["max_embed_high"] == 4, artifact
    assert config["skills"] == skills, artifact
    assert config["clients"] == clients, artifact

    scenarios = result["scenarios"]
    required_panel_paths = {
        "/api/v1/dashboard/tags", "/api/v1/dashboard/my/manifest",
    }
    for cap in (1, 4):
        scenario = scenarios[f"scenario_a_max_embed_{cap}"]
        assert scenario["search"]["failure_count"] == 0, artifact
        assert scenario["duplicate_cold_embed_calls"] == 1, artifact
        assert scenario["window_embed_calls"] <= scenario["distinct_query_terms"], artifact
        assert scenario["observed_peak_embed"] == cap, artifact
        assert scenario["sync"]["failure_count"] == 0, artifact
        assert scenario["sync"]["count"] >= clients, artifact
        assert required_panel_paths.issubset(scenario["panel"]["paths"]), artifact
        assert scenario["panel"]["failure_count"] == 0, artifact

    embed_down = scenarios["scenario_b_embed_down"]
    assert embed_down["search"]["failure_count"] == 0, artifact
    assert embed_down["peak_threads"] <= embed_down["baseline_threads"], artifact
    assert embed_down["shutdown"]["traceback_count"] == 0, artifact
    assert embed_down["shutdown"]["degraded_log_count"] >= 1, artifact

    cold = scenarios["scenario_c_cold_start"]
    assert cold["first_search"]["status"] == 200, artifact
    assert cold["first_search"]["elapsed_s"] < 3.0, artifact
    assert cold["second_search"]["status"] == 200, artifact
    assert cold["second_search"]["elapsed_s"] < 0.2, artifact

    panel = scenarios["panel_gate"]
    assert panel["sync"]["failure_count"] == 0, artifact
    assert panel["sync"]["count"] >= clients, artifact
    assert required_panel_paths.issubset(panel["panel"]["paths"]), artifact
    assert panel["panel"]["failure_count"] == 0, artifact
    assert panel["panel_core_failures"] == [], artifact
    assert panel["health"]["non_200_count"] == 0, artifact
