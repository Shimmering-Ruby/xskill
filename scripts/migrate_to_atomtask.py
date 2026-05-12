"""scripts/migrate_to_atomtask.py —— 清理旧 traj-level 元数据
=============================================================

一次性删除：
- ``traj_*.md.meta``：旧流水线给每条 traj 抽的 XML meta（intent/summary/tags 等）
- ``index.pkl``：旧 traj-level 向量索引（顶层 + 子目录都扫；新 atom 索引会
  在 watcher 重启后自动重建在 ``<root>/index.pkl``）

保留：
- ``traj_*.md``：真实轨迹源
- ``traj_*.json``：用户 / CC bridge 写的原始元数据（含 session_id 等）

按 CLAUDE.md 第 4 条：不做配置兼容。运行后 watcher 重启会按新状态机重新从
``discovered`` 走到 ``splitting``。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def nuke_legacy(root: Path) -> dict:
    """递归删除 ``traj_*.md.meta`` 和 ``index.pkl``。

    Returns:
        ``{"meta": <count>, "index": <count>}`` 实际删除的文件数。
    """
    root = Path(root)
    removed = {"meta": 0, "index": 0}
    if not root.is_dir():
        return removed
    for p in root.rglob("traj_*.md.meta"):
        p.unlink()
        removed["meta"] += 1
    for p in root.rglob("index.pkl"):
        p.unlink()
        removed["index"] += 1
    return removed


def list_legacy(root: Path) -> dict:
    """统计将被删除的文件数，但不实际删除。"""
    root = Path(root)
    if not root.is_dir():
        return {"meta": 0, "index": 0}
    return {
        "meta": sum(1 for _ in root.rglob("traj_*.md.meta")),
        "index": sum(1 for _ in root.rglob("index.pkl")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="目录路径（例如 ~/.xskill/cc_sessions）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计数不实际删除")
    args = parser.parse_args()
    root = Path(args.root).expanduser()

    if args.dry_run:
        out = list_legacy(root)
        print(f"would remove {out['meta']} .md.meta and {out['index']} index.pkl "
              f"under {root}")
        return 0

    out = nuke_legacy(root)
    print(f"removed {out['meta']} .md.meta and {out['index']} index.pkl "
          f"under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
