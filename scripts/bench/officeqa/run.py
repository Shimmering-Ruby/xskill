"""Run OfficeQA Full through a bounded xskill read-only tool harness."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from agno.tools import tool
from openai import OpenAI

import xskill

from scripts.bench.officeqa.evaluate import (
    VENDORED_SCORER,
    OfficeQACase,
    OfficeQAValidationError,
    load_cases,
    load_results,
    load_scorer,
    sha256_file,
    summarize_results,
    validate_result_record,
    verify_scores,
)
from xskill.agents.agent_tools import (
    create_agent_tool_context,
    use_agent_tool_context,
)
from xskill.agents.agent_tools import (
    grep_files as xskill_grep_files,
)
from xskill.agents.agent_tools import (
    read_file as xskill_read_file,
)
from xskill.usage import extract_usage

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
HARNESS_ID = "xskill-openai-compatible-tools-officeqa-v1"
PROMPT = """You are evaluating OfficeQA Full against the complete U.S. Treasury Bulletin transformed-text corpus.
Find the answer yourself using only the read-only corpus tools and the calculator; no source filename or oracle page is supplied.
Start with targeted grep_files queries, but use at most 8 searches; repeated broad searches are a failure mode.
Once a likely table is found, switch to read_file with a narrow line window, calculate if needed, and submit the best supported answer before the tool budget expires.
Check units, signs, fiscal periods, table headers, and whether the question asks for a value, difference, ratio, percentage, date, or ordered list.
Do not guess from memory and do not use the internet.
When you have verified the result, call submit_answer exactly once with only the direct answer value in the same shape requested by the question, without explanation, citation, XML, or Markdown.
"""


@dataclass(frozen=True)
class GenerationSettings:
    """Exact generation controls included in the run fingerprint."""

    max_output_tokens: int
    final_output_tokens: int
    inter_request_delay_seconds: float
    temperature: float
    top_p: float
    top_k: int | None
    presence_penalty: float
    seed: int
    enable_thinking: bool | None
    preserve_thinking: bool | None
    thinking_type: str | None
    final_thinking_type: str | None
    preserve_reasoning_content: bool
    send_tool_choice: bool
    reasoning_effort: str | None
    final_reasoning_effort: str | None
    request_retries: int
    request_retry_backoff_seconds: float
    max_retry_after_seconds: float


@dataclass(frozen=True)
class PricingSettings:
    """Optional public USD price snapshot used only for deterministic estimates."""

    label: str
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cost_record(
    usage: dict[str, int],
    pricing: PricingSettings | None,
) -> dict[str, str | None]:
    if pricing is None or usage.get("unavailable_calls", 0):
        return {
            "method": "unavailable",
            "currency": None,
            "amount": None,
            "price_label": None,
        }
    with localcontext() as context:
        context.prec = 28
        amount = (
            Decimal(usage["cache_miss_tokens"]) * pricing.input_per_million
            + Decimal(usage["cache_read_tokens"]) * pricing.cached_input_per_million
            + Decimal(usage["output_tokens"]) * pricing.output_per_million
        ) / Decimal(1_000_000)
    return {
        "method": "estimated_from_reported_tokens",
        "currency": "USD",
        "amount": format(amount, "f"),
        "price_label": pricing.label,
    }


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPOSITORY_ROOT), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise OfficeQAValidationError(
            f"cannot inspect the xskill source checkout: {error}"
        ) from error


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _runtime_provenance() -> dict[str, Any]:
    """Fail unless imports and Git metadata identify one clean source checkout."""
    expected_package = (SOURCE_ROOT / "xskill").resolve()
    actual_package = Path(xskill.__file__).resolve().parent
    try:
        actual_package.relative_to(expected_package)
    except ValueError as error:
        raise OfficeQAValidationError(
            "runtime xskill import does not come from this runner's source checkout"
        ) from error
    if Path(_git_output("rev-parse", "--show-toplevel")).resolve() != REPOSITORY_ROOT:
        raise OfficeQAValidationError("runner is not inside the recorded xskill Git checkout")
    dirty_entries = _git_output("status", "--porcelain", "--untracked-files=all")
    if dirty_entries:
        raise OfficeQAValidationError(
            "xskill worktree is dirty; commit or remove local changes before an evaluation run"
        )
    return {
        "xskill_revision": _git_output("rev-parse", "HEAD"),
        "source_checkout_matches_runtime": True,
        "worktree_dirty": False,
        "python_version": sys.version.split()[0],
        "package_versions": {
            name: _package_version(name)
            for name in ("xskill", "agno", "openai", "pydantic")
        },
    }


class CaseUsageLedger:
    """Collect measured per-request usage without touching xskill's Registry."""

    def __init__(self) -> None:
        self.request_attempts = 0
        self.request_retries = 0
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cache_read_tokens = 0
        self.unavailable_calls = 0
        self._last_request_started: float | None = None

    def wait_before_request(self, minimum_interval_seconds: float) -> None:
        """Space provider request starts to stay within endpoint rate limits."""
        now = time.monotonic()
        if self._last_request_started is not None:
            remaining = minimum_interval_seconds - (now - self._last_request_started)
            if remaining > 0:
                time.sleep(remaining)
                now = time.monotonic()
        self._last_request_started = now

    def record_llm(self, step: str, model: str, response: Any) -> None:
        del step, model
        usage = extract_usage(response)
        raw_usage = getattr(response, "usage", None)
        prompt_details = getattr(raw_usage, "prompt_tokens_details", None)
        detailed_cache_read = getattr(prompt_details, "cached_tokens", None)
        reported_cache_read = (
            detailed_cache_read
            if isinstance(detailed_cache_read, int) and detailed_cache_read >= 0
            else usage.cache_hit or 0
        )
        complete = all(
            isinstance(value, int)
            for value in (usage.prompt, usage.completion, usage.total)
        )
        prompt_tokens = usage.prompt if isinstance(usage.prompt, int) else 0
        if reported_cache_read > prompt_tokens:
            complete = False
        self.calls += 1
        if usage.measurement_quality == "unavailable" or not complete:
            self.unavailable_calls += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += usage.completion or 0
        self.total_tokens += usage.total or 0
        self.cache_read_tokens += min(reported_cache_read, prompt_tokens)

    def record_request_attempt(self) -> None:
        self.request_attempts += 1

    def record_request_retry(self) -> None:
        self.request_retries += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "request_attempts": self.request_attempts,
            "request_retries": self.request_retries,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_miss_tokens": max(0, self.input_tokens - self.cache_read_tokens),
            "unavailable_calls": self.unavailable_calls,
        }


class ToolRecorder:
    """Record tool names, latency, and success without persisting corpus content."""

    def __init__(self, max_research_calls: int = 18) -> None:
        self.calls: list[dict[str, Any]] = []
        self.max_research_calls = max_research_calls
        self.limits = {"grep_files": 8, "read_file": 8, "calculate": 3}
        self._seen_invocations: set[str] = set()

    def call(self, name: str, function, *args, **kwargs) -> str:
        used = sum(call["name"] == name for call in self.calls)
        limit = self.limits.get(name)
        research_used = sum(call["name"] != "submit_answer" for call in self.calls)
        budget_exhausted = (
            research_used >= self.max_research_calls
            or (limit is not None and used >= limit)
        )
        if budget_exhausted:
            result = f"error: {name} budget exhausted; use existing evidence and submit_answer"
            self.calls.append({
                "name": name,
                "success": False,
                "latency_seconds": 0.0,
                "original_chars": len(result),
                "returned_chars": len(result),
                "budget_exhausted": True,
                "duplicate": False,
            })
            print(f"  tool[{len(self.calls)}] {name} budget_exhausted", file=sys.stderr, flush=True)
            return result
        invocation = _canonical_sha256({"name": name, "args": args, "kwargs": kwargs})
        if invocation in self._seen_invocations:
            if name == "grep_files":
                result = (
                    "error: duplicate grep_files call; use a file path from the existing "
                    "result with read_file, or submit the best supported answer"
                )
            else:
                result = f"error: duplicate {name} call; use the existing result or change the input"
            self.calls.append({
                "name": name,
                "success": False,
                "latency_seconds": 0.0,
                "original_chars": len(result),
                "returned_chars": len(result),
                "budget_exhausted": False,
                "duplicate": True,
            })
            print(f"  tool[{len(self.calls)}] {name} duplicate", file=sys.stderr, flush=True)
            return result
        self._seen_invocations.add(invocation)
        started = time.monotonic()
        try:
            full_result = str(function(*args, **kwargs))
            success = not full_result.startswith("error:")
            if len(full_result) > 8000:
                result = (
                    full_result[:8000]
                    + "\n... tool result truncated at 8000 characters; narrow the query or read a smaller window"
                )
            else:
                result = full_result
            return result
        finally:
            self.calls.append({
                "name": name,
                "success": locals().get("success", False),
                "latency_seconds": round(time.monotonic() - started, 6),
                "original_chars": len(locals().get("full_result", "")),
                "returned_chars": len(locals().get("result", "")),
                "budget_exhausted": False,
                "duplicate": False,
            })
            print(
                f"  tool[{len(self.calls)}] {name} "
                f"{'ok' if locals().get('success', False) else 'error'} "
                f"chars={len(locals().get('result', ''))}",
                file=sys.stderr,
                flush=True,
            )


def _decimal_expression(expression: str) -> str:
    """Evaluate bounded arithmetic without names, attributes, calls, or code execution."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must not be empty")
    if len(expression) > 256:
        raise ValueError("expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"invalid arithmetic expression: {error.msg}") from error

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(  # noqa: TRY004 - invalid expression syntax, not an API type error
                    "only numeric literals are allowed"
                )
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > 12:
                    raise ValueError("power must be an integer between -12 and 12")
                return left ** int(right)
        raise ValueError(f"unsupported arithmetic syntax: {type(node).__name__}")

    try:
        with localcontext() as context:
            context.prec = 50
            value = visit(tree)
    except (InvalidOperation, ZeroDivisionError) as error:
        raise ValueError(f"arithmetic failed: {error}") from error
    if not value.is_finite() or value.adjusted() > 100:
        raise ValueError("arithmetic result is outside the allowed range")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _case_tools(recorder: ToolRecorder, answer_box: dict[str, str]):
    @tool(name="grep_files")
    def grep_files(
        pattern: str,
        path: str = "",
        glob: str = "*.txt",
        max_results: int = 15,
        before: int = 2,
        after: int = 2,
    ) -> str:
        """Search the OfficeQA corpus with a regular expression.

        Args:
            pattern: ripgrep-compatible regular expression.
            path: Absolute corpus directory or file path.
            glob: File-name glob, normally ``*.txt``.
            max_results: Maximum matching lines, from 1 through 500.
            before: Context lines before each match, from 0 through 10.
            after: Context lines after each match, from 0 through 10.
        """
        return recorder.call(
            "grep_files",
            xskill_grep_files.entrypoint,
            pattern=pattern,
            path=path,
            glob=glob,
            max_results=max(1, min(int(max_results), 20)),
            before=max(0, min(int(before), 10)),
            after=max(0, min(int(after), 10)),
        )

    @tool(name="read_file")
    def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
        """Read a line window from one OfficeQA corpus text file.

        Args:
            path: Absolute file path returned by grep_files.
            offset: One-based first line.
            limit: Number of lines to return.
        """
        return recorder.call(
            "read_file",
            xskill_read_file.entrypoint,
            path=path,
            offset=offset,
            limit=max(1, min(int(limit), 50)),
        )

    @tool(name="calculate")
    def calculate(expression: str) -> str:
        """Evaluate bounded arithmetic using numbers, parentheses, and + - * / // % **.

        Args:
            expression: Arithmetic expression without variables or function calls.
        """
        def evaluate(value: str) -> str:
            try:
                return _decimal_expression(value)
            except ValueError as error:
                return f"error: {error}"

        return recorder.call("calculate", evaluate, expression)

    @tool(name="submit_answer")
    def submit_answer(answer: str) -> str:
        """Submit the final direct answer exactly once.

        Args:
            answer: One non-empty line containing only the requested answer value.
        """
        value = str(answer or "").strip()
        success = bool(value) and len(value) <= 250 and len(value.splitlines()) == 1
        if "answer" in answer_box:
            success = False
            result = "error: an answer was already submitted"
        elif not success:
            result = "error: answer must be one non-empty line of at most 250 characters"
        else:
            answer_box["answer"] = value
            result = "answer accepted"
        recorder.calls.append({
            "name": "submit_answer",
            "success": success,
            "latency_seconds": 0.0,
            "original_chars": len(value),
            "returned_chars": len(result),
            "budget_exhausted": False,
            "duplicate": False,
        })
        print(
            f"  tool[{len(recorder.calls)}] submit_answer {'ok' if success else 'error'}",
            file=sys.stderr,
            flush=True,
        )
        return result

    return [grep_files, read_file, calculate, submit_answer]


def _assistant_payload(
    message: Any,
    *,
    require_reasoning_content: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is not None or require_reasoning_content:
        payload["reasoning_content"] = reasoning or ""
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _direct_answer(content: str | None) -> str:
    value = str(content or "").strip()
    if value and len(value) <= 250 and len(value.splitlines()) == 1:
        return value
    return ""


def _record_invalid_tool(recorder: ToolRecorder, name: str) -> None:
    recorder.calls.append({
        "name": name or "unknown",
        "success": False,
        "latency_seconds": 0.0,
        "original_chars": 0,
        "returned_chars": 0,
        "budget_exhausted": False,
        "duplicate": False,
    })


def _execute_tool_call(
    call: Any,
    by_name: dict[str, Any],
    recorder: ToolRecorder,
) -> str:
    name = str(call.function.name or "")
    function = by_name.get(name)
    if function is None:
        _record_invalid_tool(recorder, name)
        return f"error: unknown tool {name!r}"
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as error:
        _record_invalid_tool(recorder, name)
        return f"error: invalid JSON arguments for {name}: {error}"
    if not isinstance(arguments, dict):
        _record_invalid_tool(recorder, name)
        return f"error: arguments for {name} must be an object"
    try:
        return str(function.entrypoint(**arguments))
    except (TypeError, ValueError) as error:
        _record_invalid_tool(recorder, name)
        return f"error: invalid arguments for {name}: {error}"


def _retryable_request_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {408, 409, 429} or (
        isinstance(status_code, int) and 500 <= status_code < 600
    ):
        return True
    name = type(error).__name__
    return name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}


def _retry_after_seconds(error: Exception, maximum: float) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, maximum)


def _completion(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    settings: GenerationSettings,
    usage: CaseUsageLedger,
    tool_choice: str | dict[str, Any] | None = "auto",
    max_output_tokens: int | None = None,
):
    extra_body: dict[str, Any] = {}
    chat_template_kwargs = {}
    if settings.enable_thinking is not None:
        chat_template_kwargs["enable_thinking"] = settings.enable_thinking
    if settings.preserve_thinking is not None:
        chat_template_kwargs["preserve_thinking"] = settings.preserve_thinking
    if chat_template_kwargs:
        extra_body["chat_template_kwargs"] = chat_template_kwargs
    if settings.top_k is not None:
        extra_body["top_k"] = settings.top_k
    if settings.thinking_type is not None:
        extra_body["thinking"] = {"type": settings.thinking_type}
    request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "presence_penalty": settings.presence_penalty,
        "seed": settings.seed,
        "max_tokens": max_output_tokens or settings.max_output_tokens,
        "extra_body": extra_body,
    }
    if tools:
        request["tools"] = tools
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    if settings.reasoning_effort is not None:
        request["reasoning_effort"] = settings.reasoning_effort

    for attempt_index in range(settings.request_retries + 1):
        usage.wait_before_request(settings.inter_request_delay_seconds)
        usage.record_request_attempt()
        try:
            response = client.chat.completions.create(**request)
            break
        except Exception as error:
            if attempt_index >= settings.request_retries or not _retryable_request_error(error):
                raise
            retry_after = _retry_after_seconds(error, settings.max_retry_after_seconds)
            wait_seconds = settings.request_retry_backoff_seconds * (2 ** attempt_index)
            if retry_after is not None:
                wait_seconds = max(wait_seconds, retry_after)
            usage.record_request_retry()
            print(
                f"  request retry {attempt_index + 1}/{settings.request_retries} "
                f"after {type(error).__name__} in {wait_seconds:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(wait_seconds)
    usage.record_llm("officeqa", model, response)
    if not response.choices:
        raise RuntimeError("model response has no choices")
    return response.choices[0].message


def _final_submission_settings(settings: GenerationSettings) -> GenerationSettings:
    """Apply deterministic final controls independently from research settings."""
    return replace(
        settings,
        temperature=0.0,
        top_p=1.0,
        top_k=None,
        presence_penalty=0.0,
        enable_thinking=(False if settings.enable_thinking is not None else None),
        preserve_thinking=(False if settings.preserve_thinking is not None else None),
        thinking_type=settings.final_thinking_type,
        preserve_reasoning_content=False,
        reasoning_effort=settings.final_reasoning_effort,
    )


def _run_tool_loop(
    *,
    client: OpenAI,
    case: OfficeQACase,
    corpus_dir: Path,
    recorder: ToolRecorder,
    answer_box: dict[str, str],
    usage: CaseUsageLedger,
    model: str,
    max_rounds: int,
    settings: GenerationSettings,
) -> None:
    tool_objects = _case_tools(recorder, answer_box)
    by_name = {candidate.name: candidate for candidate in tool_objects}
    schemas = [
        {"type": "function", "function": candidate.to_dict()}
        for candidate in tool_objects
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": PROMPT + f"\nCORPUS_ROOT: {corpus_dir}\n"},
        {"role": "user", "content": case.question},
    ]
    for _round in range(max_rounds):
        message = _completion(
            client,
            model=model,
            messages=messages,
            tools=schemas,
            settings=settings,
            usage=usage,
            tool_choice="auto" if settings.send_tool_choice else None,
        )
        messages.append(_assistant_payload(
            message,
            require_reasoning_content=settings.preserve_reasoning_content,
        ))
        if not message.tool_calls:
            direct = _direct_answer(message.content)
            if direct:
                by_name["submit_answer"].entrypoint(direct)
            break
        for call in message.tool_calls:
            result = _execute_tool_call(call, by_name, recorder)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
            if answer_box:
                return

    if answer_box:
        return
    messages.append({
        "role": "user",
        "content": "Research is over. Submit the single best direct answer now; do not search again.",
    })
    submit_schema = next(
        schema for schema in schemas if schema["function"]["name"] == "submit_answer"
    )
    message = _completion(
        client,
        model=model,
        messages=messages,
        tools=[submit_schema],
        tool_choice=(
            {"type": "function", "function": {"name": "submit_answer"}}
            if settings.send_tool_choice
            else None
        ),
        settings=_final_submission_settings(settings),
        max_output_tokens=settings.final_output_tokens,
        usage=usage,
    )
    messages.append(_assistant_payload(
        message,
        require_reasoning_content=False,
    ))
    if message.tool_calls:
        for call in message.tool_calls:
            result = _execute_tool_call(call, by_name, recorder)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
            if answer_box:
                return
    direct = _direct_answer(message.content)
    if direct:
        by_name["submit_answer"].entrypoint(direct)
        return

    messages.append({
        "role": "user",
        "content": (
            "Return only the single best direct answer value on one line now. "
            "Do not explain, search, or call a tool."
        ),
    })
    message = _completion(
        client,
        model=model,
        messages=messages,
        tools=[],
        tool_choice=None,
        settings=_final_submission_settings(settings),
        max_output_tokens=settings.final_output_tokens,
        usage=usage,
    )
    direct = _direct_answer(message.content)
    if direct:
        by_name["submit_answer"].entrypoint(direct)


def _classify_exception(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}".lower()
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    return "infra_error"


def _error_code(error: Exception) -> int | None:
    code = getattr(error, "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
        return code
    return None


def _safe_error_message(error: Exception, status: str) -> str:
    """Return a diagnostic that cannot persist endpoint URLs or request content."""
    code = _error_code(error)
    if code is not None:
        return f"HTTP {code}"
    if status == "timeout":
        return "request timed out"
    return "request failed"


def run_case(
    case: OfficeQACase,
    *,
    corpus_dir: Path,
    spill_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    tool_call_limit: int,
    max_rounds: int,
    request_timeout: float,
    settings: GenerationSettings,
    pricing: PricingSettings | None,
    tolerance: float,
    score_answer,
) -> dict[str, Any]:
    """Run one isolated agent session and return a gated local result record."""
    started = time.monotonic()
    answer_box: dict[str, str] = {}
    recorder = ToolRecorder(max_research_calls=tool_call_limit)
    usage = CaseUsageLedger()
    case_spill = spill_dir / case.uid
    case_spill.mkdir(parents=True, exist_ok=True)
    context = create_agent_tool_context(
        extra_read_roots=(corpus_dir,),
        spill_root=case_spill,
    )
    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=request_timeout,
            max_retries=0,
        )
        with use_agent_tool_context(context):
            _run_tool_loop(
                client=client,
                case=case,
                corpus_dir=corpus_dir,
                recorder=recorder,
                answer_box=answer_box,
                usage=usage,
                model=model,
                max_rounds=max_rounds,
                settings=settings,
            )
        prediction = answer_box.get("answer", "")
        if not prediction:
            status = "invalid"
            score = 0.0
            error_type = None
            error_message = None
            error_code = None
        else:
            try:
                score = float(score_answer(case.answer, prediction, tolerance))
            except (TypeError, ValueError):
                status = "invalid"
                score = 0.0
                error_type = "ScorerInputError"
                error_message = "pinned scorer rejected prediction"
                error_code = None
            else:
                status = "pass" if score == 1.0 else "fail"
                error_type = None
                error_message = None
                error_code = None
    except Exception as error:  # noqa: BLE001 - case boundary records client/tool failures
        prediction = answer_box.get("answer", "")
        status = _classify_exception(error)
        score = 0.0
        error_type = type(error).__name__
        error_message = _safe_error_message(error, status)
        error_code = _error_code(error)

    usage_snapshot = usage.snapshot()
    return {
        "schema_version": 1,
        "uid": case.uid,
        "difficulty": case.difficulty,
        "status": status,
        "score": score,
        "prediction": prediction,
        "usage": usage_snapshot,
        "latency_seconds": round(time.monotonic() - started, 6),
        "tool_calls": recorder.calls,
        "cost": _cost_record(usage_snapshot, pricing),
        "error_type": error_type,
        "error_message": error_message,
        "error_code": error_code,
        "completed_at": _utc_now(),
    }


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _repair_truncated_jsonl_tail(path: Path) -> bool:
    """Drop only an incomplete final record left by an interrupted append."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("r+b") as handle:
        payload = handle.read()
        if payload.endswith(b"\n"):
            return False
        boundary = payload.rfind(b"\n") + 1
        tail = payload[boundary:]
        try:
            json.loads(tail)
        except (UnicodeDecodeError, json.JSONDecodeError):
            handle.seek(boundary)
            handle.truncate()
        else:
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(f"repaired interrupted JSONL tail: {path.name}", file=sys.stderr, flush=True)
    return True


def _append_result(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _is_retryable_result(result: dict[str, Any]) -> bool:
    if result.get("status") == "timeout":
        return True
    if result.get("status") != "infra_error":
        return False
    error_type = str(result.get("error_type") or "")
    error_code = result.get("error_code")
    return (
        error_type in {
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "RateLimitError",
        }
        or error_code in {408, 409, 429}
        or isinstance(error_code, int) and 500 <= error_code < 600
    )


def _attempt_record(
    result: dict[str, Any],
    attempt: int,
    retry_wait_before_seconds: float,
) -> dict[str, Any]:
    return {
        **result,
        "attempt": attempt,
        "retry_wait_before_seconds": round(retry_wait_before_seconds, 6),
    }


def _load_attempt_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return records
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            uid = str(record["uid"])
            attempt = int(record["attempt"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OfficeQAValidationError(
                f"invalid attempt ledger record at line {line_number}"
            ) from error
        validate_result_record(record, line_number=line_number)
        history = records.setdefault(uid, [])
        if attempt != len(history) + 1:
            raise OfficeQAValidationError(
                f"non-contiguous attempt number for {uid} at line {line_number}"
            )
        history.append(record)
    return records


def _merge_attempt_results(
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    result = dict(attempts[-1])
    usage_keys = (
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
    result["usage"] = {
        key: sum(int(attempt["usage"].get(key, 0)) for attempt in attempts)
        for key in usage_keys
    }
    costs = [attempt["cost"] for attempt in attempts]
    if all(cost["method"] == "estimated_from_reported_tokens" for cost in costs):
        labels = {cost["price_label"] for cost in costs}
        if len(labels) != 1:
            raise OfficeQAValidationError("attempt records use different price labels")
        result["cost"] = {
            "method": "estimated_from_reported_tokens",
            "currency": "USD",
            "amount": format(
                sum((Decimal(cost["amount"]) for cost in costs), Decimal(0)),
                "f",
            ),
            "price_label": costs[0]["price_label"],
        }
    else:
        result["cost"] = {
            "method": "unavailable",
            "currency": None,
            "amount": None,
            "price_label": None,
        }
    result["latency_seconds"] = round(
        sum(float(attempt["latency_seconds"]) for attempt in attempts),
        6,
    )
    result["tool_calls"] = [
        {**call, "attempt": attempt_index}
        for attempt_index, attempt in enumerate(attempts, 1)
        for call in attempt["tool_calls"]
    ]
    result["attempt_count"] = len(attempts)
    result["attempt_statuses"] = [attempt["status"] for attempt in attempts]
    result["retry_wait_seconds"] = round(
        sum(float(attempt.get("retry_wait_before_seconds", 0.0)) for attempt in attempts),
        6,
    )
    result.pop("attempt", None)
    result.pop("retry_wait_before_seconds", None)
    return result


def _select_cases(cases: list[OfficeQACase], args: argparse.Namespace) -> list[OfficeQACase]:
    selected = cases
    if args.difficulty:
        selected = [case for case in selected if case.difficulty == args.difficulty]
    if args.uid:
        requested = list(dict.fromkeys(args.uid))
        by_uid = {case.uid: case for case in selected}
        missing = [uid for uid in requested if uid not in by_uid]
        if missing:
            raise OfficeQAValidationError(f"requested UIDs are unavailable: {missing}")
        selected = [by_uid[uid] for uid in requested]
    if args.limit:
        selected = selected[: args.limit]
    if not selected:
        raise OfficeQAValidationError("case selection is empty")
    return selected


def _pricing_settings(args: argparse.Namespace) -> PricingSettings | None:
    raw_rates = (
        args.input_price_per_million_usd,
        args.cached_input_price_per_million_usd,
        args.output_price_per_million_usd,
    )
    if all(value is None for value in raw_rates) and args.price_label is None:
        return None
    if any(value is None for value in raw_rates) or args.price_label is None:
        raise OfficeQAValidationError(
            "price label and all three per-million USD rates must be provided together"
        )
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.price_label):
        raise OfficeQAValidationError("--price-label must be a short public identifier")
    try:
        rates = tuple(Decimal(value) for value in raw_rates)
    except InvalidOperation as error:
        raise OfficeQAValidationError("pricing rates must be decimal numbers") from error
    if any(not rate.is_finite() or rate < 0 for rate in rates):
        raise OfficeQAValidationError("pricing rates must be finite and non-negative")
    return PricingSettings(args.price_label, *rates)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run pinned OfficeQA Full evaluation")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, default=VENDORED_SCORER)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="OFFICEQA_API_KEY")
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint-label", default="local-openai-compatible")
    parser.add_argument("--price-label")
    parser.add_argument("--input-price-per-million-usd")
    parser.add_argument("--cached-input-price-per-million-usd")
    parser.add_argument("--output-price-per-million-usd")
    parser.add_argument("--uid", action="append")
    parser.add_argument("--difficulty", choices=("easy", "hard"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--context-window",
        type=int,
        help="context window configured on the model endpoint; recorded, not changed by this runner",
    )
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--final-output-tokens", type=int, default=512)
    parser.add_argument("--tool-call-limit", type=int, default=18)
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--inter-request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--request-retries", type=int, default=2)
    parser.add_argument("--request-retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--max-retry-after-seconds", type=float, default=120.0)
    parser.add_argument("--case-retries", type=int, default=0)
    parser.add_argument("--retry-backoff-seconds", type=float, default=30.0)
    parser.add_argument("--inter-case-delay-seconds", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--presence-penalty", type=float, default=0.0)
    parser.add_argument(
        "--enable-thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--preserve-thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--thinking-type", choices=("enabled", "disabled"))
    parser.add_argument("--final-thinking-type", choices=("enabled", "disabled"))
    parser.add_argument(
        "--preserve-reasoning-content",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--send-tool-choice",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument(
        "--final-reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument(
        "--continue-on-nonscorable",
        action="store_true",
        help="diagnostic mode only; continue after invalid or non-retryable failures",
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        raise OfficeQAValidationError("--limit must be >= 0")
    if args.case_retries < 0:
        raise OfficeQAValidationError("--case-retries must be >= 0")
    if args.request_retries < 0:
        raise OfficeQAValidationError("--request-retries must be >= 0")
    if any(not math.isfinite(value) or value < 0 for value in (
        args.request_retry_backoff_seconds,
        args.max_retry_after_seconds,
        args.retry_backoff_seconds,
    )):
        raise OfficeQAValidationError("request and case retry delays must be finite and >= 0")
    if (
        not math.isfinite(args.inter_case_delay_seconds)
        or args.inter_case_delay_seconds < 0
    ):
        raise OfficeQAValidationError("--inter-case-delay-seconds must be >= 0")
    if (
        not math.isfinite(args.inter_request_delay_seconds)
        or args.inter_request_delay_seconds < 0
    ):
        raise OfficeQAValidationError("--inter-request-delay-seconds must be >= 0")
    if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
        raise OfficeQAValidationError("--request-timeout must be finite and positive")
    if any(value <= 0 for value in (
        args.max_output_tokens,
        args.final_output_tokens,
        args.tool_call_limit,
        args.max_rounds,
    )):
        raise OfficeQAValidationError(
            "research output, final output, tool-call, and round limits must be positive"
        )
    if args.context_window is not None and args.context_window <= 0:
        raise OfficeQAValidationError("--context-window must be positive")
    if not 0 <= args.temperature <= 2:
        raise OfficeQAValidationError("--temperature must be between 0 and 2")
    if not 0 < args.top_p <= 1:
        raise OfficeQAValidationError("--top-p must be greater than 0 and at most 1")
    if args.top_k is not None and args.top_k <= 0:
        raise OfficeQAValidationError("--top-k must be positive")
    if not -2 <= args.presence_penalty <= 2:
        raise OfficeQAValidationError("--presence-penalty must be between -2 and 2")
    if args.thinking_type is not None and (
        args.enable_thinking is not None or args.preserve_thinking is not None
    ):
        raise OfficeQAValidationError(
            "--thinking-type cannot be combined with chat-template thinking flags"
        )
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.endpoint_label):
        raise OfficeQAValidationError(
            "--endpoint-label must be a short public label, not a URL or secret"
        )
    if not args.base_url.strip():
        raise OfficeQAValidationError("--base-url must not be empty")
    if not args.model.strip() or len(args.model) > 256 or any(
        character in args.model for character in "\r\n"
    ):
        raise OfficeQAValidationError("--model must be one non-empty public identifier")
    pricing = _pricing_settings(args)

    cases, input_metadata = load_cases(args.csv, args.manifest, args.corpus_dir)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfficeQAValidationError(f"invalid manifest {args.manifest}: {error}") from error
    selected = _select_cases(cases, args)
    full_uids = [case.uid for case in cases]
    scorer_config = manifest.get("scorer") or {}
    if (
        not isinstance(scorer_config.get("commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", scorer_config["commit"])
        or not isinstance(scorer_config.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", scorer_config["sha256"])
    ):
        raise OfficeQAValidationError("manifest scorer commit and SHA-256 must be pinned")
    score_answer = load_scorer(args.scorer, scorer_config["sha256"])
    raw_tolerance = scorer_config.get("tolerance", 0.0)
    if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)):
        raise OfficeQAValidationError("manifest scorer.tolerance must be a number")
    tolerance = float(raw_tolerance)
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise OfficeQAValidationError(
            "manifest scorer.tolerance must be finite and between 0 and 1"
        )
    if args.validate_only:
        print(json.dumps({
            "validated": True,
            "selected_count": len(selected),
            **input_metadata,
            "scorer_sha256": sha256_file(args.scorer),
        }, sort_keys=True))
        return 0

    if args.context_window is None:
        raise OfficeQAValidationError(
            "--context-window is required for a model run so endpoint configuration is recorded"
        )

    runtime_provenance = _runtime_provenance()

    api_key = os.environ.get(args.api_key_env, "")
    output_dir = args.output_dir.resolve()
    repository_root = Path(__file__).resolve().parents[3]
    try:
        output_dir.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise OfficeQAValidationError("--output-dir must be outside the Git repository")
    corpus_root = args.corpus_dir.resolve()
    try:
        output_dir.relative_to(corpus_root)
    except ValueError:
        pass
    else:
        raise OfficeQAValidationError("--output-dir must not be inside the search corpus")
    artifact_paths = {
        output_dir / name
        for name in ("results.jsonl", "run.json", "summary.json", "attempts.jsonl")
    }
    protected_inputs = {
        args.csv.resolve(),
        args.manifest.resolve(),
        args.scorer.resolve(),
    }
    if artifact_paths & protected_inputs:
        raise OfficeQAValidationError("output artifacts must not overwrite evaluation inputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    run_path = output_dir / "run.json"
    summary_path = output_dir / "summary.json"
    attempts_path = output_dir / "attempts.jsonl"
    final_thinking_type = args.final_thinking_type
    if final_thinking_type is None and args.thinking_type is not None:
        final_thinking_type = "disabled"
    generation_settings = GenerationSettings(
        max_output_tokens=args.max_output_tokens,
        final_output_tokens=args.final_output_tokens,
        inter_request_delay_seconds=args.inter_request_delay_seconds,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        presence_penalty=args.presence_penalty,
        seed=args.seed,
        enable_thinking=args.enable_thinking,
        preserve_thinking=args.preserve_thinking,
        thinking_type=args.thinking_type,
        final_thinking_type=final_thinking_type,
        preserve_reasoning_content=args.preserve_reasoning_content,
        send_tool_choice=args.send_tool_choice,
        reasoning_effort=args.reasoning_effort,
        final_reasoning_effort=args.final_reasoning_effort,
        request_retries=args.request_retries,
        request_retry_backoff_seconds=args.request_retry_backoff_seconds,
        max_retry_after_seconds=args.max_retry_after_seconds,
    )

    run_config = {
        "schema_version": 1,
        "benchmark": "officeqa_full",
        "harness": HARNESS_ID,
        "harness_prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        **runtime_provenance,
        "dataset_revision": manifest["source"]["revision"],
        "dataset_sha256": input_metadata["csv_sha256"],
        "corpus_tree_sha256": input_metadata["corpus_tree_sha256"],
        "corpus_file_count": input_metadata["corpus_file_count"],
        "manifest_sha256": input_metadata["manifest_sha256"],
        "scorer_commit": scorer_config["commit"],
        "scorer_sha256": scorer_config["sha256"],
        "model": args.model,
        "endpoint_label": args.endpoint_label,
        "pricing": (
            {
                "method": "estimated_from_reported_tokens",
                "currency": "USD",
                "price_label": pricing.label,
                "input_per_million": format(pricing.input_per_million, "f"),
                "cached_input_per_million": format(
                    pricing.cached_input_per_million,
                    "f",
                ),
                "output_per_million": format(pricing.output_per_million, "f"),
            }
            if pricing is not None
            else {"method": "unavailable"}
        ),
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "presence_penalty": args.presence_penalty,
        "seed": args.seed,
        "declared_context_window": args.context_window,
        "max_output_tokens": args.max_output_tokens,
        "tool_call_limit": args.tool_call_limit,
        "max_rounds": args.max_rounds,
        "request_timeout": args.request_timeout,
        "inter_request_delay_seconds": args.inter_request_delay_seconds,
        "client_max_retries": 0,
        "request_retries": args.request_retries,
        "request_retry_backoff_seconds": args.request_retry_backoff_seconds,
        "max_retry_after_seconds": args.max_retry_after_seconds,
        "case_retries": args.case_retries,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "inter_case_delay_seconds": args.inter_case_delay_seconds,
        "concurrency": 1,
        "enable_thinking": args.enable_thinking,
        "preserve_thinking": args.preserve_thinking,
        "thinking_type": args.thinking_type,
        "preserve_reasoning_content": args.preserve_reasoning_content,
        "send_tool_choice": args.send_tool_choice,
        "reasoning_effort": args.reasoning_effort,
        "final_submission": {
            "tool_choice": "submit_answer" if args.send_tool_choice else None,
            "direct_answer_fallback": "one_no_tool_request",
            "max_output_tokens": args.final_output_tokens,
            "temperature": 0.0,
            "top_p": 1.0,
            "top_k": None,
            "presence_penalty": 0.0,
            "enable_thinking": (
                False if args.enable_thinking is not None else None
            ),
            "preserve_thinking": (
                False if args.preserve_thinking is not None else None
            ),
            "thinking_type": final_thinking_type,
            "reasoning_effort": args.final_reasoning_effort,
        },
        "continue_on_nonscorable": args.continue_on_nonscorable,
        "tolerance": tolerance,
        "selected_uids": [case.uid for case in selected],
    }
    run_fingerprint = _canonical_sha256(run_config)
    if run_path.exists():
        existing_run = json.loads(run_path.read_text(encoding="utf-8"))
        if existing_run.get("run_fingerprint") != run_fingerprint:
            raise OfficeQAValidationError("output directory belongs to a different run config")
    else:
        _write_json(run_path, {
            **run_config,
            "run_fingerprint": run_fingerprint,
            "created_at": _utc_now(),
        })

    repaired_ledgers = [
        path.name
        for path in (results_path, attempts_path)
        if _repair_truncated_jsonl_tail(path)
    ]
    existing = load_results(results_path)
    selected_uids = [case.uid for case in selected]
    unexpected = sorted(set(existing) - set(selected_uids))
    if unexpected:
        raise OfficeQAValidationError(f"result ledger contains unselected UIDs: {unexpected}")
    nonscorable_existing = next(
        (
            existing[uid]
            for uid in selected_uids
            if uid in existing and existing[uid]["status"] not in {"pass", "fail"}
        ),
        None,
    )
    if nonscorable_existing is not None and not args.continue_on_nonscorable:
        summary = summarize_results(
            selected_uids,
            existing,
            expected_full_uids=full_uids,
        )
        _write_json(summary_path, {
            **summary,
            "run_fingerprint": run_fingerprint,
            "results_sha256": sha256_file(results_path),
            "repaired_ledgers": repaired_ledgers,
            "halted_uid": nonscorable_existing["uid"],
            "halted_status": nonscorable_existing["status"],
            "updated_at": _utc_now(),
        })
        print(
            f"stopped at existing non-scorable result "
            f"{nonscorable_existing['uid']} ({nonscorable_existing['status']})",
            file=sys.stderr,
            flush=True,
        )
        return 4
    attempt_records = _load_attempt_records(attempts_path)
    unexpected_attempts = sorted(set(attempt_records) - set(selected_uids))
    if unexpected_attempts:
        raise OfficeQAValidationError(
            f"attempt ledger contains unselected UIDs: {unexpected_attempts}"
        )
    for case in selected:
        history = attempt_records.get(case.uid, [])
        if case.uid not in existing and history and not _is_retryable_result(history[-1]):
            restored = _merge_attempt_results(history)
            _append_result(results_path, restored)
            existing[case.uid] = restored
    pending = [case for case in selected if case.uid not in existing]
    if pending and not api_key:
        raise OfficeQAValidationError(
            f"API key environment variable is empty: {args.api_key_env}"
        )
    for index, case in enumerate(pending, 1):
        attempt_results = list(attempt_records.get(case.uid, []))
        wait_before_attempt = 0.0
        for attempt_index in range(1, args.case_retries + 2):
            result = run_case(
                case,
                corpus_dir=args.corpus_dir.resolve(),
                spill_dir=output_dir / "spill",
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                tool_call_limit=args.tool_call_limit,
                max_rounds=args.max_rounds,
                request_timeout=args.request_timeout,
                settings=generation_settings,
                pricing=pricing,
                tolerance=tolerance,
                score_answer=score_answer,
            )
            record = _attempt_record(
                result,
                len(attempt_results) + 1,
                wait_before_attempt,
            )
            _append_result(attempts_path, record)
            attempt_results.append(record)
            attempt_records[case.uid] = attempt_results
            if not _is_retryable_result(result):
                break
            if attempt_index <= args.case_retries:
                wait_seconds = args.retry_backoff_seconds * (2 ** (attempt_index - 1))
                print(
                    f"  retry {case.uid} after {result['error_type']} "
                    f"in {wait_seconds:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_seconds)
                wait_before_attempt = wait_seconds

        if _is_retryable_result(result):
            summary = summarize_results(
                selected_uids,
                existing,
                expected_full_uids=full_uids,
            )
            _write_json(summary_path, {
                **summary,
                "run_fingerprint": run_fingerprint,
                "attempts_sha256": sha256_file(attempts_path),
                "repaired_ledgers": repaired_ledgers,
                "retry_pending_uid": case.uid,
                "updated_at": _utc_now(),
            })
            print(
                f"stopped with retryable {result['status']} for {case.uid}; "
                "rerun the same command to resume",
                file=sys.stderr,
                flush=True,
            )
            return 3

        result = _merge_attempt_results(attempt_results)
        _append_result(results_path, result)
        existing[case.uid] = result
        print(
            f"[{index}/{len(pending)}] {case.uid} {result['status']} "
            f"{result['latency_seconds']:.1f}s tokens={result['usage']['total_tokens']}",
            file=sys.stderr,
            flush=True,
        )
        summary = summarize_results(
            selected_uids,
            existing,
            expected_full_uids=full_uids,
        )
        _write_json(summary_path, {
            **summary,
            "run_fingerprint": run_fingerprint,
            "attempts_sha256": sha256_file(attempts_path),
            "repaired_ledgers": repaired_ledgers,
            **(
                {"halted_uid": case.uid, "halted_status": result["status"]}
                if result["status"] not in {"pass", "fail"}
                and not args.continue_on_nonscorable
                else {}
            ),
            "updated_at": _utc_now(),
        })
        if (
            result["status"] not in {"pass", "fail"}
            and not args.continue_on_nonscorable
        ):
            print(
                f"stopped at non-scorable result {case.uid} ({result['status']})",
                file=sys.stderr,
                flush=True,
            )
            return 4
        if index < len(pending) and args.inter_case_delay_seconds:
            time.sleep(args.inter_case_delay_seconds)

    final_cases, final_input_metadata = load_cases(
        args.csv,
        args.manifest,
        args.corpus_dir,
    )
    if (
        [case.uid for case in final_cases] != full_uids
        or final_input_metadata != input_metadata
    ):
        raise OfficeQAValidationError("OfficeQA inputs changed during the model run")
    verify_scores(cases, existing, score_answer, tolerance)
    summary = summarize_results(
        selected_uids,
        existing,
        expected_full_uids=full_uids,
    )
    _write_json(summary_path, {
        **summary,
        "run_fingerprint": run_fingerprint,
        "results_sha256": sha256_file(results_path),
        "attempts_sha256": sha256_file(attempts_path),
        "repaired_ledgers": repaired_ledgers,
        "updated_at": _utc_now(),
    })
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfficeQAValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
