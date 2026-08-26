"""tests/test_memory_vector_index.py — MemorySkillVectorIndex 的预分配矩阵实现（issue #328）。

内部存储从「字典套 Python list」换成预分配的二维 numpy 矩阵 + 空闲槽位
表，覆盖：容量自动增长与预留（``reserve``）、删除后槽位复用不搬迁其余
行、``search`` 的向量化打分正确性（含 exclude_keys、top_k 超过候选数）、
维度不匹配报错、大批量下 upsert/delete/search 混合操作的正确性。
"""
from __future__ import annotations

import math

import pytest

from xskill.recommend.skill_vector_store import MemorySkillVectorIndex


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class TestCapacityGrowth:
    def test_starts_with_zero_capacity_and_grows_on_first_upsert(self):
        index = MemorySkillVectorIndex(dim=3)
        assert index._capacity == 0
        index.upsert("a", [1.0, 0.0, 0.0], content_sha="s", source="x", name="a")
        assert index._capacity >= 1
        assert index.get("a")["vector"] == [1.0, 0.0, 0.0]

    def test_reserve_avoids_incremental_growth(self):
        index = MemorySkillVectorIndex(dim=2)
        index.reserve(100)
        capacity_after_reserve = index._capacity
        assert capacity_after_reserve >= 100
        for i in range(100):
            index.upsert(f"k{i}", [1.0, 0.0], content_sha="s", source="x", name="n")
        # reserve 过的容量足够装下全部 100 条，中途不需要再扩容一次。
        assert index._capacity == capacity_after_reserve

    def test_reserve_is_noop_when_smaller_than_current_capacity(self):
        index = MemorySkillVectorIndex(dim=2)
        index.reserve(50)
        cap = index._capacity
        index.reserve(10)
        assert index._capacity == cap

    def test_values_survive_growth(self):
        index = MemorySkillVectorIndex(dim=2, capacity=1)
        for i in range(20):
            index.upsert(f"k{i}", [float(i), 1.0], content_sha="s", source="x", name="n")
        for i in range(20):
            assert index.get(f"k{i}")["vector"] == [float(i), 1.0]


class TestFreeSlotReuse:
    def test_delete_then_upsert_reuses_row_without_growing(self):
        index = MemorySkillVectorIndex(dim=2, capacity=4)
        for i in range(4):
            index.upsert(f"k{i}", [float(i), 0.0], content_sha="s", source="x", name="n")
        assert index._capacity == 4
        index.delete("k2")
        assert "k2" not in index.list_keys()
        index.upsert("k4", [9.0, 0.0], content_sha="s", source="x", name="n")
        # 复用了 k2 腾出的槽位，不应触发扩容。
        assert index._capacity == 4
        assert index.get("k4")["vector"] == [9.0, 0.0]
        assert index.get("k2") is None

    def test_upsert_same_key_overwrites_in_place(self):
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("a", [1.0, 0.0], content_sha="v1", source="x", name="n")
        index.upsert("a", [0.0, 1.0], content_sha="v2", source="x", name="n")
        assert index.list_keys() == {"a"}
        got = index.get("a")
        assert got["vector"] == [0.0, 1.0]
        assert got["content_sha"] == "v2"

    def test_delete_missing_key_is_noop(self):
        index = MemorySkillVectorIndex(dim=2)
        index.delete("nope")  # 不应报错
        assert index.list_keys() == set()


class TestDimensionValidation:
    def test_wrong_dim_raises(self):
        index = MemorySkillVectorIndex(dim=3)
        with pytest.raises(ValueError, match="dim mismatch"):
            index.upsert("a", [1.0, 2.0], content_sha="s", source="x", name="n")


class TestSearch:
    def test_search_orders_by_cosine_similarity(self):
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("close", _unit([1.0, 0.1]), content_sha="s", source="x", name="close")
        index.upsert("far", _unit([0.0, 1.0]), content_sha="s", source="x", name="far")
        index.upsert("opposite", _unit([-1.0, 0.0]), content_sha="s", source="x", name="opposite")
        results = index.search([1.0, 0.0], top_k=3)
        assert [key for key, _score in results] == ["close", "far", "opposite"]

    def test_search_respects_exclude_keys(self):
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("a", [1.0, 0.0], content_sha="s", source="x", name="a")
        index.upsert("b", [1.0, 0.0], content_sha="s", source="x", name="b")
        results = index.search([1.0, 0.0], top_k=5, exclude_keys={"a"})
        assert [key for key, _score in results] == ["b"]

    def test_search_top_k_larger_than_candidates_returns_all(self):
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("a", [1.0, 0.0], content_sha="s", source="x", name="a")
        index.upsert("b", [0.0, 1.0], content_sha="s", source="x", name="b")
        results = index.search([1.0, 1.0], top_k=50)
        assert len(results) == 2

    def test_search_empty_index_returns_empty(self):
        index = MemorySkillVectorIndex(dim=2)
        assert index.search([1.0, 0.0], top_k=5) == []

    def test_search_after_deleting_only_candidate_returns_empty(self):
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("a", [1.0, 0.0], content_sha="s", source="x", name="a")
        index.delete("a")
        assert index.search([1.0, 0.0], top_k=5) == []

    def test_search_zero_vector_row_does_not_crash(self):
        """全零向量范数为 0，除法要避免 NaN/inf 把整个打分弄脏。"""
        index = MemorySkillVectorIndex(dim=2)
        index.upsert("zero", [0.0, 0.0], content_sha="s", source="x", name="zero")
        index.upsert("real", [1.0, 0.0], content_sha="s", source="x", name="real")
        results = dict(index.search([1.0, 0.0], top_k=5))
        assert math.isfinite(results["zero"])
        assert math.isfinite(results["real"])


class TestBulkMixedOperations:
    def test_thousand_row_upsert_delete_search_roundtrip(self):
        """大批量下混合增删查仍正确——预分配/复用逻辑不能只在小规模下对。"""
        index = MemorySkillVectorIndex(dim=4)
        index.reserve(1000)
        for i in range(1000):
            index.upsert(
                f"k{i}", _unit([float(i % 7), float(i % 5), 1.0, 0.0]),
                content_sha=f"sha{i}", source="s", name=f"n{i}",
            )
        assert len(index.list_keys()) == 1000
        for i in range(0, 1000, 3):
            index.delete(f"k{i}")
        remaining = index.list_keys()
        assert len(remaining) == 1000 - len(range(0, 1000, 3))
        for i in range(0, 1000, 3):
            assert f"k{i}" not in remaining
        # 剩余行仍能正确检索、且 content_sha 元数据没有互相串行。
        for i in range(1, 1000, 7):
            if i % 3 == 0:
                continue  # 这条已被删掉
            got = index.get(f"k{i}")
            assert got["content_sha"] == f"sha{i}"
        hits = index.search(_unit([1.0, 1.0, 1.0, 0.0]), top_k=10)
        assert len(hits) == 10
        assert all(key in remaining for key, _score in hits)
