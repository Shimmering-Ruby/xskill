#!/usr/bin/env bash
# 容器入口：跑单个 scenario 的完整生命周期
# 用法：entrypoint.sh <scenario_dir>
#   scenario_dir 是容器内绝对路径，含 pre_state/ / actions.sh / assertions.sh / fixtures/

set -euo pipefail

SCENARIO_DIR="${1:?scenario dir required}"
DAEMON_LOG=/tmp/xskill_daemon.log
FAKE_LOG=/tmp/fake_llm.log

echo "=== [rig] scenario: $SCENARIO_DIR"

# 1. 重建干净的 fake home
rm -rf "$TESTHOME"
mkdir -p "$TESTHOME"

# 2. 拷 pre_state（若有）
if [ -d "$SCENARIO_DIR/pre_state" ]; then
    rsync -a "$SCENARIO_DIR/pre_state/" "$TESTHOME/"
    echo "=== [rig] pre_state synced"
fi

# 3. 起假 LLM/embed 服务器，固定监听 $FAKE_LLM_PORT
# pre_state 的 config.yaml 应当把 llm.base_url / embedding.base_url 都指向 http://127.0.0.1:$FAKE_LLM_PORT
echo "=== [rig] starting fake LLM server on :$FAKE_LLM_PORT"
( python3 /rig/_fake_llm_server.py "$FAKE_LLM_PORT" >"$FAKE_LOG" 2>&1 ) &
FAKE_PID=$!
trap "kill $FAKE_PID 2>/dev/null; kill \${DAEMON_PID:-0} 2>/dev/null || true" EXIT

# 等假 LLM 就绪：FakeLLMServer 暴露 GET /v1/health（200 + {ok: true}）
FAKE_READY=0
for i in $(seq 1 40); do
    if curl -fs "http://127.0.0.1:$FAKE_LLM_PORT/v1/health" >/dev/null 2>&1; then
        echo "=== [rig] fake LLM ready (after ${i} polls)"
        FAKE_READY=1
        break
    fi
    sleep 0.25
done
if [ "$FAKE_READY" -ne 1 ]; then
    echo "=== [rig] fake LLM did NOT come up; tail of fake log:"
    tail -50 "$FAKE_LOG" 2>&1 || true
    exit 1
fi

# 4. 把 fixtures 暴露在环境变量里给 actions/assertions 用
export FIXTURES="$SCENARIO_DIR/fixtures"
export TESTHOME XSKILL_PORT FAKE_LLM_PORT

# 5. 起 daemon
echo "=== [rig] starting daemon on port $XSKILL_PORT (HOME=$TESTHOME)"
( HOME="$TESTHOME" xskill serve --port "$XSKILL_PORT" >"$DAEMON_LOG" 2>&1 ) &
DAEMON_PID=$!

# 6. 等 /api/v1/health 就绪（最多 45s）
python3 /rig/probe.py wait_ready "$XSKILL_PORT" 45 || {
    echo "=== [rig] daemon did NOT become ready in 45s"
    echo "=== [rig] --- daemon log tail ---"
    tail -80 "$DAEMON_LOG"
    echo "=== [rig] --- fake LLM log tail ---"
    tail -20 "$FAKE_LOG"
    exit 1
}
echo "=== [rig] daemon ready"

# 7. 跑 actions.sh（可选）
if [ -x "$SCENARIO_DIR/actions.sh" ]; then
    echo "=== [rig] running actions.sh"
    bash "$SCENARIO_DIR/actions.sh" || {
        echo "=== [rig] actions.sh failed"
        tail -80 "$DAEMON_LOG"
        exit 1
    }
fi

# 8. 跑 assertions.sh
if [ ! -x "$SCENARIO_DIR/assertions.sh" ]; then
    echo "=== [rig] no assertions.sh — scenario must have one to be meaningful"
    exit 1
fi
echo "=== [rig] running assertions.sh"
if ! bash "$SCENARIO_DIR/assertions.sh"; then
    echo "=== [rig] ✗ assertions FAILED"
    echo "=== [rig] --- daemon log tail ---"
    tail -100 "$DAEMON_LOG"
    exit 1
fi

echo "=== [rig] ✓ scenario passed"
