"""tests/test_traj_analysis.py — 分析式工具调用计数 + token 估算（子项目 D1）"""
from __future__ import annotations

from xskill.utils.traj_analysis import (
    analyze_segment, count_tool_calls, estimate_tokens,
)

SEGMENT = """## User

deploy the app

## Assistant

ok, running tools

## Tool Call: Bash

input:
```
ls
```

## Tool Call: Read

output:
```
file
```

## Assistant

done
"""


def test_count_tool_calls():
    assert count_tool_calls(SEGMENT) == 2


def test_count_tool_calls_empty():
    assert count_tool_calls("") == 0
    assert count_tool_calls("no tools here\njust text") == 0


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40) == 10  # 40 // 4


def test_analyze_segment_shape():
    a = analyze_segment(SEGMENT)
    assert a["tool_calls"] == 2
    assert a["est_tokens"] == len(SEGMENT) // 4
    assert a["chars"] == len(SEGMENT)


def test_tool_call_marker_must_be_line_start():
    # 行内出现 "Tool Call" 不算（必须是 ## 段标题）
    assert count_tool_calls("see the ## Tool Call inline mention") == 0
