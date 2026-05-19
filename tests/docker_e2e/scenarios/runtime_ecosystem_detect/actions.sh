#!/usr/bin/env bash
# Bug #5 scenario actions：
#  1. 校验初始无 claude_code（daemon 在空 home 启动）
#  2. mkdir ~/.claude/projects/foo + 丢 fake CC jsonl
#  3. 等待 watcher hook 触发新一轮检测
set -euo pipefail

echo "--- [actions] step 1: initial state should NOT have claude_code"
if python3 /rig/probe.py assert_ecosystem_present "$XSKILL_PORT" claude_code 2>/dev/null; then
    echo "✗ unexpected: claude_code already in registry on fresh home"
    exit 1
fi
echo "    ✓ confirmed empty"

echo "--- [actions] step 2: simulate user just installed Claude Code"
mkdir -p "$TESTHOME/.claude/projects/test-proj"
cp "$FIXTURES/cc_session.jsonl" "$TESTHOME/.claude/projects/test-proj/sess-abc12345.jsonl"
echo "    ✓ dropped fake CC session under $TESTHOME/.claude/projects/test-proj/"

echo "--- [actions] step 3: wait for watcher._loop on_poll_hook to fire"
# poll_interval=3，给 2 轮 + 安全 buffer = 8s
sleep 10
echo "    ✓ wait done"
