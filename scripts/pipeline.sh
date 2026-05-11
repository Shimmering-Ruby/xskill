#!/usr/bin/env bash
# =============================================================================
#  xskill 一站式 Demo 脚本
# =============================================================================
#  用法：
#      ./scripts/pipeline.sh [dataset] [max] [workspace]
#
#  例：
#      ./scripts/pipeline.sh                                    # swe_smith_dataset, 5 条, /tmp/t2s_demo
#      ./scripts/pipeline.sh sample_dataset 3                   # 60 条合成冒烟
#      ./scripts/pipeline.sh swe_smith_dataset 10 /tmp/my_run   # 自定义 workspace
#      ./scripts/pipeline.sh dataclaw_dataset 20                # Claude Code 真实会话
#
#  控制执行行为的环境变量：
#      DRY_RUN=1 ./scripts/pipeline.sh ...        # 只打印命令不执行（看看要跑啥）
#      INTERACTIVE=1 ./scripts/pipeline.sh ...    # 每条命令前等回车（最谨慎 / 可逐步跳过）
#      REINDEX=1 ./scripts/pipeline.sh ...        # Step 1 强制 --reindex 重建 meta + index
#
#  本脚本会自动：
#      1. 在 workspace 目录写好 config.yaml（如果不存在 / 或你改过脚本里的 model）
#      2. 软链到仓库的 data/ 目录（复用已下载的 660 条 swe/dataclaw/sample）
#      3. xskill init 建 skill 仓库
#      4. 跑完 index → batch → eval → skill list → candidates 五阶段
#
#  每一条实际执行的 xskill 命令都会先以紫色 "$ ..." 打印出来，方便复制单独重跑。
#  不改环境、不污染仓库；所有产出落在 workspace 目录。
#
# -----------------------------------------------------------------------------
#  代理配置（默认都是空）
# -----------------------------------------------------------------------------
#  如果需要走代理访问 LLM/embedding endpoint，在本脚本的 PROXY 区块改值，
#  或在命令行临时加：
#      HTTPS_PROXY=http://127.0.0.1:7890 ./scripts/pipeline.sh
#
#  httpx >= 0.24 / openai SDK 默认 trust_env=True，会自动读这些变量。
#
#  代理做 MITM（证书链里有自签名 CA）时，会抛 CERTIFICATE_VERIFY_FAILED：
#      T2S_SSL_VERIFY=false ./scripts/pipeline.sh          # 图省事：关掉验证
#      SSL_CERT_FILE=/path/to/proxy-ca.pem ./scripts/...   # 更安全：信任代理 CA
# -----------------------------------------------------------------------------
# NO_PROXY 只豁免 loopback；不要在默认值里写 .volces.com / .bytedance.com ——
# 如果你在字节内网想直连不走代理，自己覆盖 NO_PROXY=.volces.com,... 即可。
# 写死这种内网域名的反面效果：外网用户设了 HTTPS_PROXY 以为能走，结果被
# NO_PROXY 默认里的 .volces.com 反掉，连不上 ARK 报 DNS 错。
: "${HTTP_PROXY:=}"
: "${HTTPS_PROXY:=}"
: "${NO_PROXY:=localhost,127.0.0.1}"
: "${http_proxy:=$HTTP_PROXY}"
: "${https_proxy:=$HTTPS_PROXY}"
: "${no_proxy:=$NO_PROXY}"
export HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy

# -----------------------------------------------------------------------------
#  LLM / Embedding 配置（会写入 workspace/config.yaml）
# -----------------------------------------------------------------------------
#  改模型只需改下面这两个 MODEL 变量。支持任何 OpenAI 兼容 endpoint：
#
#      火山 ARK     https://ark.cn-beijing.volces.com/api/v3
#      OpenAI      https://api.openai.com/v1
#      DeepSeek    https://api.deepseek.com/v1
#      Moonshot    https://api.moonshot.cn/v1
#      vLLM/本地    http://localhost:8000/v1
#
#  当前默认：火山 ARK 的 doubao 系列。注意 mini 版（260M 参数）指令服从性
#  较弱，E2E 测试中会绕过 hard gate 指令；若要 skill 质量更好，换 pro 版本
#  或更大模型（Claude Sonnet / GPT-4o / Qwen3-72B / DeepSeek-V3）。
# -----------------------------------------------------------------------------
# ── 默认 / 索引用 LLM (cheap)：走 mini，给 index 阶段 + 检索等轻量场景
LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
LLM_MODEL="doubao-seed-2-0-mini-260215"
LLM_API_KEY="128aa499-b080-45a1-b0a9-d97ce13a0aaf"

# ── Skill 生成/评测用 LLM (quality)：走 pro，给 agent 生成 skill + 7 维 LLM 打分
# 留空则 fall back 到上面的 LLM_*
SKILL_LLM_BASE_URL=""
SKILL_LLM_MODEL="doubao-seed-2-0-pro-260215"
SKILL_LLM_API_KEY=""

EMBED_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
EMBED_MODEL="doubao-embedding-vision-251215"
EMBED_API_KEY="128aa499-b080-45a1-b0a9-d97ce13a0aaf"
EMBED_DIM=0            # 0 = 自动探测

# sandbox（SWE-bench Docker 沙箱评测）
SANDBOX_ENABLED=false           # 需要 Docker + SWE-smith 镜像；默认关
SANDBOX_MAX_INSTANCES=5
SANDBOX_N_TRIALS=10
SANDBOX_TIMEOUT=300
SANDBOX_TRIGGER_THRESHOLD=3

# candidate buffer 阈值
CANDIDATES_THRESHOLD=3           # supporting_trajs 达到此数自动 promote

# -----------------------------------------------------------------------------
#  流水线数据流
# -----------------------------------------------------------------------------
#      data/<dataset>/traj_*.md + traj_*.json
#             │
#      [1] xskill index
#             │ ── LLM 抽 intent/summary/blockers/tags      → traj_*.md.meta
#             │ ── Embedding                                → index.pkl
#             │
#      [2] xskill batch  (for each traj → process_traj):
#             │
#             ├─ agent (agno) 用工具:
#             │    search_skills             路由：看 frontmatter.description
#             │    search_similar_trajs      找 ≥2 相似成功轨迹
#             │    create_skill              骨架 + SKILL.md + scripts/ + references/
#             │    write_file                写 SKILL.md body（path B 新建时）
#             │    add_candidate             path A 更新走这个（不直接改 SKILL.md）
#             │    list_candidates           看已有候选避免重复
#             │
#             ├─ promote_ready_candidates (自动):
#             │    .candidates.yml 里 supporters >= threshold 的 pattern
#             │    → 内联到 SKILL.md 对应 ## <stage> 段作为 > ⚠️ blockquote
#             │                                     带 "N/M trajectories" 证据
#             │
#             ├─ run_eval (LLM 7 维打分 × n_runs 取中位):
#             │    description_precision / step_actionability / granularity /
#             │    generalizability / warning_coverage / faithfulness /
#             │    structural_quality            → metadata.eval.scores
#             │    eval_score = 7 维平均         → metadata.eval.eval_score
#             │
#             └─ should_merge:
#                   新 skill ≥ 6.0 → merged (保留 commit)
#                   更新 skill > 旧 score → merged
#                   否则 → rejected (git revert)
#
#      [3] eval --list  [4] skill list  [5] cat .candidates.yml
#
# -----------------------------------------------------------------------------
#  关键命令参数速查
# -----------------------------------------------------------------------------
#  xskill index   -c N   --dataset NAME  --all  --no-llm  --reindex  --dry-run
#  xskill batch          --dataset NAME  --max N  --dry-run
#  xskill process PATH   --dry-run
#  xskill eval    --skill NAME  --n-runs N  --sandbox  --no-sandbox  --list
#  xskill skill   list | show NAME | log NAME | diff NAME
#              freeze NAME | unfreeze NAME | delete NAME
#              rollback NAME --to HEAD~1
#              export NAME | import --from skill.tgz
#              migrate [NAME|--all]               # v1→v2 格式迁移
#              promote [--skill NAME|--all] [--threshold N]
#              archive-stale [--days N] [--threshold N]
#  xskill search       -q "…" -k N --filter all|success|failure
#  xskill search-skill -q "…" -k N
#  xskill serve   --port N [--no-ui]                 # 起 HTTP+WebUI
#  xskill init [path]                                # 初始化 skill/ 仓库
#  xskill status                                     # 显示当前配置和 skill 数量
#
# -----------------------------------------------------------------------------
#  路径优先级 (CLI flag > 环境变量 > config.yaml > 默认)
# -----------------------------------------------------------------------------
#      config path    --config       T2S_CONFIG        cwd/config.yaml
#      轨迹目录       --traj-dir     T2S_TRAJ_DIR      config.traj_dir / cwd/data
#      skill 目录     --skill-dir    T2S_SKILL_DIR     config.skill_dir / cwd/skill
#      LLM key        --llm-key      LLM_API_KEY       config.llm.api_key
#      Embed key      —              EMBED_API_KEY     config.embedding.api_key
#      UI 开关        --no-ui        T2S_NO_UI=1       UI 默认开启
#
# -----------------------------------------------------------------------------
#  产出文件
# -----------------------------------------------------------------------------
#      data/<dataset>/*.md.meta               LLM 抽的结构化元数据
#      data/<dataset>/index.pkl               轨迹向量索引
#      skill/<name>/SKILL.md                  v2 skill（frontmatter + body）
#      skill/<name>/.candidates.yml           候选缓冲
#      skill/<name>/scripts/                  可选执行脚本
#      skill/<name>/references/               补充资料 / stale_candidates.md
#      skill/.skill_index.pkl                 skill 向量索引
#      skill/.git/                            skill 仓库 git 历史
#      output/t2s_traj_*_*.log.json           完整结构化事件日志
#
# =============================================================================

set -e

# ── 定位仓库根 ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 参数 ────────────────────────────────────────────────────────────────
DATASET="${1:-swe_smith_dataset}"
MAX="${2:-5}"
WORKSPACE="${3:-/tmp/t2s_demo}"

# 控制执行模式：
#   DRY_RUN=1           只打印命令不执行
#   AUTO=1              打印后不等确认直接跑（默认 — 每条命令前先打印再执行）
#   INTERACTIVE=1       每条命令打印后等你按回车才跑（最谨慎）
DRY_RUN="${DRY_RUN:-0}"
INTERACTIVE="${INTERACTIVE:-0}"

# ── 颜色 ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_HEAD="\033[1;36m"; C_DIM="\033[2m"; C_BOLD="\033[1m"
    C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"
    C_CMD="\033[0;35m"  # 紫色，用于命令回显
    C_OFF="\033[0m"
else
    C_HEAD=""; C_DIM=""; C_BOLD=""; C_OK=""; C_WARN=""; C_ERR=""; C_CMD=""; C_OFF=""
fi

# ── 命令执行封装：打印 → （可选等确认）→ 执行 ─────────────────────────
# 用法：run xskill index --dataset foo -c 10
run() {
    # 把参数拼成一行 shell 安全的命令回显
    local cmd_display="$*"
    echo -e "${C_CMD}\$ $cmd_display${C_OFF}"
    if [ "$DRY_RUN" = "1" ]; then
        echo -e "${C_DIM}  (DRY_RUN=1，跳过执行)${C_OFF}"
        return 0
    fi
    if [ "$INTERACTIVE" = "1" ]; then
        echo -ne "${C_DIM}  ↵ Enter 执行 / Ctrl-C 中止 / s 跳过：${C_OFF}"
        read -r ans
        if [ "$ans" = "s" ] || [ "$ans" = "S" ]; then
            echo -e "${C_DIM}  (跳过)${C_OFF}"
            return 0
        fi
    fi
    "$@"
}

# ── 预检 ────────────────────────────────────────────────────────────────
if ! command -v xskill >/dev/null 2>&1; then
    echo -e "${C_ERR}✗ xskill 命令不存在${C_OFF}"
    echo "  请先安装： cd $REPO_ROOT && pip install -e ."
    exit 1
fi

# 仓库根的 data/ 在 v1 时代是数据集"软链源"，现在已经迁到用户级
# ~/data/xskill_eval/。这段保留是为了兼容 workspace 里已经解压好的
# dataset；download_data.py 现在默认下到 ~/data/xskill_eval/。
if [ ! -d "$REPO_ROOT/data" ] && [ ! -d "$WORKSPACE/data/$DATASET" ]; then
    echo -e "${C_WARN}⚠ 仓库的 data/ 和 workspace 里都没有 $DATASET${C_OFF}"
    echo "  先跑一次 python -m xskill.download_data --mode sample 下载数据"
    echo "  (默认输出: ~/data/xskill_eval/sample_dataset/)"
elif [ ! -d "$REPO_ROOT/data" ]; then
    echo -e "${C_DIM}  仓库的 data/ 不存在（已迁到 ~/data/xskill_eval/），继续用 workspace 里的 $DATASET${C_OFF}"
fi

# ── 准备 workspace ──────────────────────────────────────────────────────
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# 写 config.yaml
# 已存在时：如果脚本里的 LLM_MODEL 和文件里不一致（说明你编辑了脚本换模型），备份并重写
write_config() {
    # 构造 llm_skill 块：只有当 SKILL_* 有值时才输出（否则 fall back 到 llm）
    local skill_block=""
    if [ -n "$SKILL_LLM_MODEL" ] || [ -n "$SKILL_LLM_BASE_URL" ] || [ -n "$SKILL_LLM_API_KEY" ]; then
        skill_block="
# skill 生成 + 7 维 LLM 打分走此覆盖；缺省字段从 llm: 继承
llm_skill:"
        [ -n "$SKILL_LLM_BASE_URL" ] && skill_block="$skill_block
  base_url: \"$SKILL_LLM_BASE_URL\""
        [ -n "$SKILL_LLM_MODEL" ]    && skill_block="$skill_block
  model:    \"$SKILL_LLM_MODEL\""
        [ -n "$SKILL_LLM_API_KEY" ]  && skill_block="$skill_block
  api_key:  \"$SKILL_LLM_API_KEY\""
    fi

    cat > "$WORKSPACE/config.yaml" <<EOF
# Auto-generated by scripts/pipeline.sh
# 编辑此文件可自定义模型、路径、沙箱参数

# 默认 / 索引用 LLM (cheap)
llm:
  base_url: "$LLM_BASE_URL"
  model:    "$LLM_MODEL"
  api_key:  "$LLM_API_KEY"
$skill_block

embedding:
  base_url: "$EMBED_BASE_URL"
  model:    "$EMBED_MODEL"
  api_key:  "$EMBED_API_KEY"
  dim:      $EMBED_DIM

sandbox:
  enabled:           $SANDBOX_ENABLED
  max_instances:     $SANDBOX_MAX_INSTANCES
  n_trials:          $SANDBOX_N_TRIALS
  timeout_per_trial: $SANDBOX_TIMEOUT
  trigger_threshold: $SANDBOX_TRIGGER_THRESHOLD

candidates:
  threshold: $CANDIDATES_THRESHOLD
EOF
}

if [ ! -f "$WORKSPACE/config.yaml" ]; then
    write_config
    echo -e "${C_OK}✓ 写入 $WORKSPACE/config.yaml${C_OFF}"
    echo -e "${C_DIM}    llm.model       = $LLM_MODEL${C_OFF}"
    [ -n "$SKILL_LLM_MODEL" ] && echo -e "${C_DIM}    llm_skill.model = $SKILL_LLM_MODEL${C_OFF}"
else
    # 比对两个 model 字段（llm.model + llm_skill.model）
    cur_models=$(python3 -c "
import yaml, sys
with open('$WORKSPACE/config.yaml') as f:
    d = yaml.safe_load(f) or {}
print(d.get('llm', {}).get('model', ''))
print(d.get('llm_skill', {}).get('model', ''))
" 2>/dev/null)
    cur_llm=$(echo "$cur_models" | sed -n 1p)
    cur_skill=$(echo "$cur_models" | sed -n 2p)
    if [ "$cur_llm" != "$LLM_MODEL" ] || [ "$cur_skill" != "$SKILL_LLM_MODEL" ]; then
        cp "$WORKSPACE/config.yaml" "$WORKSPACE/config.yaml.bak.$(date +%s)"
        write_config
        echo -e "${C_WARN}⚠ 脚本与 workspace 的 model 不一致，已备份旧 config 并重写${C_OFF}"
        echo -e "${C_WARN}    旧: llm=$cur_llm  llm_skill=$cur_skill${C_OFF}"
        echo -e "${C_WARN}    新: llm=$LLM_MODEL  llm_skill=$SKILL_LLM_MODEL${C_OFF}"
    else
        echo -e "${C_DIM}  复用已有的 $WORKSPACE/config.yaml${C_OFF}"
        echo -e "${C_DIM}    llm.model       = $cur_llm${C_OFF}"
        [ -n "$cur_skill" ] && echo -e "${C_DIM}    llm_skill.model = $cur_skill${C_OFF}"
    fi
fi

# 准备 data/ —— 每个数据集独立软链到仓库，支持增量
# 如果 workspace/data 是真目录（上次跑残留的散装 traj），保留；只在里面补软链目标数据集
mkdir -p "$WORKSPACE/data"
link_dataset() {
    local ds="$1"
    local target="$REPO_ROOT/data/$ds"
    local link="$WORKSPACE/data/$ds"
    if [ -L "$link" ]; then
        # 已是软链，确认指向正确
        if [ "$(readlink "$link")" != "$target" ]; then
            ln -sfn "$target" "$link"
            echo -e "${C_OK}✓ 更新软链 data/$ds → $target${C_OFF}"
        fi
    elif [ -d "$link" ]; then
        echo -e "${C_DIM}  复用 data/$ds (非软链，$(ls "$link" 2>/dev/null | wc -l) 个文件)${C_OFF}"
    elif [ -d "$target" ]; then
        ln -sfn "$target" "$link"
        echo -e "${C_OK}✓ 软链 data/$ds → $target${C_OFF}"
    fi
}

# 自动把仓库里所有可能的数据集都软链进来（方便切换 dataset 参数不用重跑脚本）
if [ -d "$REPO_ROOT/data" ]; then
    for ds_path in "$REPO_ROOT/data"/*/; do
        [ -d "$ds_path" ] || continue
        ds_name=$(basename "$ds_path")
        # 跳过 raw/ 和软链自己
        [ "$ds_name" = "raw" ] && continue
        link_dataset "$ds_name"
    done
fi

# 检查目标 dataset 存在
if [ ! -d "$WORKSPACE/data/$DATASET" ]; then
    echo -e "${C_ERR}✗ data/$DATASET 不存在${C_OFF}"
    echo "  workspace 下可用 dataset："
    for d in "$WORKSPACE/data"/*/; do
        [ -d "$d" ] || continue
        n=$(basename "$d")
        count=$(ls "$d"/*.md 2>/dev/null | grep -v '\.meta$' | wc -l)
        echo "    $n ($count 条)"
    done
    echo
    echo "  仓库下可用 dataset："
    for d in "$REPO_ROOT/data"/*/; do
        [ -d "$d" ] || continue
        n=$(basename "$d")
        [ "$n" = "raw" ] && continue
        count=$(ls "$d"/*.md 2>/dev/null | grep -v '\.meta$' | wc -l)
        echo "    $n ($count 条)"
    done
    exit 1
fi

# 初始化 skill 仓库（已存在则跳过）
if [ ! -d "$WORKSPACE/skill/.git" ]; then
    run xskill init
else
    echo -e "${C_DIM}  复用已有的 skill/ 仓库（$(cd "$WORKSPACE/skill" && git log --oneline 2>/dev/null | wc -l) 个 commit）${C_OFF}"
fi

# ── 打印摘要 ────────────────────────────────────────────────────────────
echo
echo -e "${C_HEAD}════════════════════════════════════════════════════════════════${C_OFF}"
echo -e "${C_HEAD}  xskill 一站式 Demo${C_OFF}"
echo -e "${C_HEAD}════════════════════════════════════════════════════════════════${C_OFF}"
echo -e "  workspace         ${C_BOLD}$WORKSPACE${C_OFF}"
echo -e "  dataset           ${C_BOLD}$DATASET${C_OFF} (max=${C_BOLD}$MAX${C_OFF} 条)"
echo -e "  llm.model         ${C_BOLD}$LLM_MODEL${C_OFF}  ${C_DIM}(index / 默认)${C_OFF}"
if [ -n "$SKILL_LLM_MODEL" ]; then
    echo -e "  llm_skill.model   ${C_BOLD}$SKILL_LLM_MODEL${C_OFF}  ${C_DIM}(agent 生成 skill + 7 维打分)${C_OFF}"
else
    echo -e "  llm_skill.model   ${C_DIM}<未配置，fall back 到 llm.model>${C_OFF}"
fi
echo -e "  embed.model       ${C_BOLD}$EMBED_MODEL${C_OFF}"
echo -e "  HTTPS_PROXY       ${C_BOLD}${HTTPS_PROXY:-<空>}${C_OFF}"
echo -e "  HTTP_PROXY        ${C_BOLD}${HTTP_PROXY:-<空>}${C_OFF}"
echo -e "  NO_PROXY          ${C_DIM}$NO_PROXY${C_OFF}"
# 执行模式
mode=""
[ "$DRY_RUN" = "1" ]      && mode="${mode}${C_WARN}DRY_RUN${C_OFF} "
[ "$INTERACTIVE" = "1" ]  && mode="${mode}${C_WARN}INTERACTIVE${C_OFF} "
[ "${REINDEX:-0}" = "1" ] && mode="${mode}${C_WARN}REINDEX${C_OFF} "
[ -z "$mode" ]            && mode="${C_DIM}normal (命令打印 + 直接执行)${C_OFF}"
echo -e "  exec mode         $mode"
echo -e "${C_DIM}  提示：命令会在执行前先打印出来（紫色 \$ 行），方便你逐条检查${C_OFF}"
echo

# ── [1] 建索引 ──────────────────────────────────────────────────────────
echo -e "${C_HEAD}━━━ [1/5] Indexing trajectories from $DATASET ━━━${C_OFF}"
echo -e "${C_DIM}  预计耗时: ~5-10 min / 300 条 (并发 10)${C_OFF}"
echo -e "${C_DIM}  已有 .meta 会跳过；想强制重建加 REINDEX=1 env var${C_OFF}"
if [ "${REINDEX:-0}" = "1" ]; then
    run xskill index --reindex --dataset "$DATASET" -c 10
else
    run xskill index --dataset "$DATASET" -c 10
fi
echo

# ── [2] 批处理 ──────────────────────────────────────────────────────────
echo -e "${C_HEAD}━━━ [2/5] Batch processing (max=$MAX) ━━━${C_OFF}"
echo -e "${C_DIM}  预计耗时: ~5-7 min / 条 x $MAX 条 (LLM 串行)${C_OFF}"
echo -e "${C_DIM}  想只看 agent 推理不写入：改为调用 xskill batch --dry-run${C_OFF}"
run xskill batch --dataset "$DATASET" --max "$MAX"
echo

# ── [3] 评测汇总 ────────────────────────────────────────────────────────
echo -e "${C_HEAD}━━━ [3/5] Eval summary (all skills) ━━━${C_OFF}"
run xskill eval --list
echo

# ── [4] Skill 列表 ──────────────────────────────────────────────────────
echo -e "${C_HEAD}━━━ [4/5] Skills produced ━━━${C_OFF}"
run xskill skill list
echo

# ── [5] Candidate 缓冲 ──────────────────────────────────────────────────
echo -e "${C_HEAD}━━━ [5/5] Candidate buffers (pending promotion) ━━━${C_OFF}"
echo -e "${C_DIM}  supporting_trajs >= $CANDIDATES_THRESHOLD 时自动 promote${C_OFF}"
echo -e "${C_DIM}  手动 flush:   xskill skill promote --all --threshold 2${C_OFF}"
echo
SKILL_DIR="$WORKSPACE/skill"
if [ -d "$SKILL_DIR" ]; then
    found=0
    for d in "$SKILL_DIR"/*/; do
        [ -d "$d" ] || continue
        name=$(basename "$d")
        if [ -f "$d/.candidates.yml" ]; then
            found=1
            echo -e "${C_BOLD}→ $name${C_OFF}"
            sed 's/^/    /' < "$d/.candidates.yml"
            echo
        fi
    done
    if [ $found -eq 0 ]; then
        echo -e "${C_DIM}  (无候选缓冲，表示 agent 全部走 path B 新建 skill，未走 path A 更新)${C_OFF}"
    fi
fi
echo

# ── 结尾提示 ────────────────────────────────────────────────────────────
echo -e "${C_OK}✓ 流水线完成${C_OFF}"
echo
echo -e "${C_DIM}下一步可做：${C_OFF}"
echo -e "${C_DIM}  单 skill 详情    (cd $WORKSPACE && xskill skill show <name>)${C_OFF}"
echo -e "${C_DIM}  重评             (cd $WORKSPACE && xskill eval --skill <name> --n-runs 5)${C_OFF}"
echo -e "${C_DIM}  降阈值 promote   (cd $WORKSPACE && xskill skill promote --all --threshold 2)${C_OFF}"
echo -e "${C_DIM}  前端演示         (cd $WORKSPACE && xskill serve --port 8000)  → 浏览器开 http://localhost:8000${C_OFF}"
echo -e "${C_DIM}  看 git 历史      (cd $WORKSPACE/skill && git log --all --oneline)${C_OFF}"
echo -e "${C_DIM}  重跑 (保留产出)  ./scripts/pipeline.sh $DATASET $MAX $WORKSPACE${C_OFF}"
echo -e "${C_DIM}  全新 workspace   rm -rf $WORKSPACE && ./scripts/pipeline.sh${C_OFF}"
