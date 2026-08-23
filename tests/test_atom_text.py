"""Deterministic TaskAgent language and adjacent-Atom dedupe contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xskill.agents.atom_text import (
    adjacent_atoms_are_near_duplicates,
    detect_source_language,
    output_language_matches,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please update `src/中文.py`.\n$ pytest 测试.py", "en"),
        ("请更新 `src/english.py`。\n$ pytest tests/test_agent.py", "zh"),
        ("请帮我运行测试。\npytest tests/test_agent.py -q", "zh"),
        ("```bash\npytest tests/test_agent.py\n```", "unknown"),
        ("```bash\npytest tests/test_agent.py", "unknown"),
        ("Make the user interface responsive.", "en"),
        ("このテストを修正してください。", "unknown"),
    ],
)
def test_source_language_ignores_code_paths_and_commands(text, expected):
    assert detect_source_language(text) == expected


def test_opposite_output_language_is_rejected_but_technical_text_is_allowed():
    assert output_language_matches("Create a reusable helper.", "en")
    assert not output_language_matches("创建可复用工具。", "en")
    assert output_language_matches("`src/xskill/agents/task_agent.py`", "en")


def test_replay_near_duplicate_is_detected_without_model_calls():
    fixture = (
        Path(__file__).parent.parent
        / "scripts"
        / "bench"
        / "algorithm_replay"
        / "fixtures"
        / "baseline_v1.json"
    )
    suite = json.loads(fixture.read_text(encoding="utf-8"))
    case = next(
        case
        for case in suite["cases"]
        if case["case_id"] == "en-near-duplicate-observation"
    )
    previous, current = case["predicted_atoms"]

    assert adjacent_atoms_are_near_duplicates(
        previous,
        current,
        current_user_text=case["source_lines"][4],
    )


@pytest.mark.parametrize(
    ("previous", "current", "user_text"),
    [
        (
            {"intent": "Create a CSV file utility", "summary": "Handle CSV rows."},
            {"intent": "Create a JSON file utility", "summary": "Handle JSON data."},
            "Now create a JSON utility.",
        ),
        (
            {"intent": "Fix login validation", "summary": "Validate login."},
            {"intent": "Fix logout validation", "summary": "Validate logout."},
            "Now fix logout validation.",
        ),
    ],
)
def test_related_but_distinct_intents_are_not_merged(previous, current, user_text):
    assert not adjacent_atoms_are_near_duplicates(
        previous,
        current,
        current_user_text=user_text,
    )


def test_chinese_continuation_is_detected_as_near_duplicate():
    assert adjacent_atoms_are_near_duplicates(
        {"intent": "修复登录校验", "summary": "修复登录请求的参数校验。"},
        {"intent": "继续修复登录校验", "summary": "继续完善登录请求的参数校验。"},
        current_user_text="继续完善这个登录校验。",
    )
