"""scripts/reset_skills_keep_atoms.py
====================================

清空所有 skill 目录 + 重置 traj 状态让 watcher 重跑 cluster + edit。
**保留**已拆好的 atom 数据（这是流水线最贵的部分，不浪费 token）。

操作清单
========
1. 停 daemon (pkill xskill serve)，否则数据库被持锁
2. rm -rf ~/.xskill/skill/* （备份到时间戳目录）
3. 也清 ~/.xskill/skill/.skill_index.pkl（向量索引）
4. 同步清 ~/.claude/skills/* 中由 xskill 装的 symlink/目录（避免 CC 看见旧 skill 残骨）
5. UPDATE trajectories SET status='indexed' WHERE status IN ('done', 'clustering')
6. 重置 last_offset / last_atom_id / tasks_extracted 不动（atom 文件还在，store 自己算）

注意：不动 cc_sessions/*（traj.md + atom_*.json + index.pkl 全留）。

用法::

    python3.11 scripts/reset_skills_keep_atoms.py

    # 或者隔离 home（配合 xskill --debug serve --home /tmp/xskill-debug）：
    python3.11 scripts/reset_skills_keep_atoms.py --target-home /tmp/xskill-debug
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path


XSKILL_HOME = Path.home() / ".xskill"


def stop_daemon() -> None:
    """尽力停掉 xskill serve daemon。"""
    import subprocess
    subprocess.run(["pkill", "-f", "xskill serve"], check=False)
    # 给一点退出时间
    for _ in range(10):
        r = subprocess.run(
            ["pgrep", "-f", "xskill serve"],
            capture_output=True, text=True,
        )
        if not r.stdout.strip():
            return
        time.sleep(0.3)
    # 还活着 → SIGKILL
    subprocess.run(["pkill", "-9", "-f", "xskill serve"], check=False)


def backup_and_clear_skill_dir() -> Path | None:
    """把 ~/.xskill/skill/ 整目录搬到 .wiped-skill-<ts>，重建空目录。

    保留 .skill_index.pkl 也清掉（避免 watcher 启动后用旧索引判定 skill 存在）。
    """
    skill_dir = XSKILL_HOME / "skill"
    if not skill_dir.is_dir():
        return None
    ts = int(time.time())
    backup = XSKILL_HOME / f".wiped-skill-{ts}"
    skill_dir.rename(backup)
    (XSKILL_HOME / "skill").mkdir()
    return backup


def clear_cc_skills_installed_by_xskill(home_root: Path,
                                          purge_all: bool = False) -> tuple[int, int]:
    """清 ~/.claude/skills/ 下由 xskill 装的内容。

    默认行为（安全）：
    - symlink → 删（一定是 xskill 装的，CC 自己不会装 symlink）
    - 普通目录 → 不动（可能是用户手工 skill 或其他工具装的）

    ``purge_all=True``（适合 debug home）：
    - **所有**子目录都删，包括旧 copy install 留下的真实目录。

    返回 ``(deleted_symlinks, deleted_dirs)``。
    """
    cc_skills = home_root / ".claude" / "skills"
    if not cc_skills.is_dir():
        return (0, 0)
    n_sym = 0
    n_dir = 0
    for d in cc_skills.iterdir():
        if d.is_symlink():
            d.unlink()
            n_sym += 1
        elif d.is_dir() and purge_all:
            shutil.rmtree(d)
            n_dir += 1
    # 旧版 install 留下的备份目录 .replaced-by-symlink 顺手清
    for d in cc_skills.glob(".*.replaced-by-symlink"):
        if d.is_dir():
            shutil.rmtree(d)
    return (n_sym, n_dir)


def reset_traj_status() -> tuple[int, int]:
    """把 trajectories 状态从 done / clustering 重置回 indexed。

    返回 (重置数, 总 traj 数)。
    """
    db_path = XSKILL_HOME / "registry.db"
    if not db_path.is_file():
        return (0, 0)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE trajectories SET status='indexed', process_action=NULL, "
            "skill_generated=NULL, error_msg=NULL, retry_count=0 "
            "WHERE status IN ('done', 'clustering', 'error')"
        )
        reset_n = cur.rowcount
        total = conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
        conn.commit()
        return (reset_n, total)
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument(
        "--target-home", type=str, default=None,
        help="清 <target-home>/.claude/skills/ 下 xskill 装的 symlink；"
             "不传则默认清 $HOME/.claude/skills/。配合 --debug 模式时传隔离 home。",
    )
    p.add_argument("--keep-daemon", action="store_true",
                   help="不停 daemon（默认会 pkill）")
    p.add_argument(
        "--purge-cc-skills", action="store_true",
        help="清 CC skills 时把**所有**子目录都删（含旧 copy install 留下的真实目录）。"
             "默认只删 symlink。debug 环境（--target-home）建议加这个。",
    )
    args = p.parse_args()

    home_root = (
        Path(args.target_home).expanduser().resolve()
        if args.target_home else Path.home()
    )

    print(f"target_home (清 CC skills 用): {home_root}")
    print(f"xskill home:                  {XSKILL_HOME}")
    print("")

    if not args.keep_daemon:
        print("[1/4] stop xskill daemon...")
        stop_daemon()
    else:
        print("[1/4] skip daemon stop (--keep-daemon)")

    print("[2/4] backup and clear ~/.xskill/skill/...")
    backup = backup_and_clear_skill_dir()
    if backup:
        print(f"      -> 备份到 {backup}")
    else:
        print("      -> 没有 ~/.xskill/skill/，跳过")

    print(f"[3/4] clean CC skills under {home_root}/.claude/skills/...")
    n_sym, n_dir = clear_cc_skills_installed_by_xskill(
        home_root, purge_all=args.purge_cc_skills,
    )
    if args.purge_cc_skills:
        print(f"      -> 删除 {n_sym} 个 symlink + {n_dir} 个真实目录（--purge-cc-skills）")
    else:
        print(f"      -> 删除 {n_sym} 个 symlink（默认保留真实目录）")

    print("[4/4] reset trajectories status (done/clustering/error → indexed)...")
    reset_n, total = reset_traj_status()
    print(f"      -> {reset_n}/{total} traj 状态重置")

    print("")
    print("done. 现在可以重启 xskill serve；watcher 会从 indexed → clustering 重跑")
    print("cluster + edit 流程，atom 数据完全保留（不重新拆分，省 token）。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
