#!/usr/bin/env bash
# =============================================================================
#  pack_demo.sh —— 维护者脚本：从 data/swe_smith_dataset 取轨迹，打包成
#  scripts/demo/trajectories.zip，供 run_demo.sh 解压使用。
#
#  用法：
#      ./scripts/demo/pack_demo.sh           # 默认打包全部（所有 traj_*.md + .json）
#      ./scripts/demo/pack_demo.sh 20        # 只打前 20 条
#      ./scripts/demo/pack_demo.sh all       # 显式表示全部
#
#  只打包 *.md + *.json 原始输入，**不包含**:
#      - *.md.meta         LLM 抽取的结构化 meta，会随模型漂移；接收方首次
#                          index 时由他们自己的 LLM 重新生成
#      - index.pkl         向量索引；接收方首次 index 时用自己的 embedding 重建
#
#  这样打出来的 zip 只含"原料"，和接收方的 LLM/embedding 模型解耦。
# =============================================================================
set -e

N_ARG="${1:-all}"
if [ "$N_ARG" = "all" ] || [ "$N_ARG" = "ALL" ]; then
    N=-1
else
    N="$N_ARG"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SRC="$REPO_ROOT/data/swe_smith_dataset"
OUT_ZIP="$SCRIPT_DIR/trajectories.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [ ! -d "$SRC" ]; then
    echo "✗ 源数据不存在：$SRC"
    echo "  先下载：python -m xskill.download_data --mode swe_smith"
    exit 1
fi

stage_dir="$STAGE/swe_smith_demo"
mkdir -p "$stage_dir"

count=0
for md in $(ls "$SRC"/traj_*.md 2>/dev/null | grep -v '\.meta$' | sort); do
    [ "$N" -ge 0 ] && [ $count -ge "$N" ] && break
    base="${md%.md}"
    json="${base}.json"
    # 必须同时有 md 和 json，才视为一条完整输入
    if [ -f "$md" ] && [ -f "$json" ]; then
        cp "$md"   "$stage_dir/"
        cp "$json" "$stage_dir/"
        count=$((count + 1))
    fi
done

if [ $count -eq 0 ]; then
    echo "✗ 没有找到可打包的轨迹"
    exit 1
fi

# 打 zip（相对 STAGE 做压缩，解压后就是 swe_smith_demo/traj_*.{md,json}）
# 优先用 `zip`，没有则 fallback 到 python3 -m zipfile
rm -f "$OUT_ZIP"
if command -v zip >/dev/null 2>&1; then
    ( cd "$STAGE" && zip -qr "$OUT_ZIP" swe_smith_demo )
else
    python3 - "$STAGE" "$OUT_ZIP" <<'PYEOF'
import sys, zipfile, pathlib
stage, out = sys.argv[1], sys.argv[2]
root = pathlib.Path(stage)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            z.write(p, p.relative_to(root))
PYEOF
fi

size=$(du -sh "$OUT_ZIP" | awk '{print $1}')
echo "✓ 打包完成：$OUT_ZIP"
echo "    trajectories: $count"
echo "    size:         $size"
echo "    层级:          swe_smith_demo/traj_*.{md,json}   (不含 meta / index.pkl)"
