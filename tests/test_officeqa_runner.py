"""Offline contracts for the model-neutral OfficeQA Full runner."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bench.officeqa import run as officeqa_run
from scripts.bench.officeqa.evaluate import (
    CORPUS_TREE_HASH_FORMAT,
    OfficeQACase,
    OfficeQAValidationError,
    load_results,
    sha256_corpus_tree,
    sha256_file,
)
from scripts.bench.officeqa.run import (
    CaseUsageLedger,
    GenerationSettings,
    PricingSettings,
    ToolRecorder,
    _assistant_payload,
    _case_tools,
    _completion,
    _cost_record,
    _decimal_expression,
    _repair_truncated_jsonl_tail,
    _run_tool_loop,
    _safe_error_message,
)
from xskill.agents.agent_tools import create_agent_tool_context, use_agent_tool_context


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
            "sha256": "0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f",
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


def _settings(**changes) -> GenerationSettings:
    settings = GenerationSettings(
        max_output_tokens=128,
        final_output_tokens=512,
        inter_request_delay_seconds=0.0,
        temperature=0.0,
        top_p=1.0,
        top_k=None,
        presence_penalty=0.0,
        seed=0,
        enable_thinking=None,
        preserve_thinking=None,
        thinking_type=None,
        final_thinking_type=None,
        preserve_reasoning_content=False,
        send_tool_choice=True,
        reasoning_effort=None,
        final_reasoning_effort=None,
        request_retries=0,
        request_retry_backoff_seconds=0.0,
        max_retry_after_seconds=120.0,
    )
    return replace(settings, **changes)


def _result(
    case: OfficeQACase,
    *,
    status: str = "pass",
    prediction: str | None = None,
    error_type: str | None = None,
) -> dict:
    is_pass = status == "pass"
    return {
        "schema_version": 1,
        "uid": case.uid,
        "difficulty": case.difficulty,
        "status": status,
        "score": 1.0 if is_pass else 0.0,
        "prediction": case.answer if prediction is None and is_pass else prediction or "",
        "usage": {
            "request_attempts": 1,
            "request_retries": 0,
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "cache_read_tokens": 5,
            "cache_miss_tokens": 5,
            "unavailable_calls": 0,
        },
        "latency_seconds": 0.25,
        "tool_calls": [],
        "cost": {
            "method": "unavailable",
            "currency": None,
            "amount": None,
            "price_label": None,
        },
        "error_type": error_type,
        "error_message": "HTTP 429" if error_type else None,
        "error_code": 429 if error_type else None,
        "completed_at": "2026-08-30T00:00:00+00:00",
    }


def _provenance() -> dict:
    return {
        "xskill_revision": "a" * 40,
        "source_checkout_matches_runtime": True,
        "worktree_dirty": False,
        "python_version": "3.11.0",
        "package_versions": {
            "xskill": "test",
            "agno": "test",
            "openai": "test",
            "pydantic": "test",
        },
    }


def _response(message):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        prompt_tokens_details=SimpleNamespace(cached_tokens=5),
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("(12.5 - 2.5) / 4", "2.5"),
        ("100 * (1 + 5 / 100)", "105"),
        ("2 ** 10", "1024"),
    ],
)
def test_calculator_supports_only_bounded_arithmetic(expression, expected):
    assert _decimal_expression(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('id')", "value + 1", "2 ** 100", "[1, 2]"],
)
def test_calculator_rejects_code_and_unbounded_operations(expression):
    with pytest.raises(ValueError):
        _decimal_expression(expression)


def test_submit_answer_is_single_use():
    answer_box: dict[str, str] = {}
    tools = _case_tools(ToolRecorder(), answer_box)
    submit = next(candidate for candidate in tools if candidate.name == "submit_answer")
    assert submit.entrypoint("42") == "answer accepted"
    assert submit.entrypoint("43") == "error: an answer was already submitted"
    assert answer_box == {"answer": "42"}


def test_assistant_payload_preserves_required_reasoning_content():
    call = SimpleNamespace(
        id="call-search",
        function=SimpleNamespace(name="grep_files", arguments='{"pattern":"needle"}'),
    )
    message = SimpleNamespace(content=None, reasoning_content="", tool_calls=[call])
    payload = _assistant_payload(message, require_reasoning_content=True)
    assert payload["content"] == ""
    assert payload["reasoning_content"] == ""


def test_tool_recorder_executes_an_identical_research_call_only_once():
    executions: list[str] = []

    def search(pattern: str) -> str:
        executions.append(pattern)
        return "large corpus result"

    recorder = ToolRecorder()
    assert recorder.call("grep_files", search, "revenue") == "large corpus result"
    assert recorder.call("grep_files", search, "revenue").startswith("error: duplicate")
    assert executions == ["revenue"]
    assert recorder.calls[-1]["duplicate"] is True


def test_calculator_duplicate_key_includes_the_expression():
    recorder = ToolRecorder()
    calculator = next(
        candidate
        for candidate in _case_tools(recorder, {})
        if candidate.name == "calculate"
    )

    assert calculator.entrypoint("1 + 1") == "2"
    assert calculator.entrypoint("2 + 2") == "4"
    assert calculator.entrypoint("1 + 1").startswith("error: duplicate")
    assert [call["duplicate"] for call in recorder.calls] == [False, False, True]


def test_usage_ledger_spaces_provider_requests(monkeypatch):
    monotonic_values = iter((100.0, 102.0, 107.0))
    sleeps: list[float] = []
    monkeypatch.setattr(officeqa_run.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(officeqa_run.time, "sleep", sleeps.append)
    usage = CaseUsageLedger()
    usage.wait_before_request(7.0)
    usage.wait_before_request(7.0)
    assert sleeps == [5.0]


def test_cost_estimate_distinguishes_cached_input_from_cache_miss():
    cost = _cost_record(
        {
            "cache_miss_tokens": 1000,
            "cache_read_tokens": 9000,
            "output_tokens": 500,
        },
        PricingSettings(
            label="public-price-2026-08-30",
            input_per_million=Decimal("1"),
            cached_input_per_million=Decimal("0.1"),
            output_per_million=Decimal("2"),
        ),
    )

    assert cost == {
        "method": "estimated_from_reported_tokens",
        "currency": "USD",
        "amount": "0.0029",
        "price_label": "public-price-2026-08-30",
    }


def test_cost_is_unavailable_when_any_successful_call_omits_usage():
    usage = CaseUsageLedger()
    usage.record_request_attempt()
    usage.record_llm(
        "officeqa",
        "fake-model",
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10)),
    )
    snapshot = usage.snapshot()

    assert snapshot["calls"] == 1
    assert snapshot["unavailable_calls"] == 1
    assert _cost_record(
        snapshot,
        PricingSettings(
            label="public-price-2026-08-30",
            input_per_million=Decimal("1"),
            cached_input_per_million=Decimal("0.1"),
            output_per_million=Decimal("2"),
        ),
    ) == {
        "method": "unavailable",
        "currency": None,
        "amount": None,
        "price_label": None,
    }


def test_case_grep_tool_keeps_the_current_xskill_context_contract(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = corpus / "document.txt"
    document.write_text("first line\nunique benchmark needle\nlast line\n", encoding="utf-8")
    recorder = ToolRecorder()
    grep_tool = next(
        candidate
        for candidate in _case_tools(recorder, {})
        if candidate.name == "grep_files"
    )
    context = create_agent_tool_context(
        extra_read_roots=(corpus,),
        spill_root=tmp_path / "spill",
    )
    with use_agent_tool_context(context):
        result = grep_tool.entrypoint(
            pattern="unique benchmark needle",
            path=str(corpus),
            glob="*.txt",
            max_results=5,
            before=1,
            after=1,
        )
    assert "first line" in result
    assert "unique benchmark needle" in result
    assert "last line" in result
    assert recorder.calls[-1]["success"] is True
    assert set(grep_tool.to_dict()["parameters"]["properties"]) == {
        "pattern", "path", "glob", "max_results", "before", "after",
    }


def test_tool_loop_uses_separate_research_and_final_reasoning_settings(tmp_path):
    research = SimpleNamespace(
        content="I still need to summarize.\nNo direct answer yet.",
        reasoning_content="research trace",
        tool_calls=None,
    )
    invalid_submit = SimpleNamespace(
        content=None,
        reasoning_content=None,
        tool_calls=[SimpleNamespace(
            id="call-invalid-submit",
            function=SimpleNamespace(name="submit_answer", arguments='{"answer":""}'),
        )],
    )
    direct = SimpleNamespace(content="42", tool_calls=None)

    class Completions:
        def __init__(self):
            self.requests = []
            self.responses = iter(map(_response, (research, invalid_submit, direct)))

        def create(self, **kwargs):
            self.requests.append(json.loads(json.dumps(kwargs)))
            return next(self.responses)

    completions = Completions()
    answer_box: dict[str, str] = {}
    usage = CaseUsageLedger()
    _run_tool_loop(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        case=OfficeQACase("UID0001", "Synthetic?", "42", "easy", ("doc.txt",)),
        corpus_dir=tmp_path,
        recorder=ToolRecorder(),
        answer_box=answer_box,
        usage=usage,
        model="fake-model",
        max_rounds=3,
        settings=_settings(
            thinking_type="enabled",
            final_thinking_type="disabled",
            preserve_reasoning_content=True,
            send_tool_choice=False,
            reasoning_effort="high",
            final_reasoning_effort="low",
        ),
    )
    assert answer_box == {"answer": "42"}
    assert len(completions.requests) == 3
    assert all("tool_choice" not in request for request in completions.requests)
    assert completions.requests[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert completions.requests[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert completions.requests[2]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert completions.requests[1]["messages"][-2]["reasoning_content"] == "research trace"
    assert completions.requests[1]["reasoning_effort"] == "low"
    assert completions.requests[-1]["max_tokens"] == 512
    assert "tools" not in completions.requests[-1]
    assert usage.snapshot()["request_attempts"] == 3


def test_completion_retries_the_same_request_and_honors_retry_after(monkeypatch):
    class RateLimited(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "3"})

    message = SimpleNamespace(content="42", tool_calls=None)

    class Completions:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                raise RateLimited("slow down")
            return _response(message)

    completions = Completions()
    sleeps: list[float] = []
    monkeypatch.setattr(officeqa_run.time, "sleep", sleeps.append)
    usage = CaseUsageLedger()
    result = _completion(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="fake-model",
        messages=[{"role": "user", "content": "question"}],
        tools=[],
        settings=_settings(
            request_retries=1,
            request_retry_backoff_seconds=2.0,
            max_retry_after_seconds=10.0,
        ),
        usage=usage,
        tool_choice=None,
    )
    assert result.content == "42"
    assert completions.requests[0] == completions.requests[1]
    assert sleeps == [3.0]
    assert usage.snapshot()["request_attempts"] == 2
    assert usage.snapshot()["request_retries"] == 1
    assert usage.snapshot()["calls"] == 1


def test_persisted_request_error_does_not_include_private_endpoint_details():
    class FailedRequest(RuntimeError):
        status_code = 503

    error = FailedRequest("POST https://private.invalid/v1 with secret request content")

    assert _safe_error_message(error, "infra_error") == "HTTP 503"


def test_runtime_provenance_rejects_a_different_xskill_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(officeqa_run.xskill, "__file__", str(tmp_path / "xskill" / "__init__.py"))
    with pytest.raises(OfficeQAValidationError, match="does not come from this runner"):
        officeqa_run._runtime_provenance()


def test_jsonl_tail_recovery_preserves_complete_records(tmp_path):
    first = _result(OfficeQACase("UID0001", "Q?", "10", "hard", ("doc.txt",)))
    path = tmp_path / "results.jsonl"
    path.write_bytes(json.dumps(first).encode("utf-8") + b"\n{\"uid\":")
    assert _repair_truncated_jsonl_tail(path) is True
    assert load_results(path) == {"UID0001": first}

    complete_without_newline = tmp_path / "complete.jsonl"
    complete_without_newline.write_text(json.dumps(first), encoding="utf-8")
    assert _repair_truncated_jsonl_tail(complete_without_newline) is True
    assert complete_without_newline.read_bytes().endswith(b"\n")
    assert load_results(complete_without_newline) == {"UID0001": first}


def test_main_writes_reproducible_private_results_and_resumes(tmp_path, monkeypatch):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    output_dir = tmp_path / "local-output"
    executed: list[str] = []

    def fake_run_case(case, **_kwargs):
        executed.append(case.uid)
        return _result(case)

    monkeypatch.setattr(officeqa_run, "run_case", fake_run_case)
    monkeypatch.setattr(officeqa_run, "_runtime_provenance", _provenance)
    monkeypatch.setenv("OFFICEQA_TEST_KEY", "secret-never-persisted")
    arguments = [
        "--csv", str(csv_path),
        "--corpus-dir", str(corpus),
        "--manifest", str(manifest_path),
        "--output-dir", str(output_dir),
        "--base-url", "https://private-endpoint.invalid/v1",
        "--api-key-env", "OFFICEQA_TEST_KEY",
        "--model", "synthetic-model",
        "--context-window", "4096",
        "--final-output-tokens", "768",
        "--final-reasoning-effort", "low",
        "--seed", "7",
    ]
    assert officeqa_run.main(arguments) == 0
    assert executed == ["UID0001", "UID0002"]
    monkeypatch.delenv("OFFICEQA_TEST_KEY")
    assert officeqa_run.main(arguments) == 0
    assert executed == ["UID0001", "UID0002"]
    (output_dir / "results.jsonl").unlink()
    assert officeqa_run.main(arguments) == 0
    assert executed == ["UID0001", "UID0002"]

    run_config = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            output_dir / "run.json",
            output_dir / "summary.json",
            output_dir / "attempts.jsonl",
        )
    )
    assert run_config["xskill_revision"] == "a" * 40
    assert run_config["source_checkout_matches_runtime"] is True
    assert run_config["worktree_dirty"] is False
    assert run_config["seed"] == 7
    assert run_config["declared_context_window"] == 4096
    assert run_config["corpus_tree_sha256"]
    assert run_config["corpus_file_count"] == 3
    assert run_config["enable_thinking"] is None
    assert run_config["preserve_thinking"] is None
    assert run_config["thinking_type"] is None
    assert run_config["request_retries"] == 2
    assert run_config["final_submission"] == {
        "tool_choice": "submit_answer",
        "direct_answer_fallback": "one_no_tool_request",
        "max_output_tokens": 768,
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": None,
        "presence_penalty": 0.0,
        "enable_thinking": None,
        "preserve_thinking": None,
        "thinking_type": None,
        "reasoning_effort": "low",
    }
    assert "private-endpoint" not in persisted
    assert "secret-never-persisted" not in persisted
    assert summary["official_full_accuracy"] == 1.0
    assert summary["results_sha256"] == sha256_file(output_dir / "results.jsonl")
    results = load_results(output_dir / "results.jsonl")
    assert all(result["attempt_count"] == 1 for result in results.values())


def test_main_retries_transient_cases_and_accounts_all_attempts(tmp_path, monkeypatch):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    output_dir = tmp_path / "retry-output"
    statuses = iter(("infra_error", "pass", "pass"))
    executed: list[str] = []
    sleeps: list[float] = []

    def fake_run_case(case, **_kwargs):
        status = next(statuses)
        executed.append(case.uid)
        return _result(
            case,
            status=status,
            error_type="RateLimitError" if status == "infra_error" else None,
        )

    monkeypatch.setattr(officeqa_run, "run_case", fake_run_case)
    monkeypatch.setattr(officeqa_run, "_runtime_provenance", _provenance)
    monkeypatch.setattr(officeqa_run.time, "sleep", sleeps.append)
    monkeypatch.setenv("OFFICEQA_TEST_KEY", "retry-secret")
    assert officeqa_run.main([
        "--csv", str(csv_path),
        "--corpus-dir", str(corpus),
        "--manifest", str(manifest_path),
        "--output-dir", str(output_dir),
        "--base-url", "https://private-endpoint.invalid/v1",
        "--api-key-env", "OFFICEQA_TEST_KEY",
        "--model", "synthetic-model",
        "--context-window", "4096",
        "--case-retries", "1",
        "--retry-backoff-seconds", "2",
    ]) == 0
    assert executed == ["UID0001", "UID0001", "UID0002"]
    assert sleeps == [2.0]
    results = load_results(output_dir / "results.jsonl")
    assert results["UID0001"]["attempt_count"] == 2
    assert results["UID0001"]["attempt_statuses"] == ["infra_error", "pass"]
    assert results["UID0001"]["usage"]["total_tokens"] == 24
    assert results["UID0001"]["usage"]["request_attempts"] == 2
    assert results["UID0001"]["retry_wait_seconds"] == 2.0


def test_main_fails_fast_on_nonscorable_results_unless_diagnostic_mode(tmp_path, monkeypatch):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    monkeypatch.setattr(officeqa_run, "_runtime_provenance", _provenance)
    monkeypatch.setenv("OFFICEQA_TEST_KEY", "diagnostic-secret")
    executed: list[str] = []

    def invalid_first(case, **_kwargs):
        executed.append(case.uid)
        return _result(case, status="invalid")

    monkeypatch.setattr(officeqa_run, "run_case", invalid_first)
    common = [
        "--csv", str(csv_path),
        "--corpus-dir", str(corpus),
        "--manifest", str(manifest_path),
        "--base-url", "https://private-endpoint.invalid/v1",
        "--api-key-env", "OFFICEQA_TEST_KEY",
        "--model", "synthetic-model",
        "--context-window", "4096",
    ]
    stopped_output = tmp_path / "stopped"
    assert officeqa_run.main([*common, "--output-dir", str(stopped_output)]) == 4
    assert executed == ["UID0001"]
    stopped_summary = json.loads(
        (stopped_output / "summary.json").read_text(encoding="utf-8")
    )
    assert stopped_summary["halted_uid"] == "UID0001"
    assert stopped_summary["official_full_accuracy"] is None

    executed.clear()
    diagnostic_output = tmp_path / "diagnostic"
    assert officeqa_run.main([
        *common,
        "--output-dir", str(diagnostic_output),
        "--continue-on-nonscorable",
    ]) == 0
    assert executed == ["UID0001", "UID0002"]
    diagnostic_summary = json.loads(
        (diagnostic_output / "summary.json").read_text(encoding="utf-8")
    )
    assert diagnostic_summary["is_complete"] is True
    assert diagnostic_summary["is_cleanly_scorable"] is False
    assert diagnostic_summary["official_full_accuracy"] is None


def test_main_rejects_output_directory_inside_search_corpus(tmp_path, monkeypatch):
    csv_path, manifest_path, corpus = _fixture(tmp_path)
    monkeypatch.setattr(officeqa_run, "_runtime_provenance", _provenance)
    monkeypatch.setenv("OFFICEQA_TEST_KEY", "never-used")

    with pytest.raises(OfficeQAValidationError, match="inside the search corpus"):
        officeqa_run.main([
            "--csv", str(csv_path),
            "--corpus-dir", str(corpus),
            "--manifest", str(manifest_path),
            "--output-dir", str(corpus / "run-output"),
            "--base-url", "https://private-endpoint.invalid/v1",
            "--api-key-env", "OFFICEQA_TEST_KEY",
            "--model", "synthetic-model",
            "--context-window", "4096",
        ])


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--request-timeout", "nan"], "request-timeout must be finite"),
        (["--inter-request-delay-seconds", "inf"], "inter-request-delay-seconds"),
        (["--request-retry-backoff-seconds", "nan"], "retry delays must be finite"),
        (["--model", "bad\nidentifier"], "one non-empty public identifier"),
    ],
)
def test_main_rejects_nonfinite_timing_and_unsafe_model_metadata(tmp_path, extra, message):
    with pytest.raises(OfficeQAValidationError, match=message):
        officeqa_run.main([
            "--csv", str(tmp_path / "missing.csv"),
            "--corpus-dir", str(tmp_path / "missing-corpus"),
            "--manifest", str(tmp_path / "missing-manifest.json"),
            "--output-dir", str(tmp_path / "output"),
            "--base-url", "https://private-endpoint.invalid/v1",
            "--model", "synthetic-model",
            *extra,
        ])
