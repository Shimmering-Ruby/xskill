#!/usr/bin/env bash
# 构镜像：先确保本地 dist/*.whl 存在（make e2e 默认会调它），然后 docker build
# 把 wheel 注入。镜像 tag：xskill-e2e:local。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

# 1. 比对源码 mtime 和 wheel mtime；若任意 src/xskill/*.py 比最旧的 wheel 新，
#    或 dist/ 为空，则重新 build。否则用户改了 watcher.py / server.py 但 wheel
#    还是旧的 → docker image 装进去也是旧逻辑（Bug #5 fix 看不到），坑很深。
need_rebuild=0
if ! ls dist/xskill-*.whl >/dev/null 2>&1; then
    need_rebuild=1
    echo "=== [build] no wheel in dist/"
else
    wheel_mtime=$(stat -c %Y dist/xskill-*.whl 2>/dev/null | sort -n | head -1)
    newest_src=$(find src/xskill -name '*.py' -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
    if [ -n "$newest_src" ] && [ "$newest_src" -gt "$wheel_mtime" ]; then
        need_rebuild=1
        echo "=== [build] src/xskill/*.py newer than dist/*.whl — rebuilding"
    fi
fi
if [ "$need_rebuild" -eq 1 ]; then
    rm -f dist/xskill-*.whl dist/xskill-*.tar.gz
    python3.11 -m build --wheel >/dev/null
    echo "=== [build] wheel rebuilt: $(ls dist/xskill-*.whl)"
fi

echo "=== [build] docker build -t xskill-e2e:local ..."
# Build context 必须包含 dist/ 和 tests/docker_e2e/rig/
docker build \
    -f tests/docker_e2e/rig/Dockerfile \
    -t xskill-e2e:local \
    .
echo "=== [build] done"
