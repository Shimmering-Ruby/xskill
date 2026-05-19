#!/usr/bin/env bash
# runtime_openclaw_detect actions：
#  1. 校验初始无 openclaw（daemon 在空 home 启动）
#  2. mkdir ~/.openclaw/agents/main/sessions + 丢 fake trajectory
#  3. 等 watcher hook 触发新一轮检测
set -euo pipefail

echo "--- [actions] step 1: initial state should NOT have openclaw"
if python3 /rig/probe.py assert_ecosystem_present "$XSKILL_PORT" openclaw 2>/dev/null; then
    echo "✗ unexpected: openclaw already in registry on fresh home"
    exit 1
fi
echo "    ✓ confirmed empty"

echo "--- [actions] step 2: simulate user just installed openclaw"
mkdir -p "$TESTHOME/.openclaw/agents/main/sessions"
cp "$FIXTURES/openclaw_session.trajectory.jsonl" \
   "$TESTHOME/.openclaw/agents/main/sessions/11111111-2222-3333-4444-555555555555.trajectory.jsonl"
echo "    ✓ dropped fake openclaw session under $TESTHOME/.openclaw/agents/main/sessions/"

echo "--- [actions] step 3: wait for watcher._loop on_poll_hook to fire"
# poll_interval=3，给 2 轮 + 安全 buffer = 10s
sleep 10
echo "    ✓ wait done"
