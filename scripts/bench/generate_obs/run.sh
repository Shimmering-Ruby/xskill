#!/usr/bin/env bash
# 在容器里跑一趟 generate 行为观测，把特征写成 JSON。
#
#   ./run.sh --job baseline-01 --dry-run          # 不打模型，验装配
#   ./run.sh --job baseline-01                    # 真打 DeepSeek
#   ./run.sh --job wide-read --fake-reads 50 --dry-run
#
# job 名是必填的：features.json、输出目录、Phoenix 里的标记都按它区分，
# 没名字就没法跨轮对比。
#
# 数据：本机 Cursor 的历史会话由 export_cursor_mock.py 全量桥成 xskill 原
# 生 traj_*.md，落在 mock xskill home 里。容器只读挂 ~/.cursor，写只写
# mock home 和 runs 目录，碰不到真实 ~/.xskill。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
IMAGE="${GOBS_IMAGE:-xskill-generate-obs:local}"
GOBS_ROOT="${GOBS_ROOT:-$HOME/xskill-generate-obs}"
MOCK_HOME="$GOBS_ROOT/mock"
RUNS_DIR="$GOBS_ROOT/runs"
SOURCE_HOME="${GOBS_SOURCE_HOME:-$HOME}"
AIKEY_FILE="${AIKEY_FILE:-$HOME/.aikey}"

JOB=""
DRY_RUN=0
BUILD=0
REFRESH_MOCK=0
CAPTURE_CONTENT=1
PHOENIX_ENDPOINT="${XSKILL_OTEL_ENDPOINT:-}"
PASSTHRU=()

usage() {
  cat <<'EOF'
用法: ./run.sh --job <名字> [选项]

必填
  --job NAME              这趟实验的名字

常用
  --dry-run               不打模型（换掉 HTTP 层），其余原路跑
  --fake-reads N          dry-run 时假模型读几条轨迹（默认 8，50 左右会触发 compact）
  --instruction TEXT      给 generate 的指令
  --compact-token-limit N compact 阈值，默认 100000
  --max-context N         上下文窗口，默认 200000
  --capture-content       span 里记截断后的提示词正文（默认开）
  --no-capture-content    不记提示词正文

环境
  --build                 强制重建镜像
  --refresh-mock          重新导出 Cursor 会话到 mock home
  --phoenix-endpoint URL  OTLP 接收端；不给则自动探测本机 Phoenix

产物
  $GOBS_ROOT/runs/<job>/features.json   行为特征
  $GOBS_ROOT/runs/<job>/spans.jsonl     OTel span
  $GOBS_ROOT/runs/<job>/run.json        入参与结果摘要
  $GOBS_ROOT/runs/<job>/trace/          人读的逐轮 trace
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job) JOB="$2"; shift 2 ;;
    --job=*) JOB="${1#--job=}"; shift ;;
    --dry-run) DRY_RUN=1; PASSTHRU+=("--dry-run"); shift ;;
    --build) BUILD=1; shift ;;
    --refresh-mock) REFRESH_MOCK=1; shift ;;
    --phoenix-endpoint) PHOENIX_ENDPOINT="$2"; shift 2 ;;
    --phoenix-endpoint=*) PHOENIX_ENDPOINT="${1#--phoenix-endpoint=}"; shift ;;
    --capture-content) CAPTURE_CONTENT=1; shift ;;
    --no-capture-content) CAPTURE_CONTENT=0; shift ;;
    --instruction|--instruction-file|--user-id|--fake-reads|--max-context|--compact-token-limit)
      PASSTHRU+=("$1" "$2"); shift 2 ;;
    --instruction=*|--instruction-file=*|--user-id=*|--fake-reads=*|--max-context=*|--compact-token-limit=*)
      PASSTHRU+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$JOB" ]]; then
  echo "必须先指定 job 名字：--job <名字>" >&2
  usage
  exit 2
fi
if [[ ! "$JOB" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "job 名只允许字母数字和 . _ -（会当目录名用）: $JOB" >&2
  exit 2
fi

mkdir -p "$MOCK_HOME" "$RUNS_DIR"

# ── 镜像 ────────────────────────────────────────────────────────
if [[ "$BUILD" -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "==> 构建镜像 $IMAGE"
  docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$REPO"
fi

# ── Phoenix 端点 ────────────────────────────────────────────────
# 不给就探一下本机 6006（serve_phoenix.sh 的默认口）。容器里走 docker
# 网桥网关回到宿主机。
if [[ -z "$PHOENIX_ENDPOINT" ]]; then
  GATEWAY="$(docker network inspect bridge \
    --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || true)"
  # 8873 在前：本机长期跑着一个 Phoenix 在这个口，且它是已放行的端口。
  for port in ${GOBS_PHOENIX_PORTS:-8873 6006}; do
    if curl -sf -m 2 -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null; then
      if [[ -n "$GATEWAY" ]]; then
        PHOENIX_ENDPOINT="http://$GATEWAY:$port"
      else
        PHOENIX_ENDPOINT="http://127.0.0.1:$port"
      fi
      echo "==> 探到本机 Phoenix（:$port），span 送 $PHOENIX_ENDPOINT"
      break
    fi
  done
  if [[ -z "$PHOENIX_ENDPOINT" ]]; then
    echo "==> 没探到本机 Phoenix（跳过面板，span 仍写 spans.jsonl）"
    echo "    要面板就先跑: $HERE/serve_phoenix.sh"
  fi
fi

DOCKER_ARGS=(
  --rm
  -v "$REPO:/repo:ro"
  -v "$MOCK_HOME:/mock"
  -v "$RUNS_DIR:/runs"
  -e "HOME=/mock"
  -e "PYTHONPATH=/repo/src"
  --user "$(id -u):$(id -g)"
)
if [[ -n "$PHOENIX_ENDPOINT" ]]; then
  DOCKER_ARGS+=(-e "XSKILL_OTEL_ENDPOINT=$PHOENIX_ENDPOINT")
  DOCKER_ARGS+=(-e "XSKILL_OTEL_PUBLIC_BASE=${GOBS_PHOENIX_PUBLIC:-http://8.219.96.11:8873}")
fi

# ── mock 数据 ───────────────────────────────────────────────────
if [[ "$REFRESH_MOCK" -eq 1 || ! -f "$MOCK_HOME/.xskill/mock_manifest.json" ]]; then
  if [[ ! -d "$SOURCE_HOME/.cursor/projects" ]]; then
    echo "找不到 $SOURCE_HOME/.cursor/projects，没有 Cursor 会话可导" >&2
    exit 2
  fi
  echo "==> 导出 Cursor 会话到 mock xskill home"
  WIPE_ARGS=()
  if [[ "$REFRESH_MOCK" -eq 1 ]]; then
    WIPE_ARGS+=(--wipe)
  fi
  docker run "${DOCKER_ARGS[@]}" \
    -v "$SOURCE_HOME/.cursor:/source-home/.cursor:ro" \
    "$IMAGE" export_cursor_mock.py --source-home /source-home "${WIPE_ARGS[@]+"${WIPE_ARGS[@]}"}"
fi

# ── 真打模型时才要 key ──────────────────────────────────────────
if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ ! -f "$AIKEY_FILE" ]]; then
    echo "真跑要 DeepSeek key，但没有 $AIKEY_FILE；或者加 --dry-run" >&2
    exit 2
  fi
  # 只取需要的两个值，不回显、不落日志。
  DEEPSEEK_API_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$AIKEY_FILE" | cut -d= -f2-)"
  export DEEPSEEK_API_KEY
  if [[ -z "$DEEPSEEK_API_KEY" ]]; then
    echo "$AIKEY_FILE 里没有 DEEPSEEK_API_KEY" >&2
    exit 2
  fi
  DOCKER_ARGS+=(-e DEEPSEEK_API_KEY)
fi

if [[ "$CAPTURE_CONTENT" -eq 1 ]]; then
  PASSTHRU+=("--capture-content")
fi

echo "==> 跑 job: $JOB"
set +e
docker run "${DOCKER_ARGS[@]}" "$IMAGE" \
  run_generate_job.py --job "$JOB" --out "/runs/$JOB" "${PASSTHRU[@]+"${PASSTHRU[@]}"}"
STATUS=$?
set -e

FEATURES="$RUNS_DIR/$JOB/features.json"
if [[ -f "$FEATURES" ]]; then
  echo "==> 特征: $FEATURES"
else
  echo "==> 没生成 $FEATURES" >&2
fi
PHOENIX_URL="$(python3 -c "import json,sys; print((json.load(open(sys.argv[1])).get('phoenix') or {}).get('url') or '')" "$RUNS_DIR/$JOB/run.json" 2>/dev/null || true)"
if [[ -n "$PHOENIX_URL" ]]; then
  echo "==> Phoenix: $PHOENIX_URL"
fi
exit "$STATUS"
