"""Validate pinned OfficeQA inputs and aggregate local evaluation results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

VENDORED_SCORER = Path(__file__).with_name("vendor") / "reward.py"
CORPUS_TREE_HASH_FORMAT = (
    "SHA-256 over root-level files sorted by name; each entry is UTF-8 "
    "filename, NUL, lowercase file SHA-256, LF"
)
RESULT_SCHEMA_VERSION = 1
RESULT_STATUSES = frozenset({
    "pass", "fail", "invalid", "timeout", "infra_error", "skipped",
})
USAGE_FIELDS = (
    "request_attempts",
    "request_retries",
    "calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_miss_tokens",
    "unavailable_calls",
)


class OfficeQAValidationError(ValueError):
    """Raised when a gated input or local result violates the run contract."""


@dataclass(frozen=True)
class OfficeQACase:
    """One gated case kept in memory only while the evaluator is running."""

    uid: str
    question: str
    answer: str
    difficulty: str
    source_files: tuple[str, ...]


def sha256_file(path: Path | str) -> str:
    """Return a streaming SHA-256 digest without loading a large file at once."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_corpus_tree(corpus_dir: Path | str) -> tuple[str, int, int]:
    """Hash the exact flat TXT workspace that the model is allowed to search."""
    root = Path(corpus_dir).resolve()
    files = sorted(
        (path for path in root.rglob("*.txt") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    nested = [path.relative_to(root).as_posix() for path in files if path.parent != root]
    if nested:
        raise OfficeQAValidationError(
            f"OfficeQA corpus contains nested TXT files outside the pinned flat workspace: {nested[:5]}"
        )
    digest = hashlib.sha256()
    total_size = 0
    for path in files:
        file_digest = sha256_file(path)
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
        total_size += path.stat().st_size
    return digest.hexdigest(), len(files), total_size


def _source_files(raw: str) -> tuple[str, ...]:
    return tuple(
        name.strip()
        for name in re.split(r"[;\r\n]+", raw or "")
        if name.strip()
    )


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _corpus_file(corpus_root: Path, relative_name: str) -> Path:
    """Resolve one manifest path without allowing it to escape the corpus root."""
    candidate = (corpus_root / relative_name).resolve()
    try:
        candidate.relative_to(corpus_root)
    except ValueError as error:
        raise OfficeQAValidationError(
            f"OfficeQA source_files entry escapes the corpus root: {relative_name!r}"
        ) from error
    return candidate


def load_cases(
    csv_path: Path | str,
    manifest_path: Path | str,
    corpus_dir: Path | str,
) -> tuple[list[OfficeQACase], dict[str, Any]]:
    """Load gated rows after validating provenance, public UIDs, and corpus coverage."""
    csv_file = Path(csv_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    corpus_root = Path(corpus_dir).resolve()
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfficeQAValidationError(f"invalid manifest {manifest_file}: {error}") from error

    source = manifest.get("source") or {}
    expected_hash = source.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise OfficeQAValidationError("manifest source.sha256 must be a pinned lowercase digest")
    if (
        source.get("file") != "officeqa_full.csv"
        or not isinstance(source.get("revision"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", source["revision"])
        or isinstance(source.get("file_size_bytes"), bool)
        or not isinstance(source.get("file_size_bytes"), int)
        or source["file_size_bytes"] <= 0
    ):
        raise OfficeQAValidationError("manifest source file, revision, and size must be pinned")
    if not csv_file.is_file():
        raise OfficeQAValidationError(f"OfficeQA CSV not found: {csv_file}")
    actual_hash = sha256_file(csv_file)
    if actual_hash != expected_hash:
        raise OfficeQAValidationError(
            f"OfficeQA CSV SHA-256 mismatch: expected={expected_hash}, actual={actual_hash}"
        )
    if csv_file.stat().st_size != source.get("file_size_bytes"):
        raise OfficeQAValidationError("OfficeQA CSV size does not match the pinned manifest")
    if not corpus_root.is_dir():
        raise OfficeQAValidationError(f"OfficeQA corpus directory not found: {corpus_root}")

    required_columns = {
        "uid", "question", "answer", "source_docs", "source_files", "difficulty",
    }
    with csv_file.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        if not required_columns.issubset(columns):
            missing = sorted(required_columns - columns)
            raise OfficeQAValidationError(f"OfficeQA CSV is missing columns: {missing}")
        rows = list(reader)

    public_samples = manifest.get("samples")
    if not isinstance(public_samples, list):
        raise OfficeQAValidationError("manifest samples must be a list")
    expected = {
        sample.get("uid"): sample.get("difficulty")
        for sample in public_samples
        if isinstance(sample, dict)
    }
    if len(expected) != len(public_samples):
        raise OfficeQAValidationError("manifest samples contain invalid or duplicate UIDs")

    by_uid: dict[str, OfficeQACase] = {}
    referenced_files: set[str] = set()
    for row in rows:
        uid = (row.get("uid") or "").strip()
        difficulty = (row.get("difficulty") or "").strip()
        question = (row.get("question") or "").strip()
        answer = (row.get("answer") or "").strip()
        files = _source_files(row.get("source_files") or "")
        if not uid or uid in by_uid:
            raise OfficeQAValidationError(f"OfficeQA CSV contains an empty or duplicate UID: {uid!r}")
        if not question or not answer:
            raise OfficeQAValidationError(f"OfficeQA CSV case {uid} has an empty question or answer")
        if not files:
            raise OfficeQAValidationError(f"OfficeQA CSV case {uid} has no source_files")
        if expected.get(uid) != difficulty:
            raise OfficeQAValidationError(f"OfficeQA CSV case {uid} disagrees with the public manifest")
        by_uid[uid] = OfficeQACase(uid, question, answer, difficulty, files)
        referenced_files.update(files)

    if set(by_uid) != set(expected):
        missing = sorted(set(expected) - set(by_uid))
        unexpected = sorted(set(by_uid) - set(expected))
        raise OfficeQAValidationError(
            f"OfficeQA CSV UID set mismatch: missing={missing}, unexpected={unexpected}"
        )
    missing_files = sorted(
        name for name in referenced_files if not _corpus_file(corpus_root, name).is_file()
    )
    if missing_files:
        raise OfficeQAValidationError(
            f"OfficeQA corpus is missing {len(missing_files)} referenced files: {missing_files[:5]}"
        )
    corpus = manifest.get("corpus") or {}
    if (
        corpus.get("relative_dir") != "treasury_bulletins_parsed/transformed"
        or corpus.get("file_glob") != "*.txt"
        or corpus.get("tree_hash_format") != CORPUS_TREE_HASH_FORMAT
        or isinstance(corpus.get("file_count"), bool)
        or not isinstance(corpus.get("file_count"), int)
        or corpus["file_count"] <= 0
        or isinstance(corpus.get("total_size_bytes"), bool)
        or not isinstance(corpus.get("total_size_bytes"), int)
        or corpus["total_size_bytes"] <= 0
        or not isinstance(corpus.get("tree_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", corpus["tree_sha256"])
    ):
        raise OfficeQAValidationError("manifest corpus tree contract is unsupported")
    corpus_hash, corpus_file_count, corpus_size = sha256_corpus_tree(corpus_root)
    if corpus_hash != corpus.get("tree_sha256"):
        raise OfficeQAValidationError(
            "OfficeQA corpus tree SHA-256 does not match the pinned manifest"
        )
    if (
        corpus_file_count != corpus.get("file_count")
        or corpus_size != corpus.get("total_size_bytes")
    ):
        raise OfficeQAValidationError(
            "OfficeQA corpus file count or size does not match the pinned manifest"
        )

    ordered = [by_uid[sample["uid"]] for sample in public_samples]
    difficulty_counts = Counter(case.difficulty for case in ordered)
    if dict(sorted(difficulty_counts.items())) != manifest.get("difficulty_counts"):
        raise OfficeQAValidationError("OfficeQA difficulty counts disagree with the manifest")
    if len(ordered) != manifest.get("sample_count"):
        raise OfficeQAValidationError("OfficeQA sample count disagrees with the manifest")
    metadata = {
        "csv_sha256": actual_hash,
        "manifest_sha256": sha256_file(manifest_file),
        "sample_count": len(ordered),
        "referenced_file_count": len(referenced_files),
        "corpus_file_count": corpus_file_count,
        "corpus_size_bytes": corpus_size,
        "corpus_tree_sha256": corpus_hash,
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
    }
    return ordered, metadata


def load_scorer(
    scorer_path: Path | str,
    expected_sha256: str,
) -> Callable[[str, str, float], float]:
    """Load the exact upstream reward.py only after its digest is verified."""
    path = Path(scorer_path).resolve()
    if not path.is_file():
        raise OfficeQAValidationError(f"OfficeQA scorer not found: {path}")
    actual_hash = sha256_file(path)
    if actual_hash != expected_sha256:
        raise OfficeQAValidationError(
            f"OfficeQA scorer SHA-256 mismatch: expected={expected_sha256}, actual={actual_hash}"
        )
    source = path.read_text(encoding="utf-8")
    # The pinned upstream file uses PEP 604 annotations without a future import.
    # Compile a transient future import only after digest verification so xskill's
    # Python 3.9 CI can execute the exact, unmodified upstream bytes.
    namespace: dict[str, Any] = {
        "__file__": str(path),
        "__name__": "xskill_officeqa_reward",
    }
    code = compile("from __future__ import annotations\n" + source, str(path), "exec")
    exec(code, namespace)  # noqa: S102 - execution is restricted by the pinned digest
    score_answer = namespace.get("score_answer")
    if not callable(score_answer):
        raise OfficeQAValidationError("OfficeQA scorer does not export score_answer")
    return score_answer


def _nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfficeQAValidationError(f"result {field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise OfficeQAValidationError(f"result {field} must be finite and non-negative")
    return number


def validate_result_record(result: Any, *, line_number: int | None = None) -> dict[str, Any]:
    """Validate one runner record before it can contribute to an aggregate."""
    location = f" at line {line_number}" if line_number is not None else ""
    if not isinstance(result, dict):
        raise OfficeQAValidationError(f"result record must be an object{location}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise OfficeQAValidationError(f"unsupported result schema_version{location}")
    uid = result.get("uid")
    if not isinstance(uid, str) or not uid:
        raise OfficeQAValidationError(f"result uid must be a non-empty string{location}")
    if result.get("difficulty") not in {"easy", "hard"}:
        raise OfficeQAValidationError(f"invalid result difficulty for {uid}{location}")
    status = result.get("status")
    if status not in RESULT_STATUSES:
        raise OfficeQAValidationError(f"invalid result status for {uid}{location}: {status!r}")
    prediction = result.get("prediction")
    if not isinstance(prediction, str):
        raise OfficeQAValidationError(f"result prediction must be a string for {uid}{location}")
    score = _nonnegative_number(result.get("score"), f"score for {uid}{location}")
    if score > 1:
        raise OfficeQAValidationError(f"result score must be at most 1 for {uid}{location}")
    if status in {"pass", "fail"} and not prediction.strip():
        raise OfficeQAValidationError(f"scored result has no prediction for {uid}{location}")
    if status == "pass" and score != 1:
        raise OfficeQAValidationError(f"pass result must have score 1 for {uid}{location}")
    if status == "fail" and score == 1:
        raise OfficeQAValidationError(f"fail result cannot have score 1 for {uid}{location}")
    usage = result.get("usage")
    if not isinstance(usage, dict):
        raise OfficeQAValidationError(f"result usage must be an object for {uid}{location}")
    for field in USAGE_FIELDS:
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OfficeQAValidationError(
                f"result usage.{field} must be a non-negative integer for {uid}{location}"
            )
    if usage["request_retries"] > usage["request_attempts"]:
        raise OfficeQAValidationError(
            f"result request retries exceed attempts for {uid}{location}"
        )
    if usage["calls"] > usage["request_attempts"]:
        raise OfficeQAValidationError(
            f"result successful calls exceed request attempts for {uid}{location}"
        )
    if usage["unavailable_calls"] > usage["calls"]:
        raise OfficeQAValidationError(
            f"result unavailable usage calls exceed successful calls for {uid}{location}"
        )
    if usage["cache_read_tokens"] + usage["cache_miss_tokens"] != usage["input_tokens"]:
        raise OfficeQAValidationError(
            f"result cache token split disagrees with input tokens for {uid}{location}"
        )
    _nonnegative_number(result.get("latency_seconds"), f"latency_seconds for {uid}{location}")
    if not isinstance(result.get("tool_calls"), list):
        raise OfficeQAValidationError(f"result tool_calls must be a list for {uid}{location}")
    cost = result.get("cost")
    if not isinstance(cost, dict):
        raise OfficeQAValidationError(f"result cost must be an object for {uid}{location}")
    if cost.get("method") == "unavailable":
        if any(cost.get(field) is not None for field in ("amount", "currency", "price_label")):
            raise OfficeQAValidationError(
                f"unavailable result cost must not contain values for {uid}{location}"
            )
    elif cost.get("method") == "estimated_from_reported_tokens":
        price_label = cost.get("price_label")
        if (
            cost.get("currency") != "USD"
            or not isinstance(price_label, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", price_label)
        ):
            raise OfficeQAValidationError(f"invalid estimated cost metadata for {uid}{location}")
        if usage["unavailable_calls"]:
            raise OfficeQAValidationError(
                f"estimated cost requires complete reported usage for {uid}{location}"
            )
        if not isinstance(cost.get("amount"), str) or not cost["amount"]:
            raise OfficeQAValidationError(
                f"estimated cost amount must be a decimal string for {uid}{location}"
            )
        try:
            amount = Decimal(cost["amount"])
        except (InvalidOperation, TypeError, ValueError) as error:
            raise OfficeQAValidationError(
                f"invalid estimated cost amount for {uid}{location}"
            ) from error
        if not amount.is_finite() or amount < 0:
            raise OfficeQAValidationError(f"invalid estimated cost amount for {uid}{location}")
    else:
        raise OfficeQAValidationError(f"invalid result cost method for {uid}{location}")
    error_type = result.get("error_type")
    if error_type is not None and (
        not isinstance(error_type, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,99}", error_type)
    ):
        raise OfficeQAValidationError(f"invalid result error_type for {uid}{location}")
    error_message = result.get("error_message")
    if error_message is not None and (
        not isinstance(error_message, str)
        or len(error_message) > 160
        or "\n" in error_message
        or "\r" in error_message
    ):
        raise OfficeQAValidationError(f"invalid result error_message for {uid}{location}")
    error_code = result.get("error_code")
    if error_code is not None and (
        isinstance(error_code, bool)
        or not isinstance(error_code, int)
        or not 100 <= error_code <= 599
    ):
        raise OfficeQAValidationError(f"invalid result error_code for {uid}{location}")
    if status in {"pass", "fail"} and any(
        value is not None for value in (error_type, error_message, error_code)
    ):
        raise OfficeQAValidationError(f"scored result contains error metadata for {uid}{location}")
    if status in {"timeout", "infra_error"} and error_type is None:
        raise OfficeQAValidationError(f"failed result has no error_type for {uid}{location}")
    return result


def load_results(
    path: Path | str,
    *,
    expected_uids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load a JSONL result ledger and reject invalid or duplicate records."""
    result_path = Path(path)
    if not result_path.exists():
        return {}
    results: dict[str, dict[str, Any]] = {}
    with result_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError as error:
                raise OfficeQAValidationError(
                    f"invalid result JSON at {result_path}:{line_number}: {error}"
                ) from error
            result = validate_result_record(result, line_number=line_number)
            uid = result["uid"]
            if uid in results:
                raise OfficeQAValidationError(
                    f"duplicate result UID at {result_path}:{line_number}"
                )
            if expected_uids is not None and uid not in expected_uids:
                raise OfficeQAValidationError(
                    f"unexpected result UID at {result_path}:{line_number}: {uid}"
                )
            results[uid] = result
    return results


def verify_scores(
    cases: list[OfficeQACase],
    results: dict[str, dict[str, Any]],
    score_answer: Callable[[str, str, float], float],
    tolerance: float,
) -> None:
    """Recompute every scored prediction with the pinned upstream scorer."""
    by_uid = {case.uid: case for case in cases}
    for uid, result in results.items():
        case = by_uid.get(uid)
        if case is None:
            raise OfficeQAValidationError(f"result UID is absent from the manifest: {uid}")
        if result["difficulty"] != case.difficulty:
            raise OfficeQAValidationError(f"result difficulty disagrees with manifest for {uid}")
        if result["status"] not in {"pass", "fail"}:
            continue
        try:
            recomputed = float(score_answer(case.answer, result["prediction"], tolerance))
        except Exception as error:  # noqa: BLE001 - isolate the pinned upstream scorer boundary
            raise OfficeQAValidationError(f"scorer failed for {uid}: {error}") from error
        if not math.isfinite(recomputed) or not 0 <= recomputed <= 1:
            raise OfficeQAValidationError(f"scorer returned an invalid value for {uid}")
        expected_status = "pass" if recomputed == 1 else "fail"
        if recomputed != float(result["score"]) or expected_status != result["status"]:
            raise OfficeQAValidationError(
                f"result score disagrees with the pinned scorer for {uid}"
            )


def summarize_results(
    selected_uids: list[str],
    results: dict[str, dict[str, Any]],
    *,
    expected_full_uids: list[str],
) -> dict[str, Any]:
    """Summarize terminal states without mislabelling a subset as a Full score."""
    if len(set(selected_uids)) != len(selected_uids):
        raise OfficeQAValidationError("selected UID list contains duplicates")
    unknown = sorted(set(results) - set(selected_uids))
    if unknown:
        raise OfficeQAValidationError(f"result ledger contains unselected UIDs: {unknown}")
    counts = Counter(
        results[uid]["status"]
        for uid in selected_uids
        if uid in results
    )
    selected_count = len(selected_uids)
    recorded_count = sum(counts.values())
    scored_count = counts["pass"] + counts["fail"]
    all_terminal = recorded_count == selected_count
    clean_terminal = all_terminal and scored_count == selected_count
    is_full = len(selected_uids) == len(expected_full_uids) and (
        set(selected_uids) == set(expected_full_uids)
    )
    recorded_costs = [results[uid]["cost"] for uid in selected_uids if uid in results]
    priced_costs = [
        cost for cost in recorded_costs
        if cost["method"] == "estimated_from_reported_tokens"
    ]
    complete_cost = bool(recorded_costs) and len(priced_costs) == len(recorded_costs)
    price_labels = {cost["price_label"] for cost in priced_costs}
    if complete_cost and len(price_labels) != 1:
        raise OfficeQAValidationError("result ledger contains mixed price labels")
    cost_summary = {
        "method": "estimated_from_reported_tokens" if complete_cost else "unavailable",
        "currency": "USD" if complete_cost else None,
        "amount": (
            format(sum((Decimal(cost["amount"]) for cost in priced_costs), Decimal(0)), "f")
            if complete_cost
            else None
        ),
        "price_label": next(iter(price_labels)) if complete_cost else None,
        "priced_result_count": len(priced_costs),
    }
    return {
        "selected_count": selected_count,
        "recorded_count": recorded_count,
        "pending_count": selected_count - recorded_count,
        "status_counts": {name: counts[name] for name in sorted(RESULT_STATUSES)},
        "scored_accuracy": (
            counts["pass"] / scored_count if scored_count else None
        ),
        "official_full_accuracy": (
            counts["pass"] / selected_count if is_full and clean_terminal else None
        ),
        "is_full_selection": is_full,
        "is_complete": all_terminal,
        "is_cleanly_scorable": clean_terminal,
        "tokens": {
            name: sum(
                results[uid]["usage"][name]
                for uid in selected_uids
                if uid in results
            )
            for name in (
                "input_tokens", "output_tokens", "total_tokens",
                "cache_read_tokens", "cache_miss_tokens", "calls",
            )
        },
        "requests": {
            name: sum(
                results[uid]["usage"][field]
                for uid in selected_uids
                if uid in results
            )
            for name, field in (
                ("attempts", "request_attempts"),
                ("retries", "request_retries"),
                ("successful", "calls"),
                ("usage_unavailable", "unavailable_calls"),
            )
        },
        "cost": cost_summary,
        "latency_seconds": sum(
            float(results[uid]["latency_seconds"])
            for uid in selected_uids
            if uid in results
        ),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and aggregate pinned OfficeQA Full results",
    )
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, default=VENDORED_SCORER)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uid", action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfficeQAValidationError(f"invalid manifest {args.manifest}: {error}") from error
    cases, input_metadata = load_cases(args.csv, args.manifest, args.corpus_dir)
    by_uid = {case.uid: case for case in cases}
    if args.uid:
        selected_uids = list(dict.fromkeys(args.uid))
        if len(selected_uids) != len(args.uid):
            raise OfficeQAValidationError("--uid contains duplicates")
        missing = sorted(set(selected_uids) - set(by_uid))
        if missing:
            raise OfficeQAValidationError(f"requested UIDs are unavailable: {missing}")
    else:
        selected_uids = [case.uid for case in cases]
    scorer_config = manifest.get("scorer") or {}
    expected_scorer_hash = scorer_config.get("sha256")
    if (
        not isinstance(scorer_config.get("commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", scorer_config["commit"])
        or not isinstance(expected_scorer_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_scorer_hash)
    ):
        raise OfficeQAValidationError("manifest scorer commit and SHA-256 must be pinned")
    raw_tolerance = scorer_config.get("tolerance", 0.0)
    if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)):
        raise OfficeQAValidationError("manifest scorer.tolerance must be a number")
    tolerance = float(raw_tolerance)
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise OfficeQAValidationError(
            "manifest scorer.tolerance must be finite and between 0 and 1"
        )
    if not args.results.is_file():
        raise OfficeQAValidationError(f"OfficeQA result ledger not found: {args.results}")
    output_path = args.output.resolve()
    protected_inputs = {
        args.csv.resolve(),
        args.manifest.resolve(),
        args.scorer.resolve(),
        args.results.resolve(),
    }
    if output_path in protected_inputs:
        raise OfficeQAValidationError("--output must not overwrite an evaluation input")
    score_answer = load_scorer(args.scorer, expected_scorer_hash)
    results = load_results(args.results, expected_uids=set(selected_uids))
    verify_scores(cases, results, score_answer, tolerance)
    summary = {
        "schema_version": 1,
        "benchmark": "officeqa_full",
        "selection": {
            "uids": selected_uids,
            "sha256": _canonical_sha256(selected_uids),
        },
        **summarize_results(
            selected_uids,
            results,
            expected_full_uids=[case.uid for case in cases],
        ),
        **input_metadata,
        "scorer_commit": scorer_config.get("commit"),
        "scorer_sha256": sha256_file(args.scorer),
        "tolerance": tolerance,
        "results_sha256": sha256_file(args.results),
    }
    _write_json(output_path, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfficeQAValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
