#!/usr/bin/env bash
# =============================================================================
#  pack_demo.sh —— 维护者用脚本：从 data/swe_smith_dataset 取前 N 条，打包成
#  scripts/demo/trajectories.zip，供 run_demo.sh 解压使用。
#
#  用法：
#      ./scripts/demo/pack_demo.sh           # 默认打包 10 条
#      ./scripts/demo/pack_demo.sh 20        # 打包 20 条
#
#  只把 *.md / *.json / *.md.meta 三种文件打进 zip，不包含 index.pkl
#  （index.pkl 会在 run_demo 阶段重新生成，体积更小 & 与用户环境模型对齐）。
# =============================================================================
set -e

N="${1:-10}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SRC="$REPO_ROOT/data/swe_smith_dataset"
OUT_ZIP="$SCRIPT_DIR/trajectories.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [ ! -d "$SRC" ]; then
    echo "✗ 源数据不存在：$SRC"
    echo "  先下载：python -m traj2skill.download_data --mode swe_smith"
    exit 1
fi

# 取前 N 条 traj_NNNN 序号；每条需要同时存在 md 和 json
stage_dir="$STAGE/swe_smith_demo"
mkdir -p "$stage_dir"

count=0
for md in $(ls "$SRC"/traj_*.md 2>/dev/null | grep -v '\.meta$' | sort); do
    [ $count -ge $N ] && break
    base="${md%.md}"
    name=$(basename "$base")
    json="${base}.json"
    meta="${md}.meta"
    # md + json 是必须；meta 可选（index 阶段会补）
    if [ -f "$md" ] && [ -f "$json" ]; then
        cp "$md"   "$stage_dir/"
        cp "$json" "$stage_dir/"
        [ -f "$meta" ] && cp "$meta" "$stage_dir/"
        count=$((count + 1))
    fi
done

if [ $count -eq 0 ]; then
    echo "✗ 没有找到可打包的轨迹"
    exit 1
fi

# 打 zip（相对 STAGE 做压缩，解压后就是 swe_smith_demo/traj_*.md…）
# 优先用 `zip`，没有则 fallback 到 python3 -m zipfile
rm -f "$OUT_ZIP"
if command -v zip >/dev/null 2>&1; then
    ( cd "$STAGE" && zip -qr "$OUT_ZIP" swe_smith_demo )
else
    python3 - "$STAGE" "$OUT_ZIP" <<'PYEOF'
import os, sys, zipfile, pathlib
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
echo "    层级:          swe_smith_demo/traj_*.{md,json,md.meta}"
