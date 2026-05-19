#!/usr/bin/env bash
# Bug #5 scenario assertions：
#  A. registry 现在应该已经有 claude_code 条目（生态被运行时识别）
#  B. bridge 目录 ~/.xskill/cc_sessions/ 下应该已有 traj_cc_*.md（ingester 真桥接了）
set -euo pipefail

EXIT=0

echo "--- [assert A] registry now has claude_code"
if ! python3 /rig/probe.py assert_ecosystem_present "$XSKILL_PORT" claude_code; then
    echo "✗ A failed: claude_code still not in registry after waiting"
    EXIT=1
fi

echo "--- [assert B] bridge dir has bridged trajectory file"
if ! python3 /rig/probe.py assert_path_glob "$TESTHOME/.xskill/cc_sessions/traj_cc_*"; then
    echo "✗ B failed: no traj_cc_*.md in bridge dir — ingester未启动 或 未跑过一轮"
    ls -la "$TESTHOME/.xskill/cc_sessions/" 2>&1 || echo "(bridge dir doesn't exist at all)"
    EXIT=1
fi

exit "$EXIT"
