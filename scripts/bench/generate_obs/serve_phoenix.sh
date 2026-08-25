#!/usr/bin/env bash
# 本机起 Phoenix 面板收 OTLP。用仓库 venv 里的 arize-phoenix，不拉镜像
# （本机连 registry-1.docker.io 会被 TLS 拦）。
#
#   ./serve_phoenix.sh
#   PHOENIX_PORT=6006 ./serve_phoenix.sh
#
# 起来之后把 XSKILL_OTEL_ENDPOINT 指到这个口。公网入口自己设
# XSKILL_OTEL_PUBLIC_BASE，脚本不写死地址。
set -euo pipefail

PYTHON="${PYTHON:-/home/admin/xskill/.venv/bin/python}"
ROOT="${GOBS_ROOT:-$HOME/xskill-generate-obs}"

export PHOENIX_WORKING_DIR="${PHOENIX_WORKING_DIR:-$ROOT/phoenix}"
export PHOENIX_HOST="${PHOENIX_HOST:-0.0.0.0}"
export PHOENIX_PORT="${PHOENIX_PORT:-6006}"
export PHOENIX_TELEMETRY_ENABLED="${PHOENIX_TELEMETRY_ENABLED:-false}"
mkdir -p "$PHOENIX_WORKING_DIR"

if ! "$PYTHON" -c "import phoenix" >/dev/null 2>&1; then
  echo "venv 里没有 arize-phoenix。装: $PYTHON -m pip install 'xskill[phoenix]'" >&2
  exit 2
fi

# 已经有一个在跑就别再起：Phoenix 要独占 OTLP gRPC 口（4317），
# 第二个实例会在 bind 时直接失败退出。
if curl -sf -m 2 -o /dev/null "http://127.0.0.1:$PHOENIX_PORT/" 2>/dev/null; then
  echo "127.0.0.1:$PHOENIX_PORT 已经有 Phoenix 在跑，直接用它。"
  echo "面板: http://127.0.0.1:$PHOENIX_PORT/"
  exit 0
fi

echo "Phoenix 工作目录: $PHOENIX_WORKING_DIR"
echo "面板: http://127.0.0.1:$PHOENIX_PORT/"
exec "$PYTHON" -m phoenix.server.main serve
