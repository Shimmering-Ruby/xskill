#!/usr/bin/env bash
# =============================================================================
#  serve_ui.sh —— 跑完 run_demo.sh 之后，把前端面板拉起来继续演示
# =============================================================================
#  用法：
#      ./scripts/demo/serve_ui.sh                         # 默认 workspace=/tmp/t2s_demo, port=8000
#      ./scripts/demo/serve_ui.sh 8080                    # 指定端口
#      ./scripts/demo/serve_ui.sh 8080 /tmp/my_demo       # 端口 + workspace
#
#  环境变量：
#      OPEN_BROWSER=1    启动成功后自动打开浏览器（xdg-open / open）
#      HOST=0.0.0.0      绑定地址（默认 0.0.0.0，远程机器同网段可直接访问）
#
#  前端资源：已内置在 src/traj2skill/web/dist/，pip install -e . 后随包发布，
#  不需要 npm build。若想自己改 UI，去 web/ 目录下开发，构建脚本见 web/README。
# =============================================================================
set -e

PORT="${1:-8000}"
WORKSPACE="${2:-/tmp/t2s_demo}"
HOST="${HOST:-0.0.0.0}"

if [ -t 1 ]; then
    C_HEAD="\033[1;36m"; C_DIM="\033[2m"; C_BOLD="\033[1m"
    C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"; C_OFF="\033[0m"
else
    C_HEAD=""; C_DIM=""; C_BOLD=""; C_OK=""; C_WARN=""; C_ERR=""; C_OFF=""
fi

# ── 预检 ────────────────────────────────────────────────────────────────
if ! command -v t2s >/dev/null 2>&1; then
    echo -e "${C_ERR}✗ t2s 命令不存在 —— 先跑 ./scripts/demo/run_demo.sh${C_OFF}"
    exit 1
fi

if [ ! -f "$WORKSPACE/config.yaml" ]; then
    echo -e "${C_ERR}✗ 找不到 $WORKSPACE/config.yaml${C_OFF}"
    echo "  workspace 可能没初始化，先跑 ./scripts/demo/run_demo.sh 3 $WORKSPACE"
    exit 1
fi

if [ ! -d "$WORKSPACE/skill" ]; then
    echo -e "${C_WARN}⚠ $WORKSPACE/skill 不存在，UI 里看不到 skill 列表${C_OFF}"
    echo -e "${C_WARN}  建议先跑 ./scripts/demo/run_demo.sh 产出 skill${C_OFF}"
    echo
fi

# 端口冲突检测（ss 或 lsof，二选一）
port_in_use() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1
    else
        return 1
    fi
}

if port_in_use; then
    echo -e "${C_ERR}✗ 端口 $PORT 被占用${C_OFF}"
    echo "  换个端口跑：$0 8080 $WORKSPACE"
    exit 1
fi

# ── 打印摘要 ───────────────────────────────────────────────────────────
echo -e "${C_HEAD}════════════════════════════════════════════════════════════════${C_OFF}"
echo -e "${C_HEAD}  traj2skill Web UI${C_OFF}"
echo -e "${C_HEAD}════════════════════════════════════════════════════════════════${C_OFF}"
echo -e "  workspace   ${C_BOLD}$WORKSPACE${C_OFF}"
echo -e "  bind        ${C_BOLD}http://$HOST:$PORT${C_OFF}"
skill_count=$(ls "$WORKSPACE/skill" 2>/dev/null | grep -vE '^\.(git|skill_index)' | wc -l | tr -d ' ')
echo -e "  skills      ${C_BOLD}$skill_count${C_OFF} 个"
echo
echo -e "${C_DIM}  浏览器访问：   http://localhost:$PORT/${C_OFF}"
echo -e "${C_DIM}  SSE 流式日志：  http://localhost:$PORT/api/stream${C_OFF}"
echo -e "${C_DIM}  OpenAPI 文档：  http://localhost:$PORT/docs${C_OFF}"
echo -e "${C_DIM}  Ctrl-C 停止${C_OFF}"
echo

# ── 可选自动开浏览器（后台延迟 1.5s 等 server 起来） ───────────────────
if [ "${OPEN_BROWSER:-0}" = "1" ]; then
    (
        sleep 1.5
        url="http://localhost:$PORT/"
        if command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1
        elif command -v open     >/dev/null 2>&1; then open "$url"      >/dev/null 2>&1
        fi
    ) &
fi

# ── 启动服务（在 workspace 目录下跑，让 t2s 读对 config.yaml） ────────
cd "$WORKSPACE"
exec t2s serve --host "$HOST" --port "$PORT"
