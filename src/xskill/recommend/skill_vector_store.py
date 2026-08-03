"""技能向量索引：Milvus Lite（嵌入式）+ 与 skills_catalog 最终一致。

业务真相在 SQLite ``skills_catalog``；本模块只维护检索索引。
主键与 ``catalog_key`` 对齐；``content_sha`` 变了才 re-embed/upsert。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Sequence

logger = logging.getLogger("xskill.skill_vector_store")

COLLECTION = "skill_vectors"
DEFAULT_DIM = 8  # fake/tests；生产由首条真实向量决定或配置


def content_sha_for_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fake_embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """确定性伪向量（测试/无 embed client 时用）。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vals = []
    for i in range(dim):
        vals.append(((digest[i % len(digest)] + i * 13) % 256) / 255.0)
    # L2 normalize
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


def _safe_int_id(catalog_key: str) -> int:
    """Milvus 常用 int64 主键；由 catalog_key 稳定哈希得到。"""
    digest = hashlib.sha256(catalog_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


class SkillVectorIndex(Protocol):
    def upsert(
        self,
        catalog_key: str,
        vector: Sequence[float],
        *,
        content_sha: str,
        source: str,
        name: str,
    ) -> None: ...

    def delete(self, catalog_key: str) -> None: ...

    def get(self, catalog_key: str) -> Optional[dict]: ...

    def list_keys(self) -> set[str]: ...

    def search(
        self, vector: Sequence[float], *, top_k: int = 10,
        exclude_keys: Optional[set[str]] = None,
    ) -> list[tuple[str, float]]: ...


class MemorySkillVectorIndex:
    """纯内存实现：单测不依赖 pymilvus。"""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self._rows: dict[str, dict] = {}

    def upsert(
        self,
        catalog_key: str,
        vector: Sequence[float],
        *,
        content_sha: str,
        source: str,
        name: str,
    ) -> None:
        self._rows[catalog_key] = {
            "catalog_key": catalog_key,
            "vector": list(vector),
            "content_sha": content_sha,
            "source": source,
            "name": name,
        }

    def delete(self, catalog_key: str) -> None:
        self._rows.pop(catalog_key, None)

    def get(self, catalog_key: str) -> Optional[dict]:
        row = self._rows.get(catalog_key)
        return dict(row) if row else None

    def list_keys(self) -> set[str]:
        return set(self._rows)

    def search(
        self, vector: Sequence[float], *, top_k: int = 10,
        exclude_keys: Optional[set[str]] = None,
    ) -> list[tuple[str, float]]:
        import numpy as np

        q = np.asarray(vector, dtype=float)
        qn = float(np.linalg.norm(q)) or 1.0
        q = q / qn
        scored: list[tuple[str, float]] = []
        skip = exclude_keys or set()
        for key, row in self._rows.items():
            if key in skip:
                continue
            v = np.asarray(row["vector"], dtype=float)
            vn = float(np.linalg.norm(v)) or 1.0
            scored.append((key, float(q @ (v / vn))))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


class MilvusLiteSkillVectorIndex:
    """``~/.xskill/skill_vectors.db`` 嵌入式 Milvus Lite。"""

    def __init__(self, db_path: Path | str, *, dim: int = DEFAULT_DIM) -> None:
        from pymilvus import DataType, MilvusClient

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = int(dim)
        self._client = MilvusClient(str(self.db_path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self._client.has_collection(COLLECTION):
            return
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("catalog_key", DataType.VARCHAR, max_length=512)
        schema.add_field("content_sha", DataType.VARCHAR, max_length=64)
        schema.add_field("source", DataType.VARCHAR, max_length=32)
        schema.add_field("name", DataType.VARCHAR, max_length=512)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", metric_type="COSINE", index_type="FLAT")
        self._client.create_collection(
            collection_name=COLLECTION, schema=schema, index_params=index_params,
        )

    def upsert(
        self,
        catalog_key: str,
        vector: Sequence[float],
        *,
        content_sha: str,
        source: str,
        name: str,
    ) -> None:
        if len(vector) != self.dim:
            raise ValueError(f"vector dim {len(vector)} != {self.dim}")
        self._client.upsert(
            collection_name=COLLECTION,
            data=[{
                "id": _safe_int_id(catalog_key),
                "catalog_key": catalog_key,
                "content_sha": content_sha,
                "source": source,
                "name": name,
                "vector": list(vector),
            }],
        )

    def delete(self, catalog_key: str) -> None:
        self._client.delete(
            collection_name=COLLECTION,
            ids=[_safe_int_id(catalog_key)],
        )

    def get(self, catalog_key: str) -> Optional[dict]:
        rows = self._client.get(
            collection_name=COLLECTION,
            ids=[_safe_int_id(catalog_key)],
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "catalog_key": row.get("catalog_key", catalog_key),
            "content_sha": row.get("content_sha", ""),
            "source": row.get("source", ""),
            "name": row.get("name", ""),
            "vector": row.get("vector"),
        }

    def list_keys(self) -> set[str]:
        # query all catalog_key；Lite 小规模可接受
        try:
            rows = self._client.query(
                collection_name=COLLECTION,
                filter="",
                output_fields=["catalog_key"],
                limit=16384,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            rows = self._client.query(
                collection_name=COLLECTION,
                filter="id >= 0",
                output_fields=["catalog_key"],
                limit=16384,
            )
        return {r["catalog_key"] for r in rows if r.get("catalog_key")}

    def search(
        self, vector: Sequence[float], *, top_k: int = 10,
        exclude_keys: Optional[set[str]] = None,
    ) -> list[tuple[str, float]]:
        results = self._client.search(
            collection_name=COLLECTION,
            data=[list(vector)],
            limit=max(top_k * 3, top_k),
            output_fields=["catalog_key"],
        )
        skip = exclude_keys or set()
        out: list[tuple[str, float]] = []
        for hits in results:
            for hit in hits:
                key = hit.get("catalog_key") or hit.get("entity", {}).get("catalog_key")
                if not key or key in skip:
                    continue
                score = float(hit.get("distance", hit.get("score", 0.0)))
                out.append((key, score))
                if len(out) >= top_k:
                    return out
        return out


EmbedFn = Callable[[str], list[float]]


def indexable_catalog_rows(rows: Iterable[dict]) -> list[dict]:
    """过滤应写入向量索引的投影行（需非空 description）。"""
    out = []
    for row in rows:
        desc = (row.get("description") or "").strip()
        if not desc:
            continue
        sha = row.get("content_sha") or content_sha_for_text(desc)
        out.append({**row, "content_sha": sha, "description": desc})
    return out


def sync_row_to_index(
    index: SkillVectorIndex,
    row: dict,
    *,
    embed: EmbedFn,
) -> None:
    desc = row["description"]
    sha = row["content_sha"]
    key = row["catalog_key"]
    existing = index.get(key)
    if existing and existing.get("content_sha") == sha:
        return
    index.upsert(
        key,
        embed(desc),
        content_sha=sha,
        source=row.get("source") or "",
        name=row.get("name") or "",
    )


def delete_from_index(index: SkillVectorIndex, catalog_key: str) -> None:
    index.delete(catalog_key)


def reconcile_catalog_to_index(
    index: SkillVectorIndex,
    catalog_rows: Sequence[dict],
    *,
    embed: EmbedFn,
) -> dict:
    """对账：投影表 → Milvus。返回 {upserted, deleted, skipped}。"""
    wanted = {
        r["catalog_key"]: r for r in indexable_catalog_rows(catalog_rows)
    }
    existing_keys = index.list_keys()
    upserted = deleted = skipped = 0
    for key, row in wanted.items():
        cur = index.get(key)
        if cur and cur.get("content_sha") == row["content_sha"]:
            skipped += 1
            continue
        sync_row_to_index(index, row, embed=embed)
        upserted += 1
    for key in existing_keys - set(wanted):
        index.delete(key)
        deleted += 1
    return {"upserted": upserted, "deleted": deleted, "skipped": skipped}


def default_vector_db_path(xskill_home: Path | None = None) -> Path:
    home = Path(xskill_home) if xskill_home else Path.home() / ".xskill"
    return home.expanduser().resolve() / "skill_vectors.db"


def open_skill_vector_index(
    db_path: Path | str | None = None,
    *,
    dim: int = DEFAULT_DIM,
    memory: bool = False,
) -> SkillVectorIndex:
    if memory:
        return MemorySkillVectorIndex(dim=dim)
    path = Path(db_path) if db_path else default_vector_db_path()
    return MilvusLiteSkillVectorIndex(path, dim=dim)
