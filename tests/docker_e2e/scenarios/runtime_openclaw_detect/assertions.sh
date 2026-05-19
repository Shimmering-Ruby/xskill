#!/usr/bin/env bash
# runtime_openclaw_detect assertions：
#  A. registry 已有 openclaw 条目
#  B. bridge dir ~/.xskill/openclaw_sessions/ 下有 traj_oc_*.md（ingester 桥通了）
#  C. ~/.agents/skills/demo-skill/SKILL.md 存在（startup install_all_to_openclaw 跑通）
set -euo pipefail

EXIT=0

echo "--- [assert A] registry now has openclaw"
if ! python3 /rig/probe.py assert_ecosystem_present "$XSKILL_PORT" openclaw; then
    echo "✗ A failed: openclaw still not in registry after waiting"
    EXIT=1
fi

echo "--- [assert B] bridge dir has bridged trajectory file"
if ! python3 /rig/probe.py assert_path_glob "$TESTHOME/.xskill/openclaw_sessions/traj_oc_*"; then
    echo "✗ B failed: no traj_oc_*.md in bridge dir — ingester 未启动或未跑过一轮"
    ls -la "$TESTHOME/.xskill/openclaw_sessions/" 2>&1 || echo "(bridge dir doesn't exist at all)"
    EXIT=1
fi

echo "--- [assert C] startup install_all_to_openclaw landed demo-skill"
if ! python3 /rig/probe.py assert_path_exists "$TESTHOME/.agents/skills/demo-skill/SKILL.md"; then
    echo "✗ C failed: demo-skill not installed to ~/.agents/skills/"
    ls -la "$TESTHOME/.agents/skills/" 2>&1 || echo "(skills dir doesn't exist)"
    EXIT=1
fi

exit "$EXIT"
