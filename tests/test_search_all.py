"""tests/test_search_all.py -- cross-dataset search via search_all()"""

from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from traj2skill.search import search_all


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "test_registry.db"


def _make_search_result(traj_id: str, similarity: float, dataset_dir: str = ""):
    return {
        "traj_id": traj_id,
        "similarity": similarity,
        "meta": {"intent": f"intent for {traj_id}", "success": True, "tags": ["test"]},
        "md_path": f"{dataset_dir}/{traj_id}.md",
        "traj_json": {},
    }


class TestSearchAll:
    def test_merges_and_sorts_across_datasets(self, tmp_path, db_path):
        from traj2skill.registry import register_dir

        d1 = tmp_path / "ds1"
        d1.mkdir()
        (d1 / "index.pkl").write_bytes(b"fake")

        d2 = tmp_path / "ds2"
        d2.mkdir()
        (d2 / "index.pkl").write_bytes(b"fake")

        register_dir(d1, db_path=db_path)
        register_dir(d2, db_path=db_path)

        # Mock search() to return different results per directory
        def fake_search(dataset_dir, query_text, top_k=5, min_similarity=0.0,
                        success_filter="all", config=None):
            if dataset_dir == d1.resolve():
                return [
                    _make_search_result("traj_A", 0.9, str(d1)),
                    _make_search_result("traj_B", 0.5, str(d1)),
                ]
            elif dataset_dir == d2.resolve():
                return [
                    _make_search_result("traj_C", 0.8, str(d2)),
                    _make_search_result("traj_D", 0.3, str(d2)),
                ]
            return []

        with patch("traj2skill.search.search", side_effect=fake_search), \
             patch("traj2skill.registry.get_registry_dir", return_value=tmp_path), \
             patch("traj2skill.registry.all_index_paths",
                   return_value=[d1.resolve(), d2.resolve()]):
            results = search_all("test query", top_k=3)

        assert len(results) == 3
        assert results[0]["traj_id"] == "traj_A"
        assert results[0]["similarity"] == 0.9
        assert results[1]["traj_id"] == "traj_C"
        assert results[1]["similarity"] == 0.8
        assert results[2]["traj_id"] == "traj_B"
        # traj_D (0.3) is cut off by top_k=3

    def test_returns_empty_when_no_dirs(self, db_path):
        with patch("traj2skill.registry.all_index_paths", return_value=[]):
            results = search_all("test query")
        assert results == []

    def test_skips_dirs_with_missing_index(self, tmp_path, db_path):
        d1 = tmp_path / "ds1"
        d1.mkdir()
        # no index.pkl

        with patch("traj2skill.registry.all_index_paths", return_value=[d1]), \
             patch("traj2skill.search.search", side_effect=FileNotFoundError("no index")):
            results = search_all("test query")
        assert results == []

    def test_adds_dataset_dir_field(self, tmp_path, db_path):
        d1 = tmp_path / "ds1"
        d1.mkdir()

        def fake_search(**kwargs):
            return [_make_search_result("traj_X", 0.7)]

        # Use keyword form to match the actual call
        def fake_search_positional(dataset_dir, query_text, top_k=5,
                                    min_similarity=0.0, success_filter="all", config=None):
            return [_make_search_result("traj_X", 0.7)]

        with patch("traj2skill.registry.all_index_paths", return_value=[d1]), \
             patch("traj2skill.search.search", side_effect=fake_search_positional):
            results = search_all("test query")

        assert len(results) == 1
        assert results[0]["dataset_dir"] == str(d1)
