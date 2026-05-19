#!/usr/bin/env bash
# Docker E2E 顶层入口
# 用法：
#   run.sh <scenario_name>     跑单个 scenario
#   run.sh all                  跑 scenarios/ 下所有
set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="$THIS_DIR/scenarios"

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    echo "usage: $0 <scenario_name>|all" >&2
    exit 2
fi

# 1. 确保镜像就绪
bash "$THIS_DIR/rig/build.sh"

# 2. 决定要跑哪些 scenario
if [ "$TARGET" = "all" ]; then
    mapfile -t SCENARIOS < <(find "$SCENARIOS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
else
    if [ ! -d "$SCENARIOS_DIR/$TARGET" ]; then
        echo "scenario not found: $SCENARIOS_DIR/$TARGET" >&2
        exit 2
    fi
    SCENARIOS=("$TARGET")
fi

# 3. LLM key（部分 scenario 需要）
LLM_KEY=""
if [ -r "$HOME/.aikey" ]; then
    LLM_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$HOME/.aikey" | cut -d= -f2- || true)"
fi

# 4. 逐个跑
FAILED=0
for name in "${SCENARIOS[@]}"; do
    echo
    echo "========================================================"
    echo "=== scenario: $name"
    echo "========================================================"

    docker run --rm \
        -e DEEPSEEK_API_KEY="$LLM_KEY" \
        -v "$SCENARIOS_DIR/$name:/scenario:ro" \
        xskill-e2e:local \
        /scenario \
        || { echo "✗ scenario $name FAILED"; FAILED=$((FAILED+1)); }
done

echo
if [ "$FAILED" -eq 0 ]; then
    echo "========================================================"
    echo "✓ all ${#SCENARIOS[@]} scenario(s) passed"
    exit 0
else
    echo "✗ $FAILED of ${#SCENARIOS[@]} scenario(s) failed"
    exit 1
fi
