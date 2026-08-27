"""技能向量索引：Milvus Lite（嵌入式，可选）+ 与 skills_catalog 最终一致。

业务真相在 SQLite ``skills_catalog``；本模块只维护检索索引。
主键与 ``catalog_key`` 对齐；``content_sha`` 变了才 re-embed/upsert。

``pymilvus`` 为 optional extra（``xskill[milvus]``）。未安装时：
- 检索侧走引擎/skillhub 的 numpy 全库乘 fallback；
- 重活进程用内存索引对账（不落 ``skill_vectors.db``），并每小时 warn 一次。
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Sequence

logger = logging.getLogger("xskill.skill_vector_store")

COLLECTION = "skill_vectors"
DEFAULT_DIM = 8  # fake/tests；生产由首条真实向量决定或配置

# 未装 / 打不开 Milvus 时的节流警告（写进 xskill.log）。
# ``None`` = 从未 warn；勿用 0.0——``time.monotonic()`` 从开机起算，
# CI / 短寿命机器 uptime < 1h 时会把「首次」误判成仍在节流窗口内。
_MILVUS_WARN_INTERVAL_S = 3600.0
_milvus_last_warn_mono: Optional[float] = None
_pymilvus_import_ok: Optional[bool] = None


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
    """纯内存实现：单测不依赖 pymilvus，生产在装不上 Milvus Lite 时兜底。

    向量存放在一块预分配的二维 numpy 矩阵里（按行索引），不是「字典套
    Python list」——万级技能场景下这省两笔账：一是 Python float 装箱的
    空间（每个元素一个独立对象，vs 数组里连续的 8 字节）；二是
    ``search()`` 不再逐行把 list 转成 numpy 数组再算余弦，而是对整块
    矩阵一次性做向量化的归一化和矩阵乘法（issue #328）。

    删除的行位标记进空闲槽位表，之后 upsert 优先复用而不是让矩阵一直
    增长；容量不够时才整体扩容一次（``reserve()`` 可以在批量写入前把
    容量一次开够，避免边写边翻倍拷贝——问题同样出在「不知道最终大小、
    靠 append 摸着石头过河」，只是发生在 numpy 矩阵而不是 Python list
    上，道理一样）。
    """

    _GROWTH_FACTOR = 2
    _MIN_CAPACITY = 16

    def __init__(self, dim: int = DEFAULT_DIM, *, capacity: int = 0) -> None:
        import numpy as np

        self.dim = dim
        self._capacity = max(int(capacity), 0)
        self._matrix = np.empty((self._capacity, dim), dtype=np.float64)
        self._size = 0  # 已分配（含空闲槽）的行数上界
        self._key_to_row: dict[str, int] = {}
        self._meta: dict[str, dict] = {}
        self._free_rows: list[int] = []

    def reserve(self, n: int) -> None:
        """把容量一次性扩到至少 ``n`` 行；批量写入前调用可以避免边写边扩容。

        不是必须调用——不调用时 upsert 仍会按需自动扩容（翻倍策略），
        只是明知最终规模时提前 reserve 能省掉中途几次整体拷贝。
        """
        if n > self._capacity:
            self._grow_to(int(n))

    def _grow_to(self, capacity: int) -> None:
        import numpy as np

        new_matrix = np.empty((capacity, self.dim), dtype=np.float64)
        if self._size:
            new_matrix[: self._size] = self._matrix[: self._size]
        self._matrix = new_matrix
        self._capacity = capacity

    def _allocate_row(self) -> int:
        if self._free_rows:
            return self._free_rows.pop()
        if self._size >= self._capacity:
            self._grow_to(
                max(self._capacity * self._GROWTH_FACTOR, self._MIN_CAPACITY)
            )
        row = self._size
        self._size += 1
        return row

    def upsert(
        self,
        catalog_key: str,
        vector: Sequence[float],
        *,
        content_sha: str,
        source: str,
        name: str,
    ) -> None:
        import numpy as np

        vec = np.asarray(vector, dtype=np.float64)
        if vec.shape != (self.dim,):
            raise ValueError(
                f"vector dim mismatch for {catalog_key!r}: "
                f"expected ({self.dim},), got {vec.shape}"
            )
        row = self._key_to_row.get(catalog_key)
        if row is None:
            row = self._allocate_row()
            self._key_to_row[catalog_key] = row
        self._matrix[row] = vec
        self._meta[catalog_key] = {
            "content_sha": content_sha, "source": source, "name": name,
        }

    def delete(self, catalog_key: str) -> None:
        row = self._key_to_row.pop(catalog_key, None)
        self._meta.pop(catalog_key, None)
        if row is not None:
            self._free_rows.append(row)

    def get(self, catalog_key: str) -> Optional[dict]:
        row = self._key_to_row.get(catalog_key)
        if row is None:
            return None
        return {
            "catalog_key": catalog_key,
            "vector": self._matrix[row].tolist(),
            **self._meta[catalog_key],
        }

    def list_keys(self) -> set[str]:
        return set(self._key_to_row)

    @property
    def _rows(self) -> dict:
        """只读兼容视图（``{key: get(key)}``），供既有测试按旧的
        「字典套行」形状断言用；生产代码不读它。"""
        return {key: self.get(key) for key in self._key_to_row}

    def search(
        self, vector: Sequence[float], *, top_k: int = 10,
        exclude_keys: Optional[set[str]] = None,
    ) -> list[tuple[str, float]]:
        import numpy as np

        skip = exclude_keys or set()
        keys = [key for key in self._key_to_row if key not in skip]
        if not keys:
            return []
        rows = np.fromiter(
            (self._key_to_row[key] for key in keys),
            dtype=np.int64, count=len(keys),
        )
        # 一次性切片整块矩阵，不逐行 np.asarray 转换；归一化和打分都是
        # 向量化操作，不是 Python 循环里一条条算。
        candidates = self._matrix[rows]
        norms = np.linalg.norm(candidates, axis=1)
        norms[norms == 0] = 1.0
        q = np.asarray(vector, dtype=np.float64)
        qn = float(np.linalg.norm(q)) or 1.0
        scores = (candidates / norms[:, None]) @ (q / qn)
        k = min(top_k, len(keys))
        # top_k 通常远小于候选数：先用 argpartition 圈出前 k 个（均摊
        # O(n)），再对这一小撮排序，比对全量做一次 argsort 省时间。
        top_indices = np.argpartition(-scores, k - 1)[:k] if k > 0 else np.array([], dtype=np.int64)
        top_indices = top_indices[np.argsort(-scores[top_indices])]
        return [(keys[i], float(scores[i])) for i in top_indices]


class MilvusLiteSkillVectorIndex:
    """``~/.xskill/skill_vectors.db`` 嵌入式 Milvus Lite。"""

    def __init__(self, db_path: Path | str, *, dim: int = DEFAULT_DIM) -> None:
        from pymilvus import MilvusClient

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = int(dim)
        self._client = MilvusClient(str(self.db_path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self._client.has_collection(COLLECTION):
            try:
                described = self._client.describe_collection(COLLECTION)
                current_dim = self._described_vector_dim(described)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "cannot inspect existing Milvus collection dimension; "
                    "keeping collection",
                    exc_info=True,
                )
                return
            if current_dim is None or current_dim == self.dim:
                return
            # 向量索引是 skills_catalog 的可重建投影。维度变化时 Milvus 不能原地
            # 修改 schema，只能删 collection 后由本轮 model_changed 全量重灌。
            logger.info(
                "Milvus skill vector dimension changed: %s -> %s; rebuilding",
                current_dim,
                self.dim,
            )
            self._client.drop_collection(COLLECTION)
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

    @staticmethod
    def _described_vector_dim(description: dict) -> int | None:
        """兼容 pymilvus 2.4+ describe_collection 的字段形状。"""
        for field in description.get("fields", []) or []:
            if field.get("name") != "vector":
                continue
            value = (field.get("params") or {}).get("dim", field.get("dim"))
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None

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


def catalog_row_is_indexable(row: dict) -> bool:
    """该 catalog 行当前是否可进入推荐向量索引。"""
    if row.get("retired"):
        return False
    if not (row.get("description") or "").strip():
        return False
    # SkillHub 条目没有 native Git 分支，历史上 distributable 固定为 0，
    # 但仍是可检索第三方技能；native 则只索引 main/staging 成品。
    return row.get("source") == "skillhub" or bool(row.get("distributable", 1))


def indexable_catalog_rows(rows: Iterable[dict]) -> list[dict]:
    """过滤应写入向量索引的投影行。"""
    out = []
    for row in rows:
        if not catalog_row_is_indexable(row):
            continue
        desc = (row.get("description") or "").strip()
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
    if (
        existing
        and existing.get("content_sha") == sha
        and (existing.get("source") or "") == (row.get("source") or "")
        and (existing.get("name") or "") == (row.get("name") or "")
    ):
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
    force_upsert: bool = False,
    should_apply: Optional[Callable[[str, Optional[dict]], bool]] = None,
) -> dict:
    """对账：投影表 → Milvus。``should_apply`` 用于并发 generation fence。"""
    wanted = {
        r["catalog_key"]: r for r in indexable_catalog_rows(catalog_rows)
    }
    existing_keys = index.list_keys()
    # 已知这批最终最多写入多少行，提前把索引（若支持，如
    # MemorySkillVectorIndex）预留够容量，避免逐条 upsert 时反复翻倍扩容。
    reserve = getattr(index, "reserve", None)
    if callable(reserve):
        reserve(len(existing_keys) + len(wanted))
    upserted = deleted = skipped = deferred = 0
    for key, row in wanted.items():
        cur = index.get(key)
        if (
            not force_upsert
            and cur
            and cur.get("content_sha") == row["content_sha"]
            and (cur.get("source") or "") == (row.get("source") or "")
            and (cur.get("name") or "") == (row.get("name") or "")
        ):
            skipped += 1
            continue
        # content 未变、仅 source/name 元数据变化时复用旧向量，避免一次无意义的
        # embedding；模型切换的 force_upsert 仍强制重算。
        if (
            not force_upsert
            and cur
            and cur.get("content_sha") == row["content_sha"]
            and cur.get("vector") is not None
        ):
            vector = cur["vector"]
        else:
            vector = embed(row["description"])
        if should_apply is not None and not should_apply(key, row):
            deferred += 1
            continue
        index.upsert(
            key,
            vector,
            content_sha=row["content_sha"],
            source=row.get("source") or "",
            name=row.get("name") or "",
        )
        upserted += 1
    for key in existing_keys - set(wanted):
        if should_apply is not None and not should_apply(key, None):
            deferred += 1
            continue
        index.delete(key)
        deleted += 1
    return {
        "upserted": upserted,
        "deleted": deleted,
        "skipped": skipped,
        "deferred": deferred,
    }


def default_vector_db_path(xskill_home: Path | None = None) -> Path:
    home = Path(xskill_home) if xskill_home else Path.home() / ".xskill"
    return home.expanduser().resolve() / "skill_vectors.db"


def pymilvus_available() -> bool:
    """``pymilvus`` 是否可 import（不探测 Lite 运行时是否能开库）。"""
    global _pymilvus_import_ok
    if _pymilvus_import_ok is not None:
        return _pymilvus_import_ok
    try:
        import pymilvus  # noqa: F401
    except ImportError:
        _pymilvus_import_ok = False
    else:
        _pymilvus_import_ok = True
    return _pymilvus_import_ok


def warn_milvus_unavailable_hourly(reason: str) -> None:
    """服务器侧无 Milvus 时每小时最多一条 WARNING（进 ``xskill.log``）。"""
    global _milvus_last_warn_mono
    now = time.monotonic()
    if (
        _milvus_last_warn_mono is not None
        and (now - _milvus_last_warn_mono) < _MILVUS_WARN_INTERVAL_S
    ):
        return
    _milvus_last_warn_mono = now
    logger.warning(
        "Milvus Lite unavailable (%s); skill vector search uses slower "
        "numpy/in-memory fallback and may hurt recommend/search performance "
        "on large catalogs. Prefer: pip install 'xskill[milvus]' "
        "(or pip install 'pymilvus>=2.4.2').",
        reason,
    )


def try_open_milvus_lite_index(
    db_path: Path | str | None = None,
    *,
    dim: int = DEFAULT_DIM,
) -> Optional[SkillVectorIndex]:
    """仅打开真正的 Milvus Lite；不可用返回 ``None``（并可能 hourly warn）。"""
    if not pymilvus_available():
        warn_milvus_unavailable_hourly("pymilvus not installed")
        return None
    path = Path(db_path) if db_path else default_vector_db_path()
    try:
        return MilvusLiteSkillVectorIndex(path, dim=dim)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        warn_milvus_unavailable_hourly(
            f"open failed: {type(exc).__name__}: {exc}",
        )
        logger.debug("MilvusLiteSkillVectorIndex open failed", exc_info=True)
        return None


def open_skill_vector_index(
    db_path: Path | str | None = None,
    *,
    dim: int = DEFAULT_DIM,
    memory: bool = False,
) -> SkillVectorIndex:
    """打开向量索引。

    - ``memory=True``：测试用纯内存。
    - 默认尝试 Milvus Lite；``pymilvus`` 未装或开库失败 → 内存索引 + 节流 warn
      （不落盘；引擎检索侧另有 numpy fallback）。
    """
    if memory:
        return MemorySkillVectorIndex(dim=dim)
    milvus = try_open_milvus_lite_index(db_path, dim=dim)
    if milvus is not None:
        return milvus
    return MemorySkillVectorIndex(dim=dim)
