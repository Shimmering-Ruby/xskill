#!/bin/bash
# 真 openclaw + 真 DeepSeek 的 e2e
#
# 流程：
#   1. 起 fake LLM (xskill daemon 自己用) + xskill daemon
#   2. 在 ~/.xskill/skill/ 放一个独有的 demo skill
#   3. mkdir ~/.openclaw/agents/main/sessions —— 触发 xskill detect_known_ecosystems
#      看见 openclaw，startup install_all_to_openclaw 把 demo skill 装到
#      ~/.agents/skills/
#   4. 等几秒让 xskill watcher 跑一轮，再确认 demo skill 已落地
#   5. 配置 openclaw + 真 DeepSeek
#   6. 跑 `openclaw agent --local` 真打一次 DeepSeek，让它产出 trajectory
#   7. 验：
#        - openclaw 的 trace.metadata.data.skills 里包含我们装的 demo skill
#          → 证明 openclaw 真的扫到了 ~/.agents/skills/xskill-demo-marker
#        - xskill 桥出了 traj_oc_*.md
set -euo pipefail

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "DEEPSEEK_API_KEY env required" >&2
    exit 2
fi

mkdir -p "$HOME/.openclaw" "$HOME/.xskill/skill"
cp /tmp/openclaw.json "$HOME/.openclaw/openclaw.json"
cp /tmp/xskill_config.yaml "$HOME/.xskill/config.yaml"
cp -r /tmp/demo_skill "$HOME/.xskill/skill/xskill-demo-marker"

# 1. 起 fake LLM (xskill daemon 用)
echo "=== [1] starting fake LLM on :19999"
python3.11 -c "
import sys, os
sys.path.insert(0, '/usr/local/lib/python3.11/dist-packages')
" 2>/dev/null || true

# fake_llm_server.py 是 xskill 测试用的；直接用 xskill 自己的 wheel 没带它，
# 我们这里只需要 daemon 起来不崩，不调用 fake LLM 的实际推理。
python3.11 <<'PYEOF' > /tmp/fake.log 2>&1 &
from fastapi import FastAPI
import uvicorn
app = FastAPI()
@app.get("/v1/health")
def h(): return {"ok": True}
@app.post("/v1/chat/completions")
def c(): return {"id":"x","choices":[{"message":{"role":"assistant","content":"ok"}}]}
@app.post("/v1/embeddings")
def e(): return {"data":[{"embedding":[0.0]*8,"index":0}]}
uvicorn.run(app, host="127.0.0.1", port=19999, log_level="warning")
PYEOF
FAKE_PID=$!
for i in $(seq 1 40); do
    curl -fs http://127.0.0.1:19999/v1/health >/dev/null 2>&1 && break
    sleep 0.25
done
echo "    fake LLM ready"

# 2. 起 xskill daemon
echo "=== [2] starting xskill daemon"
xskill serve --port 18910 > /tmp/xskill.log 2>&1 &
XSKILL_PID=$!
trap "kill $FAKE_PID $XSKILL_PID 2>/dev/null || true" EXIT

for i in $(seq 1 60); do
    if curl -fs http://127.0.0.1:18910/api/v1/health >/dev/null 2>&1; then
        echo "    daemon ready"
        break
    fi
    sleep 0.5
done
if ! curl -fs http://127.0.0.1:18910/api/v1/health >/dev/null 2>&1; then
    echo "✗ xskill daemon failed to come up"
    tail -30 /tmp/xskill.log
    exit 1
fi

# 3. mkdir ~/.openclaw/agents/main/sessions 触发 xskill detect openclaw
echo "=== [3] creating ~/.openclaw/agents/main/sessions to trigger detect"
mkdir -p "$HOME/.openclaw/agents/main/sessions"
mkdir -p "$HOME/.openclaw/workspace"

# 4. 等 daemon 下一轮 poll，触发 install_all_to_openclaw
echo "=== [4] waiting 8s for xskill to detect openclaw + install demo skill"
sleep 8

if [ ! -e "$HOME/.agents/skills/xskill-demo-marker/SKILL.md" ]; then
    echo "✗ xskill 没装成 demo skill 到 ~/.agents/skills/xskill-demo-marker/"
    ls -la "$HOME/.agents/skills/" 2>&1 || echo "(.agents/skills doesn't exist)"
    tail -40 /tmp/xskill.log
    exit 1
fi
echo "    ✓ ~/.agents/skills/xskill-demo-marker/SKILL.md exists"

# 5. 用 openclaw 真打一次 DeepSeek
echo "=== [5] running real openclaw agent --local with DeepSeek"
set +e
openclaw agent --local --json \
    --message "Just reply 'hello'. Nothing else." \
    > /tmp/agent_out.json 2> /tmp/agent_err.log
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
    echo "✗ openclaw agent run failed (rc=$RC)"
    echo "--- agent stdout ---"; cat /tmp/agent_out.json
    echo "--- agent stderr ---"; tail -50 /tmp/agent_err.log
    exit 1
fi
echo "    agent run OK"

# 找 trajectory 文件
TRAJ_FILE=$(find "$HOME/.openclaw/agents" -name '*.trajectory.jsonl' | head -1)
if [ -z "$TRAJ_FILE" ]; then
    echo "✗ openclaw agent 没产生 trajectory.jsonl"
    find "$HOME/.openclaw" -type f 2>&1 | head
    exit 1
fi
echo "    trajectory: $TRAJ_FILE ($(wc -l < "$TRAJ_FILE") events)"

# 6a. 验：openclaw trace.metadata.data.skills 里包含 xskill-demo-marker
echo "=== [6a] assert openclaw saw the installed skill"
SKILLS_SEEN=$(jq -r '
    select(.type == "trace.metadata") | .data.skills | tojson
' "$TRAJ_FILE" 2>/dev/null | head -1)
echo "    trace.metadata.data.skills = $SKILLS_SEEN"
if ! echo "$SKILLS_SEEN" | grep -q "xskill-demo-marker"; then
    echo "✗ openclaw 的 trace.metadata 里没看到 xskill-demo-marker"
    echo "完整 trace.metadata event："
    jq -c 'select(.type == "trace.metadata")' "$TRAJ_FILE" | head -1
    exit 1
fi
echo "    ✓ openclaw saw xskill-demo-marker in trace.metadata"

# 6b. 验：xskill 桥出了 traj_oc_*.md
echo "=== [6b] wait xskill ingester to bridge new trajectory"
sleep 8
if ! ls "$HOME/.xskill/openclaw_sessions/traj_oc_"*.md 1>/dev/null 2>&1; then
    echo "✗ xskill 没桥出 traj_oc_*.md"
    ls -la "$HOME/.xskill/openclaw_sessions/" 2>&1 || echo "(bridge dir missing)"
    tail -40 /tmp/xskill.log
    exit 1
fi
BRIDGED=$(ls "$HOME/.xskill/openclaw_sessions/traj_oc_"*.md | head -1)
echo "    ✓ xskill bridged: $BRIDGED"

echo ""
echo "=========================================="
echo "✓ REAL openclaw + REAL DeepSeek e2e PASSED"
echo "=========================================="
