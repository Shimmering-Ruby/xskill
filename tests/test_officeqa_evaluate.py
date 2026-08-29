"""Offline contracts for pinned OfficeQA inputs, scoring, and aggregation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.bench.officeqa.evaluate import (
    CORPUS_TREE_HASH_FORMAT,
    VENDORED_SCORER,
    OfficeQAValidationError,
    load_cases,
    load_results,
    load_scorer,
    main,
    sha256_file,
    sha256_corpus_tree,
    summarize_results,
    verify_scores,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    csv_path = tmp_path / "officeqa_full.csv"
    rows = [
        {
            "uid": "UID0001",
            "question": "Synthetic question one?",
            "answer": "10",
            "source_docs": "https://example.invalid/one",
            "source_files": "doc-one.txt\ndoc-two.txt",
            "difficulty": "hard",
        },
        {
            "uid": "UID0002",
            "question": "Synthetic question two?",
            "answer": "20",
            "source_docs": "https://example.invalid/two",
            "source_files": "doc-two.txt;doc-three.txt",
            "difficulty": "easy",
        },
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "source": {
            "file": "officeqa_full.csv",
            "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            "file_size_bytes": csv_path.stat().st_size,
            "revision": "a" * 40,
        },
        "scorer": {
            "commit": "b" * 40,
            "sha256": "0" * 64,
            "tolerance": 0.0,
        },
        "sample_count": 2,
        "difficulty_counts": {"easy": 1, "hard": 1},
        "samples": [
            {"uid": "UID0001", "difficulty": "hard"},
            {"uid": "UID0002", "difficulty": "easy"},
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("doc-one.txt", "doc-two.txt", "doc-three.txt"):
        (corpus / name).write_text("synthetic corpus\n", encoding="utf-8")
    tree_hash, file_count, total_size = sha256_corpus_tree(corpus)
    manifest["corpus"] = {
        "relative_dir": "treasury_bulletins_parsed/transformed",
        "file_glob": "*.txt",
        "file_count": file_count,
        "total_size_bytes": total_size,
        "tree_sha256": tree_hash,
        "tree_hash_format": CORPUS_TREE_HASH_FORMAT,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return csv_path, manifest_path, corpus


def _result(
    uid: str,
    *,
    difficulty: str = "hard",
    status: str = "pass",
    score: float = 1.0,
    prediction: str = "10",
) -> dict:
    return {
        "schema_version": 1,
        "uid": uid,
        "difficulty": difficulty,
        "status": status,
        "score": score,
        "prediction": prediction,
        "usage": {
            "request_attempts": 1,
            "request_retries": 0,
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "cache_read_tokens": 0,
            "cache_miss_tokens": 10,
            "unavailable_calls": 0,
        },
        "latency_seconds": 1.5,
        "tool_calls": [],
        "cost": {
            "method": "unavailable",
            "currency": None,
            "amount": None,
            "price_label": None,
        },
        "error_type": None,
        "error_message": None,
        "error_code": None,
        "completed_at": "2026-08-30T00:00:00+00:00",
    }


def _write_results(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def test_load_cases_validates_hash_manifest_and_all_referenced_files(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)

    cases, metadata = load_cases(csv_path, manifest_path, corpus)

    assert [case.uid for case in cases] == ["UID0001", "UID0002"]
    assert cases[0].source_files == ("doc-one.txt", "doc-two.txt")
    assert metadata["sample_count"] == 2
    assert metadata["referenced_file_count"] == 3


def test_load_cases_rejects_missing_or_escaping_corpus_file(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    (corpus / "doc-three.txt").unlink()
    with pytest.raises(OfficeQAValidationError, match="missing 1 referenced files"):
        load_cases(csv_path, manifest_path, corpus)

    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
    rows[0]["source_files"] = "../outside.txt"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["sha256"] = sha256_file(csv_path)
    manifest["source"]["file_size_bytes"] = csv_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(OfficeQAValidationError, match="escapes the corpus root"):
        load_cases(csv_path, manifest_path, corpus)


def test_load_cases_rejects_changed_gated_csv(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    with csv_path.open("a", encoding="utf-8") as handle:
        handle.write("changed")

    with pytest.raises(OfficeQAValidationError, match="SHA-256 mismatch"):
        load_cases(csv_path, manifest_path, corpus)


def test_load_cases_rejects_modified_or_nested_search_corpus(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    (corpus / "doc-one.txt").write_text("modified corpus\n", encoding="utf-8")
    with pytest.raises(OfficeQAValidationError, match="tree SHA-256"):
        load_cases(csv_path, manifest_path, corpus)

    csv_path, manifest_path, corpus = _fixture(tmp_path / "nested")
    nested = corpus / "__MACOSX"
    nested.mkdir()
    (nested / "._doc-one.txt").write_text("metadata\n", encoding="utf-8")
    with pytest.raises(OfficeQAValidationError, match="nested TXT files"):
        load_cases(csv_path, manifest_path, corpus)


def test_load_scorer_requires_exact_digest_and_callable(tmp_path):
    scorer = tmp_path / "reward.py"
    scorer.write_text(
        "def score_answer(ground_truth, predicted, tolerance=0.0):\n"
        "    return float(ground_truth == predicted)\n",
        encoding="utf-8",
    )
    digest = sha256_file(scorer)

    score_answer = load_scorer(scorer, digest)

    assert score_answer("10", "10", 0.0) == 1.0
    with pytest.raises(OfficeQAValidationError, match="SHA-256 mismatch"):
        load_scorer(scorer, "0" * 64)


def test_vendored_scorer_is_the_exact_pinned_upstream_file():
    assert sha256_file(VENDORED_SCORER) == (
        "0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f"
    )


def test_result_ledger_rejects_duplicate_invalid_and_unexpected_records(tmp_path):
    path = tmp_path / "results.jsonl"
    record = _result("UID0001")
    _write_results(path, [record, record])
    with pytest.raises(OfficeQAValidationError, match="duplicate result UID"):
        load_results(path)

    invalid = _result("UID0001")
    invalid["usage"]["input_tokens"] = -1
    _write_results(path, [invalid])
    with pytest.raises(OfficeQAValidationError, match="usage.input_tokens"):
        load_results(path)

    _write_results(path, [_result("UID0002", difficulty="easy")])
    with pytest.raises(OfficeQAValidationError, match="unexpected result UID"):
        load_results(path, expected_uids={"UID0001"})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("request_retries", 2, "retries exceed attempts"),
        ("calls", 2, "calls exceed request attempts"),
        ("unavailable_calls", 2, "unavailable usage calls"),
        ("cache_read_tokens", 1, "cache token split"),
    ],
)
def test_result_ledger_rejects_inconsistent_usage_counters(
    tmp_path,
    field,
    value,
    message,
):
    path = tmp_path / "results.jsonl"
    record = _result("UID0001")
    record["usage"][field] = value
    _write_results(path, [record])

    with pytest.raises(OfficeQAValidationError, match=message):
        load_results(path)


def test_result_ledger_rejects_unsafe_or_inconsistent_error_metadata(tmp_path):
    path = tmp_path / "results.jsonl"
    record = _result("UID0001")
    record["error_message"] = "private endpoint\nhttps://secret.invalid"
    _write_results(path, [record])
    with pytest.raises(OfficeQAValidationError, match="error_message"):
        load_results(path)

    failed = _result("UID0001", status="fail", score=0.0, prediction="9")
    failed["error_code"] = 429
    _write_results(path, [failed])
    with pytest.raises(OfficeQAValidationError, match="contains error metadata"):
        load_results(path)


def test_verify_scores_rejects_runner_disagreement(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    cases, _metadata = load_cases(csv_path, manifest_path, corpus)
    results = {"UID0001": _result("UID0001", status="fail", score=0.0)}

    with pytest.raises(OfficeQAValidationError, match="disagrees with the pinned scorer"):
        verify_scores(cases, results, lambda answer, prediction, tolerance: 1.0, 0.0)


def test_summary_requires_exact_full_uid_set_and_clean_scores():
    one = _result("UID0001")
    subset = summarize_results(
        ["UID0001"],
        {"UID0001": one},
        expected_full_uids=["UID0001", "UID0002"],
    )
    wrong_set = summarize_results(
        ["UID0001", "UID9999"],
        {"UID0001": one},
        expected_full_uids=["UID0001", "UID0002"],
    )
    incomplete = summarize_results(
        ["UID0001", "UID0002"],
        {"UID0001": one},
        expected_full_uids=["UID0001", "UID0002"],
    )

    assert subset["official_full_accuracy"] is None
    assert wrong_set["is_full_selection"] is False
    assert incomplete["is_full_selection"] is True
    assert incomplete["is_complete"] is False
    assert incomplete["official_full_accuracy"] is None


def test_summary_aggregates_only_one_complete_public_price_snapshot():
    one = _result("UID0001")
    two = _result("UID0002", difficulty="easy")
    for result, amount in ((one, "0.001"), (two, "0.002")):
        result["cost"] = {
            "method": "estimated_from_reported_tokens",
            "currency": "USD",
            "amount": amount,
            "price_label": "public-2026-08-30",
        }

    summary = summarize_results(
        ["UID0001", "UID0002"],
        {"UID0001": one, "UID0002": two},
        expected_full_uids=["UID0001", "UID0002"],
    )

    assert summary["cost"] == {
        "method": "estimated_from_reported_tokens",
        "currency": "USD",
        "amount": "0.003",
        "price_label": "public-2026-08-30",
        "priced_result_count": 2,
    }
    two["cost"]["price_label"] = "different-public-snapshot"
    with pytest.raises(OfficeQAValidationError, match="mixed price labels"):
        summarize_results(
            ["UID0001", "UID0002"],
            {"UID0001": one, "UID0002": two},
            expected_full_uids=["UID0001", "UID0002"],
        )


def test_cli_recomputes_scores_and_writes_deterministic_full_summary(tmp_path, capsys):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    scorer = tmp_path / "reward.py"
    scorer.write_text(
        "def score_answer(ground_truth, predicted, tolerance=0.0):\n"
        "    return float(ground_truth == predicted)\n",
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scorer"]["sha256"] = sha256_file(scorer)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    results_path = tmp_path / "results.jsonl"
    _write_results(results_path, [
        _result("UID0001"),
        _result(
            "UID0002",
            difficulty="easy",
            status="fail",
            score=0.0,
            prediction="19",
        ),
    ])
    output = tmp_path / "summary.json"

    assert main([
        "--csv", str(csv_path),
        "--corpus-dir", str(corpus),
        "--manifest", str(manifest_path),
        "--scorer", str(scorer),
        "--results", str(results_path),
        "--output", str(output),
    ]) == 0

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["official_full_accuracy"] == 0.5
    assert summary["is_cleanly_scorable"] is True
    assert summary["selection"]["uids"] == ["UID0001", "UID0002"]
    assert len(summary["selection"]["sha256"]) == 64
    assert summary["requests"] == {
        "attempts": 2,
        "retries": 0,
        "successful": 2,
        "usage_unavailable": 0,
    }
    assert summary["cost"] == {
        "method": "unavailable",
        "currency": None,
        "amount": None,
        "price_label": None,
        "priced_result_count": 0,
    }
    assert summary["results_sha256"] == sha256_file(results_path)
    assert json.loads(capsys.readouterr().out)["official_full_accuracy"] == 0.5


def test_cli_refuses_to_overwrite_an_input(tmp_path):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    scorer = tmp_path / "reward.py"
    scorer.write_text(
        "def score_answer(ground_truth, predicted, tolerance=0.0):\n"
        "    return float(ground_truth == predicted)\n",
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scorer"]["sha256"] = sha256_file(scorer)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    results_path = tmp_path / "results.jsonl"
    _write_results(results_path, [_result("UID0001")])

    with pytest.raises(OfficeQAValidationError, match="must not overwrite"):
        main([
            "--csv", str(csv_path),
            "--corpus-dir", str(corpus),
            "--manifest", str(manifest_path),
            "--scorer", str(scorer),
            "--results", str(results_path),
            "--output", str(results_path),
            "--uid", "UID0001",
        ])
