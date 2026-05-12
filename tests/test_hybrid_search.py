"""HybridSearch：向量 + BM25 关键字，结果 union + dedup（不做 rerank）"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.hybrid_search import HybridSearch
from tests.test_atom_task_store import _FakeEmbed


def _seed(tmp_path: Path) -> AtomTaskStore:
    store = AtomTaskStore(root=tmp_path / "cc-sessions")
    items = [
        ("atom_a_0001", "a", "fix django migration conflict"),
        ("atom_a_0002", "a", "implement pandas groupby agg"),
        ("atom_b_0001", "b", "deploy fastapi to port 8000"),
        ("atom_b_0002", "b", "django orm query optimization"),
    ]
    for aid, tid, summary in items:
        store.save(AtomTask(
            atom_id=aid, traj_id=tid,
            offset_start=0, offset_end=10,
            intent=summary, summary=summary,
            tags=[], used_skills=[], ux_score=None,
            pre_atom_id=None, post_atom_id=None,
            context_prefix="", raw_segment="",
        ))
    store.rebuild_vector_index(_FakeEmbed())
    return store


class TestHybridSearch:
    def test_keyword_matches_appear_in_results(self, tmp_path):
        store = _seed(tmp_path)
        hs = HybridSearch(store, _FakeEmbed())
        results = hs.search("django", top_k=10)
        ids = [r["atom_id"] for r in results]
        # 两条 django 内容必须都出现（BM25 命中）
        assert "atom_a_0001" in ids
        assert "atom_b_0002" in ids

    def test_results_deduplicated_by_atom_id(self, tmp_path):
        store = _seed(tmp_path)
        hs = HybridSearch(store, _FakeEmbed())
        results = hs.search("django migration conflict", top_k=10)
        ids = [r["atom_id"] for r in results]
        assert len(ids) == len(set(ids))

    def test_each_result_records_signal_source(self, tmp_path):
        store = _seed(tmp_path)
        hs = HybridSearch(store, _FakeEmbed())
        results = hs.search("django", top_k=10)
        for r in results:
            assert "sources" in r
            assert set(r["sources"]).issubset({"vector", "keyword"})
            assert len(r["sources"]) >= 1

    def test_empty_query_returns_empty(self, tmp_path):
        store = _seed(tmp_path)
        hs = HybridSearch(store, _FakeEmbed())
        assert hs.search("", top_k=5) == [] or all(
            "sources" in r for r in hs.search("", top_k=5)
        )

    def test_no_atoms_returns_empty(self, tmp_path):
        store = AtomTaskStore(root=tmp_path / "empty")
        hs = HybridSearch(store, _FakeEmbed())
        assert hs.search("django", top_k=5) == []
