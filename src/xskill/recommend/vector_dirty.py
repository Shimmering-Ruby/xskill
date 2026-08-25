"""skills_catalog → 向量索引的持久化增量队列。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

from xskill.pipeline.registry import pooled_connection


def mark_catalog_vector_dirty_on_connection(
    conn,
    catalog_key: str,
    *,
    operation: str,
    content_sha: str = "",
    marked_at: float | None = None,
) -> None:
    """在调用方事务中合并一个目标状态，并递增 generation。"""
    if operation not in {"upsert", "delete"}:
        raise ValueError(f"invalid catalog vector operation: {operation!r}")
    conn.execute(
        """
        INSERT INTO catalog_vector_dirty(
            catalog_key, generation, dirty, operation, content_sha, marked_at
        ) VALUES (?, 1, 1, ?, ?, ?)
        ON CONFLICT(catalog_key) DO UPDATE SET
            generation=catalog_vector_dirty.generation + 1,
            dirty=1,
            operation=excluded.operation,
            content_sha=excluded.content_sha,
            marked_at=excluded.marked_at
        """,
        (catalog_key, operation, content_sha, time.time() if marked_at is None else marked_at),
    )


def list_catalog_vector_dirty(
    *,
    db_path: Optional[Path] = None,
    limit: int = 256,
) -> list[dict]:
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT catalog_key, generation, operation, content_sha, marked_at
            FROM catalog_vector_dirty
            WHERE dirty=1
            ORDER BY marked_at, catalog_key
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


def count_catalog_vector_dirty(*, db_path: Optional[Path] = None) -> int:
    """当前积压的脏项数——用来判断「要不要播种全量对账」和向状态文件报告进度。"""
    with pooled_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_vector_dirty WHERE dirty=1"
        ).fetchone()
    return int(row["n"]) if row else 0


def _row_indexable_from_light_columns(
    *, content_sha: str, source: str, distributable, retired,
) -> bool:
    """``catalog_row_is_indexable`` 的窄列版本：只用 ``content_sha`` 的
    非空性代表「有正文」，不读 description 原文——避免播种全量对账时把
    全表文本一次性摊进内存（issue #328）。``content_sha`` 只在
    description 非空时才非空（见 ``catalog_store.py`` 写入逻辑），可以
    安全地当「有没有正文」的代理，不需要重复两边的判定逻辑本体。
    """
    if retired:
        return False
    if not (content_sha or "").strip():
        return False
    return source == "skillhub" or bool(
        distributable if distributable is not None else 1
    )


def seed_full_catalog_vector_sweep(
    *,
    db_path: Optional[Path] = None,
    existing_index_keys: Iterable[str] = (),
) -> dict:
    """把当前全部可索引 catalog 行标脏，交给增量消费循环分批处理（issue #328）。

    调用方只应在「没有正在进行中的积压」时播种一次——重复播种会把已经
    处理过、已经从脏表清掉的 key 重新标脏，抹掉上一轮已经取得的进度。
    之后每轮只消费一批（``limit`` 条），天然把一次性的全量重建拆成多轮，
    不会因为没装持久索引就在单轮里把整份 catalog 的正文和向量都摊进内存。

    ``existing_index_keys``：当前索引里已有、但按最新 catalog 状态已不
    再可索引（被 retire、被删）的 key 会被标记 delete，交由消费循环把
    它们从索引里清掉，行为与旧的全量对账一致。
    """
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.catalog_key, c.content_sha, c.source, c.distributable,
                   CASE WHEN l.state='retired' THEN 1 ELSE 0 END AS retired
            FROM skills_catalog AS c
            LEFT JOIN skill_lifecycle AS l ON l.skill_name=c.name
            """
        ).fetchall()
        indexable_keys: set[str] = set()
        seeded_upsert = 0
        for row in rows:
            if not _row_indexable_from_light_columns(
                content_sha=row["content_sha"], source=row["source"],
                distributable=row["distributable"], retired=row["retired"],
            ):
                continue
            indexable_keys.add(row["catalog_key"])
            mark_catalog_vector_dirty_on_connection(
                conn, row["catalog_key"], operation="upsert",
                content_sha=row["content_sha"] or "",
            )
            seeded_upsert += 1
        stale = set(existing_index_keys) - indexable_keys
        for catalog_key in stale:
            mark_catalog_vector_dirty_on_connection(
                conn, catalog_key, operation="delete",
            )
        conn.commit()
    return {
        "total_indexable": len(indexable_keys),
        "seeded_upsert": seeded_upsert,
        "seeded_delete": len(stale),
    }


def list_all_catalog_vector_generations(
    *, db_path: Optional[Path] = None,
) -> dict[str, int]:
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT catalog_key, generation FROM catalog_vector_dirty
            WHERE dirty=1
            """
        ).fetchall()
    return {row["catalog_key"]: int(row["generation"]) for row in rows}


def catalog_vector_event_is_current(
    catalog_key: str,
    generation: int,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    with pooled_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT generation FROM catalog_vector_dirty
            WHERE catalog_key=? AND dirty=1
            """,
            (catalog_key,),
        ).fetchone()
    return row is not None and int(row["generation"]) == int(generation)


def clear_catalog_vector_dirty(
    catalog_key: str,
    generation: int,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """只确认观察到的 generation；晚到更新不会被旧 worker 删除。"""
    with pooled_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE catalog_vector_dirty SET dirty=0
            WHERE catalog_key=? AND generation=? AND dirty=1
            """,
            (catalog_key, int(generation)),
        )
        conn.commit()
        return cursor.rowcount > 0


def finish_catalog_vector_reconcile(
    generations: dict[str, int],
    *,
    model_fingerprint: str,
    reconciled_at: float | None = None,
    db_path: Optional[Path] = None,
) -> None:
    """提交全量对账水位，并按 generation 清理开始时观察到的事件。"""
    with pooled_connection(db_path) as conn:
        for catalog_key, generation in generations.items():
            conn.execute(
                "UPDATE catalog_vector_dirty SET dirty=0 "
                "WHERE catalog_key=? AND generation=? AND dirty=1",
                (catalog_key, int(generation)),
            )
        conn.execute(
            """
            INSERT INTO catalog_vector_sync_meta(
                singleton, model_fingerprint, reconciled_at
            ) VALUES (1, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                model_fingerprint=excluded.model_fingerprint,
                reconciled_at=excluded.reconciled_at
            """,
            (
                model_fingerprint,
                time.time() if reconciled_at is None else reconciled_at,
            ),
        )
        conn.commit()


def catalog_vector_reconcile_reason(
    model_fingerprint: str,
    *,
    db_path: Optional[Path] = None,
    now: float | None = None,
    interval_seconds: float = 24 * 60 * 60,
) -> str:
    """返回 bootstrap/model/periodic；空串表示本轮走增量。"""
    with pooled_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT model_fingerprint, reconciled_at
            FROM catalog_vector_sync_meta WHERE singleton=1
            """
        ).fetchone()
    if row is None:
        return "bootstrap"
    if (row["model_fingerprint"] or "") != model_fingerprint:
        return "model_changed"
    current = time.time() if now is None else float(now)
    if current - float(row["reconciled_at"] or 0) >= interval_seconds:
        return "periodic"
    return ""
