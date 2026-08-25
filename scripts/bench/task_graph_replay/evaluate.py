"""Evaluate recorded Logical Task graphs without model or network calls."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from scripts.bench.evaluate import prf
from xskill.tasks.models import (
    MEASUREMENT_QUALITIES,
    AtomRef,
    AttemptRelation,
    DecisionRecord,
    EvidenceRange,
    LogicalTask,
    SessionRef,
    TaskAtomMembership,
    TaskAttempt,
    TaskGraphGeneration,
    TaskRelation,
    UsageAllocation,
    stable_ref_key,
)

SCHEMA_VERSION = 1
USAGE_PLANES = frozenset(("execution", "xskill_processing"))
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class TaskReplayValidationError(ValueError):
    """Raised when a replay fixture violates its versioned contract."""


def _require(mapping: dict[str, Any], key: str, expected: type, context: str) -> Any:
    if key not in mapping:
        raise TaskReplayValidationError(f"{context}: missing required field {key!r}")
    value = mapping[key]
    if expected is int and isinstance(value, bool):
        raise TaskReplayValidationError(f"{context}.{key}: expected int, got bool")
    if not isinstance(value, expected):
        raise TaskReplayValidationError(
            f"{context}.{key}: expected {expected.__name__}, got {type(value).__name__}"
        )
    return value


def _non_negative_number(value: Any, context: str, *, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskReplayValidationError(f"{context}: expected a non-negative number")
    if not math.isfinite(float(value)) or value < 0:
        raise TaskReplayValidationError(f"{context}: expected a non-negative number")
    return value


def _compile_generation_unchecked(
    case: dict[str, Any],
    side_name: str,
    context: str,
) -> TaskGraphGeneration:
    """Compile concise annotations into the production Task Graph contract."""
    side = _require(case, side_name, dict, context)
    tenant_id = str(case.get("tenant_id") or "tenant-fixture")
    task_scope_id = str(case.get("task_scope_id") or case["case_id"])
    atoms = _require(case, "atoms", list, context)
    atom_refs = {}
    atom_specs = {}
    for index, atom in enumerate(atoms):
        atom_context = f"{context}.atoms[{index}]"
        if not isinstance(atom, dict):
            raise TaskReplayValidationError(f"{atom_context}: expected an object")
        atom_id = _require(atom, "atom_id", str, atom_context)
        if not atom_id or atom_id in atom_refs:
            raise TaskReplayValidationError(
                f"{atom_context}: empty or duplicate atom_id"
            )
        traj_id = _require(atom, "traj_id", str, atom_context)
        start = _require(atom, "start", int, atom_context)
        end = _require(atom, "end", int, atom_context)
        if start < 0 or end <= start:
            raise TaskReplayValidationError(f"{atom_context}: invalid half-open range")
        atom_refs[atom_id] = AtomRef(
            tenant_id, task_scope_id, "source-fixture", traj_id, atom_id
        )
        atom_specs[atom_id] = atom

    membership_specs = _require(side, "memberships", list, f"{context}.{side_name}")
    memberships = []
    task_ids = set()
    for index, membership in enumerate(membership_specs):
        item_context = f"{context}.{side_name}.memberships[{index}]"
        if not isinstance(membership, dict):
            raise TaskReplayValidationError(f"{item_context}: expected an object")
        atom_id = _require(membership, "atom_id", str, item_context)
        task_id = _require(membership, "task_id", str, item_context)
        if atom_id not in atom_refs:
            raise TaskReplayValidationError(f"{item_context}: unknown atom_id")
        task_ids.add(task_id)
        memberships.append(
            TaskAtomMembership(
                f"membership-{side_name}-{index}",
                task_id,
                atom_refs[atom_id],
                str(membership.get("role") or "primary"),
                membership.get("confidence"),
                str(membership.get("decision") or "confirmed"),
                "recorded-fixture",
                "baseline-v1",
                (f"evidence-{atom_id}",),
                "2026-08-25T00:00:00Z",
            )
        )

    attempts = []
    attempt_specs = _require(side, "attempts", list, f"{context}.{side_name}")
    for index, attempt in enumerate(attempt_specs):
        item_context = f"{context}.{side_name}.attempts[{index}]"
        if not isinstance(attempt, dict):
            raise TaskReplayValidationError(f"{item_context}: expected an object")
        attempt_id = _require(attempt, "attempt_id", str, item_context)
        task_id = _require(attempt, "task_id", str, item_context)
        outcome = _require(attempt, "outcome", str, item_context)
        atom_ids = _require(attempt, "atom_ids", list, item_context)
        task_ids.add(task_id)
        evidence_ranges = []
        for atom_id in atom_ids:
            if atom_id not in atom_refs:
                raise TaskReplayValidationError(
                    f"{item_context}: unknown evidence Atom"
                )
            atom_ref = atom_refs[atom_id]
            atom = atom_specs[atom_id]
            session_ref = SessionRef(
                atom_ref.tenant_id,
                atom_ref.task_scope_id,
                atom_ref.source_scope_id,
                atom_ref.traj_id,
            )
            content_hash = hashlib.sha256(
                f"{case['case_id']}:{atom_id}".encode()
            ).hexdigest()
            evidence_ranges.append(
                EvidenceRange(
                    f"evidence-{atom_id}",
                    session_ref,
                    "trajectory_line",
                    atom["start"],
                    atom["end"],
                    f"sha256:{content_hash}",
                    atom_hash=f"sha256:{content_hash}",
                    atom_ref=atom_ref,
                    model=dict(attempt.get("model") or {}),
                    harness=dict(attempt.get("harness") or {}),
                    skills=tuple(attempt.get("skills") or ()),
                )
            )
        evidence_ids = tuple(item.evidence_id for item in evidence_ranges)
        decision = DecisionRecord(
            f"decision-{side_name}-{attempt_id}",
            "outcome",
            outcome,
            attempt.get("confidence"),
            "confirmed",
            "recorded-fixture",
            "baseline-v1",
            evidence_ids,
            "2026-08-25T00:00:00Z",
        )
        lifecycle = str(attempt.get("lifecycle") or "finished")
        attempts.append(
            TaskAttempt(
                attempt_id,
                task_id,
                "2026-08-25T00:00:00Z",
                None if lifecycle == "running" else "2026-08-25T00:01:00Z",
                lifecycle,
                outcome,
                str(attempt.get("verification") or "unverified"),
                str(attempt.get("user_disposition") or "unknown"),
                tuple(evidence_ranges),
                decisions=(decision,),
                execution_identity=dict(attempt.get("execution_identity") or {}),
            )
        )

    relation_specs = _require(side, "task_relations", list, f"{context}.{side_name}")
    relations = []
    for index, relation in enumerate(relation_specs):
        item_context = f"{context}.{side_name}.task_relations[{index}]"
        source = _require(relation, "from_task_id", str, item_context)
        target = _require(relation, "to_task_id", str, item_context)
        task_ids.update((source, target))
        relations.append(
            TaskRelation(
                f"task-relation-{side_name}-{index}",
                source,
                target,
                _require(relation, "relation_type", str, item_context),
                relation.get("confidence"),
                str(relation.get("decision") or "confirmed"),
                "recorded-fixture",
                "baseline-v1",
                (),
                "2026-08-25T00:00:00Z",
            )
        )
    attempt_relations = []
    for index, relation in enumerate(
        _require(side, "attempt_relations", list, f"{context}.{side_name}")
    ):
        item_context = f"{context}.{side_name}.attempt_relations[{index}]"
        attempt_relations.append(
            AttemptRelation(
                f"attempt-relation-{side_name}-{index}",
                _require(relation, "from_attempt_id", str, item_context),
                _require(relation, "to_attempt_id", str, item_context),
                _require(relation, "relation_type", str, item_context),
                relation.get("confidence"),
                str(relation.get("decision") or "confirmed"),
                "recorded-fixture",
                "baseline-v1",
                (),
                "2026-08-25T00:00:00Z",
            )
        )

    attempts_by_task: dict[str, list[TaskAttempt]] = defaultdict(list)
    for attempt in attempts:
        attempts_by_task[attempt.task_id].append(attempt)
    task_states = side.get("task_states") or {}
    tasks = []
    confirmed_counts = defaultdict(int)
    for membership in memberships:
        if (
            membership.role == "primary"
            and membership.decision == "confirmed"
            and not membership.stale
        ):
            confirmed_counts[membership.task_id] += 1
    for task_id in sorted(task_ids):
        state = task_states.get(task_id) or {}
        task_attempts = attempts_by_task.get(task_id, [])
        last_outcome = task_attempts[-1].outcome if task_attempts else "unknown"
        lifecycle = str(
            state.get("lifecycle")
            or (
                "blocked"
                if last_outcome == "blocked"
                else "open"
                if last_outcome == "unknown"
                else "closed"
            )
        )
        outcome = str(
            state.get("outcome")
            or ("unknown" if lifecycle in {"open", "blocked"} else last_outcome)
        )
        tasks.append(
            LogicalTask(
                task_id,
                f"Fixture {task_id}",
                f"Synthetic {task_id}",
                "2026-08-25T00:00:00Z",
                lifecycle=lifecycle,
                outcome=outcome,
                tombstoned=confirmed_counts[task_id] == 0,
            )
        )

    allocations = []
    for item in side.get("usage_allocations") or ():
        try:
            allocations.append(UsageAllocation.from_dict(item))
        except (TypeError, ValueError) as error:
            raise TaskReplayValidationError(
                f"{context}.{side_name}.usage_allocations: {error}"
            ) from error
    return TaskGraphGeneration(
        f"generation-{case['case_id']}-{side_name}",
        tenant_id,
        task_scope_id,
        f"sha256:source-{case['case_id']}",
        {"name": "recorded-fixture", "version": "1"},
        0,
        "2026-08-25T00:00:00Z",
        tuple(tasks),
        tuple(memberships),
        tuple(relations),
        tuple(attempts),
        tuple(attempt_relations),
        tuple(allocations),
    )


def _compile_generation(
    case: dict[str, Any],
    side_name: str,
    context: str,
) -> TaskGraphGeneration:
    try:
        return _compile_generation_unchecked(case, side_name, context)
    except TaskReplayValidationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise TaskReplayValidationError(f"{context}.{side_name}: {error}") from error


def _confirmed_atom_assignment(graph: TaskGraphGeneration) -> dict[str, str]:
    return {
        stable_ref_key(item.atom_ref): item.task_id
        for item in graph.memberships
        if item.role == "primary" and item.decision == "confirmed" and not item.stale
    }


def _validate_gold(graph: TaskGraphGeneration, context: str) -> None:
    if any(task.tombstoned for task in graph.tasks):
        raise TaskReplayValidationError(f"{context}: gold Tasks must be live")
    assignment = _confirmed_atom_assignment(graph)
    if not assignment:
        raise TaskReplayValidationError(
            f"{context}: gold needs confirmed primary Atom memberships"
        )
    if any(
        item.role != "primary" or item.decision != "confirmed" or item.stale
        for item in graph.memberships
    ):
        raise TaskReplayValidationError(
            f"{context}: gold memberships must be live confirmed primary facts"
        )
    if any(item.decision != "confirmed" or item.stale for item in graph.relations):
        raise TaskReplayValidationError(
            f"{context}: gold Task relations must be live confirmed facts"
        )
    if any(item.decision != "confirmed" for item in graph.attempt_relations):
        raise TaskReplayValidationError(
            f"{context}: gold Attempt relations must be confirmed facts"
        )


def _validate_usage(
    case: dict[str, Any], prediction: TaskGraphGeneration, context: str
):
    events = _require(case, "usage_events", list, context)
    event_by_key: dict[tuple[str, str], dict] = {}
    for index, event in enumerate(events):
        item_context = f"{context}.usage_events[{index}]"
        if not isinstance(event, dict):
            raise TaskReplayValidationError(f"{item_context}: expected an object")
        event_id = _require(event, "usage_event_id", str, item_context)
        plane = _require(event, "usage_plane", str, item_context)
        quality = _require(event, "measurement_quality", str, item_context)
        key = plane, event_id
        if not event_id or plane not in USAGE_PLANES or key in event_by_key:
            raise TaskReplayValidationError(
                f"{item_context}: invalid or duplicate identity"
            )
        if quality not in MEASUREMENT_QUALITIES:
            raise TaskReplayValidationError(
                f"{item_context}: invalid measurement_quality"
            )
        numeric_values = []
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = event.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise TaskReplayValidationError(f"{item_context}.{name}: expected int")
            numeric_values.append(
                _non_negative_number(value, f"{item_context}.{name}", optional=True)
            )
        numeric_values.append(
            _non_negative_number(
                event.get("cost_usd"), f"{item_context}.cost_usd", optional=True
            )
        )
        if quality == "unavailable" and any(
            value is not None for value in numeric_values
        ):
            raise TaskReplayValidationError(
                f"{item_context}: unavailable usage cannot contain numeric values"
            )
        if quality != "unavailable" and all(value is None for value in numeric_values):
            raise TaskReplayValidationError(
                f"{item_context}: measured or estimated usage needs a value"
            )
        event_by_key[key] = event
    for allocation in prediction.usage_allocations:
        key = allocation.usage_plane, allocation.usage_event_id
        if key not in event_by_key:
            raise TaskReplayValidationError(
                f"{context}.prediction: allocation references unknown usage event {key!r}"
            )
    return event_by_key


def validate_suite(suite: Any) -> None:
    if not isinstance(suite, dict):
        raise TaskReplayValidationError("suite: expected an object")
    version = _require(suite, "schema_version", int, "suite")
    if version != SCHEMA_VERSION:
        raise TaskReplayValidationError(
            f"suite.schema_version: supported={SCHEMA_VERSION}, got={version}"
        )
    _require(suite, "suite_id", str, "suite")
    config = _require(suite, "metric_config", dict, "suite")
    bins = _require(config, "ece_bins", int, "suite.metric_config")
    if bins <= 0:
        raise TaskReplayValidationError("suite.metric_config.ece_bins must be > 0")
    tolerance = _non_negative_number(
        config.get("usage_numeric_tolerance"),
        "suite.metric_config.usage_numeric_tolerance",
    )
    if tolerance > 1:
        raise TaskReplayValidationError(
            "suite.metric_config.usage_numeric_tolerance must be <= 1"
        )
    manifest = _require(suite, "run_manifest", dict, "suite")
    for key in (
        "repository_revision",
        "model",
        "harness",
        "prompt_fingerprint",
        "generated_at",
    ):
        _require(manifest, key, str, "suite.run_manifest")
    if not _SHA256_RE.fullmatch(manifest["prompt_fingerprint"]):
        raise TaskReplayValidationError(
            "suite.run_manifest.prompt_fingerprint must be sha256:<64 lowercase hex>"
        )
    if _require(manifest, "seed", int, "suite.run_manifest") < 0:
        raise TaskReplayValidationError("suite.run_manifest.seed must be >= 0")
    cases = _require(suite, "cases", list, "suite")
    if not cases:
        raise TaskReplayValidationError("suite.cases must not be empty")
    seen_case_ids = set()
    for index, case in enumerate(cases):
        context = f"suite.cases[{index}]"
        if not isinstance(case, dict):
            raise TaskReplayValidationError(f"{context}: expected an object")
        case_id = _require(case, "case_id", str, context)
        if not case_id or case_id in seen_case_ids:
            raise TaskReplayValidationError(f"{context}.case_id: empty or duplicate")
        seen_case_ids.add(case_id)
        gold = _compile_generation(case, "gold", context)
        prediction = _compile_generation(case, "prediction", context)
        _validate_gold(gold, f"{context}.gold")
        expected_atoms = {
            stable_ref_key(
                AtomRef(
                    gold.tenant_id,
                    gold.task_scope_id,
                    "source-fixture",
                    atom["traj_id"],
                    atom["atom_id"],
                )
            )
            for atom in case["atoms"]
        }
        if set(_confirmed_atom_assignment(gold)) != expected_atoms:
            raise TaskReplayValidationError(
                f"{context}.gold: every annotated Atom needs one confirmed membership"
            )
        _validate_usage(case, prediction, context)


def load_suite(path: Path | str) -> dict[str, Any]:
    suite_path = Path(path)
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TaskReplayValidationError(
            f"invalid JSON in {suite_path}: {error}"
        ) from error
    validate_suite(suite)
    return suite


def _clusters(graph: TaskGraphGeneration) -> tuple[dict[str, set[str]], dict[str, str]]:
    clusters: dict[str, set[str]] = defaultdict(set)
    assignment = _confirmed_atom_assignment(graph)
    for atom_key, task_id in assignment.items():
        clusters[task_id].add(atom_key)
    return dict(clusters), assignment


def _pairs(clusters: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {
        tuple(sorted(pair))
        for atoms in clusters.values()
        for pair in itertools.combinations(sorted(atoms), 2)
    }


def _prf(gold: set[Any], predicted: set[Any]) -> dict[str, float | int]:
    true_positive = len(gold & predicted)
    false_positive = len(predicted - gold)
    false_negative = len(gold - predicted)
    precision, recall, f1 = prf(true_positive, false_positive, false_negative)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _safe_ratio(numerator: float, denominator: float, *, empty: float = 1.0) -> float:
    return round(numerator / denominator, 6) if denominator else empty


def _grouping_counts(gold: TaskGraphGeneration, prediction: TaskGraphGeneration):
    gold_clusters, gold_assignment = _clusters(gold)
    predicted_clusters, predicted_assignment = _clusters(prediction)
    b3_precision_sum = 0.0
    b3_recall_sum = 0.0
    for atom_key, gold_task in gold_assignment.items():
        gold_cluster = gold_clusters[gold_task]
        predicted_task = predicted_assignment.get(atom_key)
        predicted_cluster = (
            predicted_clusters[predicted_task] if predicted_task else {atom_key}
        )
        overlap = len(gold_cluster & predicted_cluster)
        b3_precision_sum += overlap / len(predicted_cluster)
        b3_recall_sum += overlap / len(gold_cluster)
    gold_pairs = _pairs(gold_clusters)
    predicted_pairs = _pairs(predicted_clusters)
    return {
        "gold_pairs": gold_pairs,
        "predicted_pairs": predicted_pairs,
        "b3_precision_sum": b3_precision_sum,
        "b3_recall_sum": b3_recall_sum,
        "atom_count": len(gold_assignment),
        "false_splits": sorted(gold_pairs - predicted_pairs),
        "false_merges": sorted(predicted_pairs - gold_pairs),
    }


def _public_grouping(counts: dict[str, Any]) -> dict[str, Any]:
    pairwise = _prf(counts["gold_pairs"], counts["predicted_pairs"])
    precision = _safe_ratio(counts["b3_precision_sum"], counts["atom_count"])
    recall = _safe_ratio(counts["b3_recall_sum"], counts["atom_count"])
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "pairwise": pairwise,
        "b3": {"precision": precision, "recall": recall, "f1": round(f1, 6)},
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _task_relations(graph: TaskGraphGeneration) -> set[tuple[str, tuple, tuple]]:
    clusters, _ = _clusters(graph)
    result = set()
    for relation in graph.relations:
        if relation.decision != "confirmed" or relation.stale:
            continue
        source = tuple(sorted(clusters.get(relation.from_task_id, ())))
        target = tuple(sorted(clusters.get(relation.to_task_id, ())))
        if not source:
            source = (f"__empty__:{relation.from_task_id}",)
        if not target:
            target = (f"__empty__:{relation.to_task_id}",)
        result.add((f"task:{relation.relation_type}", source, target))
    return result


def _evidence_ids(attempt: TaskAttempt) -> set[str]:
    return {item.evidence_id for item in attempt.evidence_ranges if not item.stale}


def _align_attempts(
    gold: TaskGraphGeneration,
    prediction: TaskGraphGeneration,
) -> dict[str, str]:
    gold_clusters, _ = _clusters(gold)
    predicted_clusters, _ = _clusters(prediction)
    candidates = []
    for predicted_attempt in prediction.attempts:
        for gold_attempt in gold.attempts:
            evidence_score = _jaccard(
                _evidence_ids(predicted_attempt), _evidence_ids(gold_attempt)
            )
            if evidence_score == 0:
                continue
            task_score = _jaccard(
                predicted_clusters.get(predicted_attempt.task_id, set()),
                gold_clusters.get(gold_attempt.task_id, set()),
            )
            candidates.append(
                (
                    -(0.8 * evidence_score + 0.2 * task_score),
                    predicted_attempt.attempt_id,
                    gold_attempt.attempt_id,
                )
            )
    alignment = {}
    used_predicted = set()
    used_gold = set()
    for _score, predicted_id, gold_id in sorted(candidates):
        if predicted_id in used_predicted or gold_id in used_gold:
            continue
        alignment[predicted_id] = gold_id
        used_predicted.add(predicted_id)
        used_gold.add(gold_id)
    return alignment


def _attempt_relations(
    graph: TaskGraphGeneration,
    alignment: dict[str, str] | None = None,
) -> set[tuple[str, str, str]]:
    result = set()
    for relation in graph.attempt_relations:
        if relation.decision != "confirmed":
            continue
        source = relation.from_attempt_id
        target = relation.to_attempt_id
        if alignment is not None:
            source = alignment.get(source, f"__pred__:{source}")
            target = alignment.get(target, f"__pred__:{target}")
        result.add((f"attempt:{relation.relation_type}", source, target))
    return result


def _macro_metrics(gold: set[tuple], predicted: set[tuple]) -> dict[str, Any]:
    labels = sorted({item[0] for item in gold | predicted})
    by_type = {
        label: _prf(
            {item for item in gold if item[0] == label},
            {item for item in predicted if item[0] == label},
        )
        for label in labels
    }
    macro = {
        metric: _safe_ratio(
            sum(float(by_type[label][metric]) for label in labels), len(labels)
        )
        for metric in ("precision", "recall", "f1")
    }
    return {"micro": _prf(gold, predicted), "macro": macro, "by_type": by_type}


def _attempt_counts(
    gold: TaskGraphGeneration,
    prediction: TaskGraphGeneration,
    alignment: dict[str, str],
) -> dict[str, Any]:
    gold_by_id = {item.attempt_id: item for item in gold.attempts}
    predicted_by_id = {item.attempt_id: item for item in prediction.attempts}
    inverse = {gold_id: predicted_id for predicted_id, gold_id in alignment.items()}
    predicted_outcomes = {}
    correct = 0
    evidence_matches = 0
    evidence_total = 0
    errors = []
    for gold_id, gold_attempt in gold_by_id.items():
        gold_evidence = _evidence_ids(gold_attempt)
        evidence_total += len(gold_evidence)
        predicted_id = inverse.get(gold_id)
        if predicted_id is None:
            errors.append({"type": "attempt_missing", "attempt_id": gold_id})
            continue
        predicted_attempt = predicted_by_id[predicted_id]
        evidence_matches += len(gold_evidence & _evidence_ids(predicted_attempt))
        predicted_outcomes[gold_id] = predicted_attempt.outcome
        if predicted_attempt.outcome == gold_attempt.outcome:
            correct += 1
        else:
            errors.append(
                {
                    "type": "attempt_outcome_mismatch",
                    "attempt_id": gold_id,
                    "gold": gold_attempt.outcome,
                    "predicted": predicted_attempt.outcome,
                }
            )
    spurious = sorted(set(predicted_by_id) - set(alignment))
    errors.extend(
        {"type": "attempt_spurious", "attempt_id": attempt_id}
        for attempt_id in spurious
    )
    labels = sorted(
        {item.outcome for item in gold.attempts}
        | {item.outcome for item in prediction.attempts}
    )
    by_outcome_counts = {}
    by_outcome = {}
    for label in labels:
        true_positive = sum(
            predicted_outcomes.get(gold_id) == label and item.outcome == label
            for gold_id, item in gold_by_id.items()
        )
        false_negative = sum(
            item.outcome == label and predicted_outcomes.get(gold_id) != label
            for gold_id, item in gold_by_id.items()
        )
        false_positive = sum(
            outcome == label and gold_by_id[gold_id].outcome != label
            for gold_id, outcome in predicted_outcomes.items()
        ) + sum(predicted_by_id[attempt_id].outcome == label for attempt_id in spurious)
        precision, recall, f1 = prf(true_positive, false_positive, false_negative)
        by_outcome_counts[label] = (true_positive, false_positive, false_negative)
        by_outcome[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    return {
        "gold_ids": set(gold_by_id),
        "predicted_detection_ids": set(alignment.values())
        | {f"__pred__:{attempt_id}" for attempt_id in spurious},
        "gold_count": len(gold_by_id),
        "correct": correct,
        "by_outcome_counts": by_outcome_counts,
        "by_outcome": by_outcome,
        "macro_f1": _safe_ratio(
            sum(item["f1"] for item in by_outcome.values()), len(by_outcome)
        ),
        "evidence_matches": evidence_matches,
        "evidence_total": evidence_total,
        "errors": errors,
    }


def _calibration(samples: list[tuple[float, int]], bins: int) -> dict[str, Any]:
    if not samples:
        return {"count": 0, "brier": None, "ece": None}
    brier = sum((probability - target) ** 2 for probability, target in samples) / len(
        samples
    )
    grouped: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for probability, target in samples:
        grouped[min(int(probability * bins), bins - 1)].append((probability, target))
    ece = sum(
        len(values)
        / len(samples)
        * abs(
            sum(item[0] for item in values) / len(values)
            - sum(item[1] for item in values) / len(values)
        )
        for values in grouped.values()
    )
    return {"count": len(samples), "brier": round(brier, 6), "ece": round(ece, 6)}


def _membership_calibration(
    gold: TaskGraphGeneration,
    prediction: TaskGraphGeneration,
) -> tuple[list[tuple[float, int]], int]:
    gold_assignment = _confirmed_atom_assignment(gold)
    gold_clusters, _ = _clusters(gold)
    predicted_clusters, _ = _clusters(prediction)
    task_alignment = {}
    for task_id, atoms in predicted_clusters.items():
        ranked = sorted(
            (
                (_jaccard(atoms, gold_atoms), gold_id)
                for gold_id, gold_atoms in gold_clusters.items()
            ),
            key=lambda item: (-item[0], item[1]),
        )
        task_alignment[task_id] = ranked[0][1] if ranked and ranked[0][0] else None
    samples = []
    total = 0
    for membership in prediction.memberships:
        if membership.stale or membership.role != "primary":
            continue
        total += 1
        if membership.confidence is None:
            continue
        target = int(
            task_alignment.get(membership.task_id)
            == gold_assignment.get(stable_ref_key(membership.atom_ref))
        )
        samples.append((membership.confidence, target))
    return samples, total


def _outcome_confidence(attempt: TaskAttempt) -> float | None:
    decisions = [
        item
        for item in attempt.decisions
        if item.dimension == "outcome" and item.value == attempt.outcome
    ]
    if not decisions:
        return None
    return max(
        decisions, key=lambda item: (item.observed_at, item.decision_id)
    ).confidence


def _outcome_calibration(
    gold: TaskGraphGeneration,
    prediction: TaskGraphGeneration,
    alignment: dict[str, str],
) -> tuple[list[tuple[float, int]], int, int]:
    gold_by_id = {item.attempt_id: item for item in gold.attempts}
    samples = []
    covered_gold = set()
    for attempt in prediction.attempts:
        confidence = _outcome_confidence(attempt)
        if confidence is None:
            continue
        gold_id = alignment.get(attempt.attempt_id)
        if gold_id is not None:
            covered_gold.add(gold_id)
        target = int(
            gold_id is not None and attempt.outcome == gold_by_id[gold_id].outcome
        )
        samples.append((confidence, target))
    return samples, len(gold_by_id), len(covered_gold)


def _usage_counts(
    events: dict[tuple[str, str], dict],
    prediction: TaskGraphGeneration,
    tolerance: float,
):
    allocations_by_key: dict[tuple[str, str], list] = defaultdict(list)
    for allocation in prediction.usage_allocations:
        allocations_by_key[(allocation.usage_plane, allocation.usage_event_id)].append(
            allocation
        )
    planes = {
        plane: {
            "events": 0,
            "conserved_events": 0,
            "measured_events": 0,
            "estimated_events": 0,
            "unavailable_events": 0,
            "raw_total_tokens": 0,
            "allocated_total_tokens": 0,
            "raw_cost_usd": 0.0,
            "allocated_cost_usd": 0.0,
            "unattributed_fraction": 0.0,
            "shared_fraction": 0.0,
        }
        for plane in sorted(USAGE_PLANES)
    }
    errors = []
    for key, event in events.items():
        plane, event_id = key
        counts = planes[plane]
        counts["events"] += 1
        counts[f"{event['measurement_quality']}_events"] += 1
        allocations = allocations_by_key.get(key, [])
        fraction_sum = sum(item.fraction for item in allocations)
        counts["unattributed_fraction"] += sum(
            item.fraction
            for item in allocations
            if item.allocation_mode == "unattributed"
        )
        counts["shared_fraction"] += sum(
            item.fraction for item in allocations if item.allocation_mode == "shared"
        )
        conserved = abs(fraction_sum - 1.0) <= tolerance
        raw_tokens = event.get("total_tokens")
        allocated_tokens = sum(
            item.total_tokens for item in allocations if item.total_tokens is not None
        )
        raw_cost = event.get("cost_usd")
        allocated_cost = sum(
            float(item.cost_usd) for item in allocations if item.cost_usd is not None
        )
        if raw_tokens is not None:
            counts["raw_total_tokens"] += raw_tokens
            counts["allocated_total_tokens"] += allocated_tokens
            conserved = conserved and allocated_tokens == raw_tokens
        if raw_cost is not None:
            counts["raw_cost_usd"] += float(raw_cost)
            counts["allocated_cost_usd"] += allocated_cost
            conserved = conserved and abs(allocated_cost - float(raw_cost)) <= tolerance
        if conserved:
            counts["conserved_events"] += 1
        else:
            errors.append(
                {
                    "type": "usage_not_conserved",
                    "usage_plane": plane,
                    "usage_event_id": event_id,
                    "fraction_sum": round(fraction_sum, 9),
                    "token_delta": (
                        None if raw_tokens is None else allocated_tokens - raw_tokens
                    ),
                    "cost_delta": (
                        None
                        if raw_cost is None
                        else round(allocated_cost - raw_cost, 9)
                    ),
                }
            )
    return planes, errors


def _case_counts(case: dict, config: dict) -> dict[str, Any]:
    gold = _compile_generation(case, "gold", "case")
    prediction = _compile_generation(case, "prediction", "case")
    grouping = _grouping_counts(gold, prediction)
    alignment = _align_attempts(gold, prediction)
    gold_relations = _task_relations(gold) | _attempt_relations(gold)
    predicted_relations = _task_relations(prediction) | _attempt_relations(
        prediction, alignment
    )
    attempts = _attempt_counts(gold, prediction, alignment)
    membership_samples, membership_total = _membership_calibration(gold, prediction)
    outcome_samples, outcome_total, outcome_covered = _outcome_calibration(
        gold, prediction, alignment
    )
    usage_events = _validate_usage(case, prediction, "case")
    usage, usage_errors = _usage_counts(
        usage_events, prediction, config["usage_numeric_tolerance"]
    )
    errors = [
        {"type": "task_false_split", "atom_keys": list(pair)}
        for pair in grouping["false_splits"]
    ] + [
        {"type": "task_false_merge", "atom_keys": list(pair)}
        for pair in grouping["false_merges"]
    ]
    errors.extend(
        {"type": "relation_missing", "relation": list(item)}
        for item in sorted(gold_relations - predicted_relations)
    )
    errors.extend(
        {"type": "relation_spurious", "relation": list(item)}
        for item in sorted(predicted_relations - gold_relations)
    )
    errors.extend(attempts["errors"])
    errors.extend(usage_errors)
    return {
        "grouping": grouping,
        "gold_relations": gold_relations,
        "predicted_relations": predicted_relations,
        "attempts": attempts,
        "membership_samples": membership_samples,
        "membership_total": membership_total,
        "outcome_samples": outcome_samples,
        "outcome_total": outcome_total,
        "outcome_covered": outcome_covered,
        "usage": usage,
        "errors": errors,
    }


def _public_metrics(counts: dict[str, Any], bins: int) -> dict[str, Any]:
    attempts = counts["attempts"]
    membership = _calibration(counts["membership_samples"], bins)
    membership["coverage"] = _safe_ratio(
        membership["count"], counts["membership_total"], empty=0.0
    )
    outcome = _calibration(counts["outcome_samples"], bins)
    outcome["coverage"] = _safe_ratio(
        counts["outcome_covered"], counts["outcome_total"], empty=0.0
    )
    usage = {}
    for plane, values in counts["usage"].items():
        public = dict(values)
        event_count = public["events"]
        public["conservation_rate"] = _safe_ratio(
            public.pop("conserved_events"), event_count
        )
        public["unattributed_fraction"] = _safe_ratio(
            public["unattributed_fraction"], event_count, empty=0.0
        )
        public["shared_fraction"] = _safe_ratio(
            public["shared_fraction"], event_count, empty=0.0
        )
        for name in (
            "events",
            "measured_events",
            "estimated_events",
            "unavailable_events",
            "raw_total_tokens",
            "allocated_total_tokens",
        ):
            public[name] = int(public[name])
        for name in (
            "raw_cost_usd",
            "allocated_cost_usd",
            "unattributed_fraction",
            "shared_fraction",
        ):
            public[name] = round(public[name], 9)
        usage[plane] = public
    return {
        "task_grouping": _public_grouping(counts["grouping"]),
        "relations": _macro_metrics(
            counts["gold_relations"], counts["predicted_relations"]
        ),
        "attempt_detection": _prf(
            attempts["gold_ids"], attempts["predicted_detection_ids"]
        ),
        "attempt_outcome": {
            "accuracy": _safe_ratio(attempts["correct"], attempts["gold_count"]),
            "macro_f1": attempts["macro_f1"],
            "by_outcome": attempts["by_outcome"],
        },
        "confidence": {"membership": membership, "attempt_outcome": outcome},
        "evidence_coverage": _safe_ratio(
            attempts["evidence_matches"], attempts["evidence_total"], empty=0.0
        ),
        "usage": usage,
    }


def _prefix_relation(item: tuple, prefix: str) -> tuple:
    label, source, target = item
    if isinstance(source, tuple):
        return (
            label,
            tuple(prefix + atom_key for atom_key in source),
            tuple(prefix + atom_key for atom_key in target),
        )
    return label, prefix + source, prefix + target


def _merge_counts(case_counts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    merged = {
        "grouping": {
            "gold_pairs": set(),
            "predicted_pairs": set(),
            "b3_precision_sum": 0.0,
            "b3_recall_sum": 0.0,
            "atom_count": 0,
        },
        "gold_relations": set(),
        "predicted_relations": set(),
        "attempts": {
            "gold_ids": set(),
            "predicted_detection_ids": set(),
            "gold_count": 0,
            "correct": 0,
            "by_outcome_counts": defaultdict(lambda: [0, 0, 0]),
            "evidence_matches": 0,
            "evidence_total": 0,
        },
        "membership_samples": [],
        "membership_total": 0,
        "outcome_samples": [],
        "outcome_total": 0,
        "outcome_covered": 0,
        "usage": {plane: defaultdict(float) for plane in sorted(USAGE_PLANES)},
        "errors": [],
    }
    for index, counts in enumerate(case_counts):
        prefix = f"case-{index}:"
        grouping = counts["grouping"]
        for key in ("gold_pairs", "predicted_pairs"):
            merged["grouping"][key].update(
                (prefix + left, prefix + right) for left, right in grouping[key]
            )
        for key in ("b3_precision_sum", "b3_recall_sum", "atom_count"):
            merged["grouping"][key] += grouping[key]
        for key in ("gold_relations", "predicted_relations"):
            merged[key].update(_prefix_relation(item, prefix) for item in counts[key])
        attempts = counts["attempts"]
        for key in ("gold_ids", "predicted_detection_ids"):
            merged["attempts"][key].update(prefix + item for item in attempts[key])
        for key in ("gold_count", "correct", "evidence_matches", "evidence_total"):
            merged["attempts"][key] += attempts[key]
        for label, values in attempts["by_outcome_counts"].items():
            for value_index, value in enumerate(values):
                merged["attempts"]["by_outcome_counts"][label][value_index] += value
        for key in ("membership_samples", "outcome_samples"):
            merged[key].extend(counts[key])
        for key in ("membership_total", "outcome_total", "outcome_covered"):
            merged[key] += counts[key]
        for plane, values in counts["usage"].items():
            for key, value in values.items():
                merged["usage"][plane][key] += value
        merged["errors"].extend(
            {"case_index": index, **error} for error in counts["errors"]
        )
    by_outcome = {}
    for label, values in merged["attempts"]["by_outcome_counts"].items():
        precision, recall, f1 = prf(*values)
        by_outcome[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    merged["attempts"]["by_outcome"] = by_outcome
    merged["attempts"]["macro_f1"] = _safe_ratio(
        sum(item["f1"] for item in by_outcome.values()), len(by_outcome)
    )
    return merged


def evaluate_suite(suite: dict[str, Any]) -> dict[str, Any]:
    validate_suite(suite)
    config = suite["metric_config"]
    counts = [_case_counts(case, config) for case in suite["cases"]]
    merged = _merge_counts(counts)
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite["suite_id"],
        "run_manifest": suite["run_manifest"],
        "metric_config": config,
        "metrics": _public_metrics(merged, config["ece_bins"]),
        "error_count": len(merged["errors"]),
        "cases": [
            {
                "case_id": case["case_id"],
                "metrics": _public_metrics(case_count, config["ece_bins"]),
                "errors": case_count["errors"],
            }
            for case, case_count in zip(suite["cases"], counts)
        ],
    }
    report = json.loads(json.dumps(report, ensure_ascii=False, sort_keys=True))
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def render_text(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    grouping = metrics["task_grouping"]
    return "\n".join(
        (
            f"suite: {report['suite_id']}",
            f"revision: {report['run_manifest']['repository_revision']}",
            (
                f"task_pairwise_f1={grouping['pairwise']['f1']:.3f} "
                f"b3_f1={grouping['b3']['f1']:.3f}"
            ),
            f"relation_macro_f1={metrics['relations']['macro']['f1']:.3f}",
            f"attempt_outcome_accuracy={metrics['attempt_outcome']['accuracy']:.3f}",
            f"evidence_coverage={metrics['evidence_coverage']:.3f}",
            (
                f"usage_execution_conservation="
                f"{metrics['usage']['execution']['conservation_rate']:.3f}"
            ),
            (
                f"usage_processing_conservation="
                f"{metrics['usage']['xskill_processing']['conservation_rate']:.3f}"
            ),
            f"errors={report['error_count']}",
            f"report_sha256={report['report_sha256']}",
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a recorded xskill Logical Task/Attempt replay suite."
    )
    parser.add_argument("suite", type=Path, help="Path to the versioned replay JSON")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = evaluate_suite(load_suite(args.suite))
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
