#!/usr/bin/env python3
"""backfill_user_key.py — P2-2.1 存量归因一次性回填（D5，跑完即弃）

把 ``trajectories.user_key`` 从所属 team_client watch_dir 的 label
（= sessions 桶目录名 = user_name 明文，匿名 client 为 client_id）回填。
非 team 目录的轨迹 user_key 留空（'(local)' 语义由聚合层给出）。

用法（server 机器上跑一次）::

    python scripts/backfill_user_key.py            # 默认 ~/.xskill 注册库
    python scripts/backfill_user_key.py /path/to/registry.db

幂等：只回填 user_key 为空的行，重复跑无副作用。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xskill.pipeline.registry import get_connection  # noqa: E402


def backfill(db_path: Path | None = None) -> dict:
    conn = get_connection(db_path)  # get_connection 自带 user_key 列迁移
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM trajectories"
            " WHERE COALESCE(user_key,'')=''"
        ).fetchone()[0]
        conn.execute(
            "UPDATE trajectories SET user_key = ("
            "  SELECT w.label FROM watch_dirs w WHERE w.id = trajectories.watch_dir_id"
            "    AND COALESCE(w.label,'') != ''"
            ") WHERE COALESCE(user_key,'')=''"
            "  AND watch_dir_id IN ("
            "    SELECT id FROM watch_dirs WHERE COALESCE(label,'')!='')"
        )
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) FROM trajectories"
            " WHERE COALESCE(user_key,'')=''"
        ).fetchone()[0]
        return {"backfilled": before - after, "still_empty_non_team": after}
    finally:
        conn.close()


if __name__ == "__main__":
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = backfill(db)
    print(f"回填 {result['backfilled']} 行;"
          f" 剩余 user_key 为空(非 team 轨迹,预期): {result['still_empty_non_team']}")
