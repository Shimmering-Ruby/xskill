#!/usr/bin/env bash
# host 入口：build 镜像 + 跑容器
# 跑前需要 ~/.aikey 含 DEEPSEEK_API_KEY，会真打 DeepSeek（产生少量费用）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO"

if [ ! -r "$HOME/.aikey" ]; then
    echo "~/.aikey not found — need DEEPSEEK_API_KEY in there" >&2
    exit 2
fi
DEEPSEEK_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$HOME/.aikey" | cut -d= -f2-)"
if [ -z "$DEEPSEEK_KEY" ]; then
    echo "DEEPSEEK_API_KEY missing in ~/.aikey" >&2
    exit 2
fi

# 确保 wheel 最新（rig/build.sh 里那段逻辑的最小版本）
if ! ls dist/xskill-*.whl >/dev/null 2>&1; then
    rm -f dist/xskill-*.whl dist/xskill-*.tar.gz
    python3.11 -m build --wheel >/dev/null
fi

echo "=== build openclaw_real_llm image (npm install openclaw ~5-10min first time) ==="
docker build -t xskill-openclaw-real:local -f tests/docker_e2e/openclaw_real_llm/Dockerfile .

echo
echo "=== run real openclaw + DeepSeek e2e ==="
docker run --rm \
    -e DEEPSEEK_API_KEY="$DEEPSEEK_KEY" \
    xskill-openclaw-real:local
