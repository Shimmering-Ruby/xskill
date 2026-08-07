"""预计算推荐结果表：重活进程写，/sync 只读。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from xskill.pipeline.registry import pooled_connection

logger = logging.getLogger("xskill.recommend_store")


def mark_recommend_dirty(
    user_key: str,
    *,
    reason: str = "",
    db_path: Optional[Path] = None,
) -> None:
    if not user_key:
        return
    with pooled_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recommend_dirty(user_key, reason, marked_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_key) DO UPDATE SET
                reason=excluded.reason,
                marked_at=datetime('now')
            """,
            (user_key, reason),
        )
        conn.execute(
            """
            UPDATE client_recommend_slots SET stale=1
            WHERE user_key=?
            """,
            (user_key,),
        )
        conn.commit()


def mark_all_recommend_dirty(
    *,
    reason: str = "catalog_changed",
    db_path: Optional[Path] = None,
) -> int:
    """技能全集变化且无法细粒度归因时：标所有已有推荐行脏。"""
    with pooled_connection(db_path) as conn:
        conn.execute(
            "UPDATE client_recommend_slots SET stale=1"
        )
        rows = conn.execute(
            "SELECT user_key FROM client_recommend_slots"
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO recommend_dirty(user_key, reason, marked_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_key) DO UPDATE SET
                    reason=excluded.reason,
                    marked_at=datetime('now')
                """,
                (row["user_key"], reason),
            )
        conn.commit()
        return len(rows)


def list_dirty_user_keys(
    *,
    limit: int = 64,
    db_path: Optional[Path] = None,
) -> list[str]:
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT user_key FROM recommend_dirty
            ORDER BY marked_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [r["user_key"] for r in rows]


def clear_recommend_dirty(
    user_key: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    with pooled_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM recommend_dirty WHERE user_key=?",
            (user_key,),
        )
        conn.commit()


def save_recommend_slots(
    user_key: str,
    skill_names: list[str],
    *,
    fingerprint: str = "",
    db_path: Optional[Path] = None,
) -> None:
    payload = json.dumps(list(skill_names), ensure_ascii=False)
    with pooled_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO client_recommend_slots(
                user_key, slots_json, fingerprint, computed_at, stale
            ) VALUES (?, ?, ?, datetime('now'), 0)
            ON CONFLICT(user_key) DO UPDATE SET
                slots_json=excluded.slots_json,
                fingerprint=excluded.fingerprint,
                computed_at=datetime('now'),
                stale=0
            """,
            (user_key, payload, fingerprint),
        )
        conn.execute(
            "DELETE FROM recommend_dirty WHERE user_key=?",
            (user_key,),
        )
        conn.commit()


def load_recommend_slots(
    user_key: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[list[str]]:
    """读推荐结果；过期也返回上一份（stale 不阻断）。无行则 None。"""
    if not user_key:
        return None
    with pooled_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT slots_json FROM client_recommend_slots
            WHERE user_key=?
            """,
            (user_key,),
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["slots_json"] or "[]")
    except json.JSONDecodeError:
        logger.warning("bad slots_json for user_key=%s", user_key)
        return None
    if not isinstance(data, list):
        return None
    return [str(x) for x in data]
