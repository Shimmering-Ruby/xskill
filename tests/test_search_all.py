"""tests/test_search_all.py -- cross-dataset AtomTask 检索（v2）"""

from __future__ import annotations

from unittest.mock import patch


from xskill.utils.search import search_all


def _hit(atom_id: str, vec_sim: float, dataset_dir: str = ""):
    return {
        "atom_id": atom_id,
        "sources": ["vector"],
        "vector_similarity": vec_sim,
        "traj_id": atom_id.replace("atom_", "traj_").split("_")[0],
        "intent": f"intent for {atom_id}",
        "summary": f"summary {atom_id}",
        "md_path": f"{dataset_dir}/traj_x.md",
    }


class TestSearchAll:
    def test_merges_and_sorts_across_datasets(self, tmp_path):
        from xskill.pipeline.registry import register_dir
        db_path = tmp_path / "test_registry.db"
        d1 = tmp_path / "ds1"; d1.mkdir(); (d1 / "index.pkl").write_bytes(b"fake")
        d2 = tmp_path / "ds2"; d2.mkdir(); (d2 / "index.pkl").write_bytes(b"fake")
        register_dir(d1, db_path=db_path)
        register_dir(d2, db_path=db_path)

        def fake_search(dataset_dir, query_text, top_k=5, min_similarity=0.0,
                        success_filter="all", config=None):
            if dataset_dir == d1.resolve():
                return [_hit("atom_A", 0.9, str(d1)),
                        _hit("atom_B", 0.5, str(d1))]
            if dataset_dir == d2.resolve():
                return [_hit("atom_C", 0.8, str(d2)),
                        _hit("atom_D", 0.3, str(d2))]
            return []

        with patch("xskill.utils.search.search", side_effect=fake_search), \
             patch("xskill.pipeline.registry.all_index_paths",
                   return_value=[d1.resolve(), d2.resolve()]):
            results = search_all("test query", top_k=3)

        assert len(results) == 3
        # 按 vector_similarity 倒序
        assert results[0]["atom_id"] == "atom_A"
        assert results[0]["vector_similarity"] == 0.9
        assert results[1]["atom_id"] == "atom_C"
        assert results[2]["atom_id"] == "atom_B"

    def test_returns_empty_when_no_dirs(self):
        with patch("xskill.pipeline.registry.all_index_paths", return_value=[]):
            assert search_all("test query") == []

    def test_skips_dirs_with_missing_index(self, tmp_path):
        d1 = tmp_path / "ds1"; d1.mkdir()
        with patch("xskill.pipeline.registry.all_index_paths", return_value=[d1]), \
             patch("xskill.utils.search.search", side_effect=FileNotFoundError("no index")):
            results = search_all("test query")
        assert results == []

    def test_adds_dataset_dir_field(self, tmp_path):
        d1 = tmp_path / "ds1"; d1.mkdir()

        def fake_search(dataset_dir, query_text, top_k=5, min_similarity=0.0,
                        success_filter="all", config=None):
            return [_hit("atom_X", 0.7)]

        with patch("xskill.pipeline.registry.all_index_paths", return_value=[d1]), \
             patch("xskill.utils.search.search", side_effect=fake_search):
            results = search_all("test query")
        assert len(results) == 1
        assert results[0]["dataset_dir"] == str(d1)
