"""Contracts for the deterministic Logical Task and Attempt replay."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.bench.task_graph_replay.evaluate import (
    TaskReplayValidationError,
    evaluate_suite,
    load_suite,
    main,
    render_text,
    validate_suite,
)

FIXTURE_DIR = (
    Path(__file__).parent.parent
    / "scripts"
    / "bench"
    / "task_graph_replay"
    / "fixtures"
)
BASELINE_PATH = FIXTURE_DIR / "baseline_v1.json"
REPORT_PATH = FIXTURE_DIR / "baseline_v1.report.json"


pytestmark = pytest.mark.algorithm_replay


def test_baseline_report_matches_checked_in_snapshot():
    report = evaluate_suite(load_suite(BASELINE_PATH))

    assert report == json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_baseline_exposes_expected_regression_signals():
    report = evaluate_suite(load_suite(BASELINE_PATH))
    metrics = report["metrics"]

    assert metrics["task_grouping"]["pairwise"]["f1"] < 1.0
    assert metrics["task_grouping"]["b3"]["f1"] < 1.0
    assert metrics["relations"]["macro"]["f1"] < 1.0
    assert set(metrics["relations"]["by_type"]) >= {
        "task:parent",
        "task:subtask",
        "attempt:continuation_of",
        "attempt:correction_of",
        "attempt:retry_of",
    }
    assert metrics["attempt_detection"]["precision"] < 1.0
    assert metrics["attempt_outcome"]["accuracy"] < 1.0
    assert metrics["confidence"]["membership"]["brier"] > 0.0
    assert metrics["confidence"]["attempt_outcome"]["ece"] > 0.0
    assert metrics["evidence_coverage"] < 1.0
    assert metrics["usage"]["execution"]["conservation_rate"] == 1.0
    assert metrics["usage"]["xskill_processing"]["conservation_rate"] == 1.0
    assert metrics["usage"]["execution"]["shared_fraction"] > 0.0
    assert metrics["usage"]["execution"]["unattributed_fraction"] > 0.0
    assert metrics["usage"]["execution"]["unavailable_events"] == 1
    assert metrics["usage"]["xskill_processing"]["estimated_events"] == 1
    assert report["error_count"] > 0


def test_valid_false_merge_reduces_pairwise_precision():
    suite = load_suite(BASELINE_PATH)
    case = suite["cases"][1]
    case["prediction"]["memberships"][1]["task_id"] = "pred-task-a"
    case["prediction"]["attempts"][1]["task_id"] = "pred-task-a"
    case["prediction"]["usage_allocations"][1]["task_id"] = "pred-task-a"

    metrics = evaluate_suite(suite)["cases"][1]["metrics"]

    assert metrics["task_grouping"]["pairwise"]["precision"] < 1.0


def test_numeric_usage_imbalance_is_reported_without_hiding_the_event():
    suite = load_suite(BASELINE_PATH)
    allocation = suite["cases"][2]["prediction"]["usage_allocations"][0]
    allocation["total_tokens"] = 59

    case = evaluate_suite(suite)["cases"][2]

    assert case["metrics"]["usage"]["execution"]["conservation_rate"] == 0.0
    assert any(error["type"] == "usage_not_conserved" for error in case["errors"])


def test_multiple_confirmed_primary_memberships_use_production_invariant():
    suite = load_suite(BASELINE_PATH)
    broken = deepcopy(suite)
    broken["cases"][1]["prediction"]["memberships"][3]["decision"] = "confirmed"

    with pytest.raises(
        TaskReplayValidationError,
        match="at most one confirmed primary membership",
    ):
        validate_suite(broken)


def test_unknown_usage_event_fails_loudly():
    suite = load_suite(BASELINE_PATH)
    suite["cases"][0]["prediction"]["usage_allocations"][0]["usage_event_id"] = (
        "missing-event"
    )

    with pytest.raises(TaskReplayValidationError, match="unknown usage event"):
        validate_suite(suite)


def test_out_of_range_confidence_fails_loudly():
    suite = load_suite(BASELINE_PATH)
    suite["cases"][0]["prediction"]["memberships"][0]["confidence"] = 1.1

    with pytest.raises(TaskReplayValidationError, match=r"within \[0, 1\]"):
        validate_suite(suite)


def test_missing_gold_atom_membership_fails_loudly():
    suite = load_suite(BASELINE_PATH)
    suite["cases"][0]["gold"]["memberships"].pop()

    with pytest.raises(TaskReplayValidationError, match="every annotated Atom"):
        validate_suite(suite)


def test_unsupported_schema_version_fails_loudly():
    suite = load_suite(BASELINE_PATH)
    suite["schema_version"] = 2

    with pytest.raises(TaskReplayValidationError, match="supported=1, got=2"):
        validate_suite(suite)


def test_cli_renders_text_and_json(capsys):
    assert main([str(BASELINE_PATH)]) == 0
    assert capsys.readouterr().out == (
        render_text(evaluate_suite(load_suite(BASELINE_PATH))) + "\n"
    )

    assert main([str(BASELINE_PATH), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == evaluate_suite(
        load_suite(BASELINE_PATH)
    )
