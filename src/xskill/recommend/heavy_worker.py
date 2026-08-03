"""重活进程：Milvus 对账 + 脏用户推荐预计算（与 Web GIL 隔离）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("xskill.recommend.heavy_worker")


def _load_catalog_rows(db_path: Path) -> list[dict]:
    from xskill.pipeline.registry import pooled_connection

    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT catalog_key, name, source, description, content_sha, skill_id
            FROM skills_catalog
            """
        ).fetchall()
    return [dict(r) for r in rows]


def run_vector_reconcile(
    *,
    db_path: Path,
    vector_db_path: Path,
    embed=None,
    memory_index=None,
) -> dict:
    from xskill.recommend.skill_vector_store import (
        DEFAULT_DIM,
        fake_embed,
        open_skill_vector_index,
        reconcile_catalog_to_index,
    )

    rows = _load_catalog_rows(db_path)
    embed_fn = embed or (lambda text: fake_embed(text, DEFAULT_DIM))
    index = memory_index or open_skill_vector_index(vector_db_path, dim=DEFAULT_DIM)
    stats = reconcile_catalog_to_index(index, rows, embed=embed_fn)
    logger.info(
        "vector reconcile: upserted=%s deleted=%s skipped=%s",
        stats["upserted"], stats["deleted"], stats["skipped"],
    )
    return stats


def _skill_name_from_index(vector_index, catalog_key: str) -> str:
    row = vector_index.get(catalog_key)
    if row:
        name = (row.get("name") or "").strip()
        if name:
            return name
        if row.get("source") == "skillhub" and ":" in catalog_key:
            return catalog_key.split(":", 1)[-1]
    if ":" in catalog_key:
        return catalog_key.split(":", 1)[-1]
    return catalog_key


def compute_recommend_for_user(
    user_key: str,
    *,
    db_path: Path,
    vector_index,
    top_k: int = 20,
    profile_centers: Optional[list[list[float]]] = None,
) -> list[str]:
    """用画像中心向量在索引里 search；无中心则写空推荐（sync 侧走 ranked/ux）。"""
    from xskill.recommend.recommend_store import save_recommend_slots

    if not profile_centers:
        save_recommend_slots(user_key, [], fingerprint="no_profile", db_path=db_path)
        return []
    names: list[str] = []
    seen: set[str] = set()
    for center in profile_centers:
        for catalog_key, _score in vector_index.search(center, top_k=top_k):
            name = _skill_name_from_index(vector_index, catalog_key)
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= top_k:
                break
        if len(names) >= top_k:
            break
    save_recommend_slots(
        user_key, names, fingerprint=f"centers={len(profile_centers)}", db_path=db_path,
    )
    return names


def _user_key_for_client(engine, client_id: str) -> str:
    reg = getattr(engine, "client_registry", None)
    if reg is not None:
        try:
            name = reg.user_name_for(client_id)
            if name:
                return name
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return client_id


def _client_id_for_user_key(engine, user_key: str) -> str:
    """推荐表键 → 画像键（client_id）。"""
    if not user_key:
        return user_key
    reg = getattr(engine, "client_registry", None)
    if reg is None:
        return user_key
    try:
        for row in reg.list():
            cid = row["client_id"]
            if cid == user_key:
                return cid
            try:
                if reg.user_name_for(cid) == user_key:
                    return cid
            except Exception:  # pylint: disable=broad-exception-caught
                continue
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("client_id resolve failed for %s", user_key, exc_info=True)
    return user_key


def _load_profile_centers(engine, client_id: str) -> Optional[list[list[float]]]:
    try:
        user = engine.load_client_user(client_id, include_recommended=False)
        ci = user.client_interest
        if ci is None or ci.feature_tensor is None:
            return None
        return [list(map(float, row)) for row in ci.feature_tensor]
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("profile centers unavailable for %s", client_id, exc_info=True)
        return None


def process_dirty_recommends(
    *,
    db_path: Path,
    vector_index,
    engine,
    limit: int = 32,
) -> int:
    from xskill.recommend.recommend_store import (
        clear_recommend_dirty,
        list_dirty_user_keys,
        save_recommend_slots,
    )

    keys = list_dirty_user_keys(limit=limit, db_path=db_path)
    done = 0
    for user_key in keys:
        try:
            client_id = _client_id_for_user_key(engine, user_key)
            centers = _load_profile_centers(engine, client_id)
            compute_recommend_for_user(
                user_key,
                db_path=db_path,
                vector_index=vector_index,
                profile_centers=centers,
            )
            done += 1
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("recommend dirty failed user_key=%s", user_key)
            clear_recommend_dirty(user_key, db_path=db_path)
            save_recommend_slots(user_key, [], fingerprint="error", db_path=db_path)
    return done


def _embed_fn_from_engine(engine):
    """从引擎 embed_client 构造 embed(text)->list[float]；不可用则 None。"""
    client = getattr(engine, "embed_client", None)
    if client is None or not hasattr(client, "encode"):
        return None

    def _embed(text: str) -> list[float]:
        vec = client.encode(text)
        return [float(x) for x in vec]

    return _embed


def run_recommend_heavy_once(
    *,
    engine,
    db_path: Path | None = None,
    vector_db_path: Path | None = None,
    memory_index=None,
    mark_catalog_dirty: bool = True,
) -> dict:
    """对账向量索引并消化推荐脏队列（画像刷新由调用方先跑）。"""
    from xskill.config import XSKILL_HOME, get_registry_db_path
    from xskill.recommend.recommend_store import mark_all_recommend_dirty
    from xskill.recommend.skill_vector_store import (
        DEFAULT_DIM,
        default_vector_db_path,
        fake_embed,
        open_skill_vector_index,
    )

    registry = Path(db_path) if db_path else get_registry_db_path()
    vdb = Path(vector_db_path) if vector_db_path else default_vector_db_path(XSKILL_HOME)
    embed_fn = _embed_fn_from_engine(engine)
    if embed_fn is None:
        embed_fn = lambda text: fake_embed(text, DEFAULT_DIM)  # noqa: E731
        dim = DEFAULT_DIM
    else:
        dim = len(embed_fn("dimension probe"))
    # open_skill_vector_index：无 pymilvus 时退回内存索引并 hourly warn
    index = memory_index or open_skill_vector_index(vdb, dim=dim)
    vec_stats = run_vector_reconcile(
        db_path=registry,
        vector_db_path=vdb,
        embed=embed_fn,
        memory_index=index,
    )
    if mark_catalog_dirty and (
        vec_stats.get("upserted", 0) or vec_stats.get("deleted", 0)
    ):
        mark_all_recommend_dirty(reason="catalog_vector_changed", db_path=registry)
    n = process_dirty_recommends(
        db_path=registry, vector_index=index, engine=engine,
    )
    return {"vector": vec_stats, "recommends": n}
