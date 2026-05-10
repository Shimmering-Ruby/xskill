#!/usr/bin/env bash
# =============================================================================
#  run_demo.sh —— xskill 端到端演示一键包
# =============================================================================
#  这份脚本 + 同目录的 trajectories.zip 让你零配置跑完整个 pipeline：
#  index → batch (LLM 蒸 skill) → eval → skill list → candidates。
#
#  用法：
#      ./scripts/demo/run_demo.sh                          # 默认 max=3, workspace=/tmp/t2s_demo
#      ./scripts/demo/run_demo.sh 5                        # 处理 5 条
#      ./scripts/demo/run_demo.sh 5 /tmp/my_demo           # 指定 workspace
#
#  环境变量：
#      SKIP_INSTALL=1         已经 pip install -e . 过，跳过自动安装
#      DRY_RUN=1              只回显命令（透传给 pipeline.sh）
#      INTERACTIVE=1          每条命令前等回车（透传给 pipeline.sh）
#
#  自定义 LLM key / model：
#      直接编辑 scripts/pipeline.sh 顶部的 LLM_*/SKILL_LLM_*/EMBED_* 变量，
#      或跑完一次后编辑 $WORKSPACE/config.yaml。
#
#  流程：
#      1. 确认 xskill 已安装（没有就在仓库根 pip install -e .）
#      2. 解压 trajectories.zip → $WORKSPACE/data/swe_smith_demo/
#      3. 调用 scripts/pipeline.sh 跑 5 阶段流水线
#         （pipeline.sh 会写 config.yaml、xskill init、index、batch、eval、skill list）
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIPELINE="$REPO_ROOT/scripts/pipeline.sh"
ZIP_FILE="$SCRIPT_DIR/trajectories.zip"

MAX="${1:-3}"
WORKSPACE="${2:-/tmp/t2s_demo}"
DATASET="swe_smith_demo"

# ── 颜色 ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_HEAD="\033[1;36m"; C_DIM="\033[2m"; C_BOLD="\033[1m"
    C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"; C_OFF="\033[0m"
else
    C_HEAD=""; C_DIM=""; C_BOLD=""; C_OK=""; C_WARN=""; C_ERR=""; C_OFF=""
fi

banner() { echo -e "${C_HEAD}━━━ $* ━━━${C_OFF}"; }

# ── 预检 ────────────────────────────────────────────────────────────────
[ -f "$ZIP_FILE" ] || { echo -e "${C_ERR}✗ 缺少 $ZIP_FILE —— 先跑 ./scripts/demo/pack_demo.sh${C_OFF}"; exit 1; }
[ -f "$PIPELINE" ] || { echo -e "${C_ERR}✗ 找不到 $PIPELINE${C_OFF}"; exit 1; }
command -v unzip >/dev/null 2>&1 || { echo -e "${C_ERR}✗ 需要 unzip（apt install unzip）${C_OFF}"; exit 1; }

# ── 1. 安装 xskill ─────────────────────────────────────────────────────────
banner "1/3 确认 xskill 已安装"
if ! command -v xskill >/dev/null 2>&1; then
    if [ "${SKIP_INSTALL:-0}" = "1" ]; then
        echo -e "${C_ERR}✗ SKIP_INSTALL=1 但 xskill 不在 PATH${C_OFF}"; exit 1
    fi
    echo -e "${C_DIM}  未检测到 xskill，从 $REPO_ROOT 执行 pip install -e .${C_OFF}"
    ( cd "$REPO_ROOT" && pip install -e . )
else
    echo -e "${C_DIM}  已安装：$(command -v xskill)${C_OFF}"
fi

# ── 2. 准备 workspace + 解压轨迹 ───────────────────────────────────────
banner "2/3 解压演示轨迹 → $WORKSPACE/data/$DATASET"
mkdir -p "$WORKSPACE/data"
target_dir="$WORKSPACE/data/$DATASET"

if [ -d "$target_dir" ] && [ -n "$(ls -A "$target_dir" 2>/dev/null)" ]; then
    count=$(ls "$target_dir"/*.md 2>/dev/null | grep -v meta | wc -l)
    echo -e "${C_DIM}  已存在 $target_dir（$count 条），跳过解压${C_OFF}"
else
    rm -rf "$target_dir"
    # zip 顶层目录就是 swe_smith_demo/，解压到 $WORKSPACE/data 即可
    unzip -q "$ZIP_FILE" -d "$WORKSPACE/data/"
    count=$(ls "$target_dir"/*.md 2>/dev/null | grep -v meta | wc -l)
    echo -e "${C_OK}✓ 解压 $count 条轨迹${C_OFF}"
fi

# ── 3. 调用 pipeline.sh ────────────────────────────────────────────────
banner "3/3 进入主流水线：pipeline.sh $DATASET $MAX $WORKSPACE"
echo -e "${C_DIM}  pipeline.sh 会顺序执行 init → index → batch → eval → skill list → candidates${C_OFF}"
echo -e "${C_DIM}  LLM key 已在 pipeline.sh 顶部写好默认值，改模型/key 直接编辑该脚本${C_OFF}"
echo

"$PIPELINE" "$DATASET" "$MAX" "$WORKSPACE"

echo
echo -e "${C_OK}✓ 端到端演示完成${C_OFF}"
echo -e "${C_DIM}  workspace: $WORKSPACE${C_OFF}"
echo -e "${C_DIM}  Web UI：   (cd $WORKSPACE && xskill serve --port 8000) → http://localhost:8000${C_OFF}"
echo -e "${C_DIM}  skill 详情：(cd $WORKSPACE && xskill skill show <name>)${C_OFF}"
echo -e "${C_DIM}  清理重跑：  rm -rf $WORKSPACE && $0 $MAX $WORKSPACE${C_OFF}"
