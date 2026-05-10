"""tests/test_watcher.py -- DirectoryWatcher unit tests

Tests the watcher's discover + meta + index pipeline with mocked LLM/embed.
Does NOT test actual LLM calls or process_traj (those are integration tests).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xskill.registry import register_dir, discover_trajectories, get_unindexed, get_needs_meta
from xskill.watcher import DirectoryWatcher


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture()
def traj_dir(tmp_path):
    d = tmp_path / "dataset"
    d.mkdir()
    (d / "traj_0001.md").write_text("# Traj 1\nagent did X then Y")
    (d / "traj_0001.json").write_text("{}")
    return d


@pytest.fixture()
def skill_dir(tmp_path):
    d = tmp_path / "skill"
    d.mkdir()
    return d


class TestWatcherDiscovery:
    """Test that _poll_once discovers files and updates DB."""

    def test_discover_new_files(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)

        watcher = DirectoryWatcher(
            llm=None, embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        watcher._poll_once()

        # File should be discovered but not indexed (no LLM/embed)
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        rows = conn.execute("SELECT filename FROM trajectories WHERE watch_dir_id=?", (wid,)).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["filename"] == "traj_0001.md"

    def test_stats_updated(self, traj_dir, skill_dir, db_path):
        register_dir(traj_dir, db_path=db_path)

        watcher = DirectoryWatcher(
            llm=None, embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        watcher._poll_once()

        assert watcher.stats["polls"] == 1
        assert watcher.stats["new_trajs"] == 1


class TestWatcherMetaExtraction:
    """Test meta extraction step with mocked _process_one_meta."""

    def test_calls_process_one_meta(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)
        # Pre-discover so file is known
        discover_trajectories(wid, traj_dir, db_path=db_path)

        mock_llm = MagicMock()

        with patch("xskill.watcher.get_needs_meta", return_value=["traj_0001.md"]) as mock_gn, \
             patch("xskill.index._process_one_meta") as mock_pom:
            mock_gn.side_effect = lambda wid, **kw: ["traj_0001.md"] if kw else ["traj_0001.md"]

            watcher = DirectoryWatcher(
                llm=mock_llm, embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
            )
            watcher._poll_once()

            mock_pom.assert_called_once()
            call_args = mock_pom.call_args
            assert str(call_args[0][0]).endswith("traj_0001.md")
            assert call_args[0][1] is mock_llm


class TestWatcherUxScore:
    """Test header parsing → ux_score trigger."""

    def test_triggers_score_for_traj_with_header(self, tmp_path, db_path):
        traj_dir = tmp_path / "dataset"
        traj_dir.mkdir()
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "test_skill").mkdir()

        # Write a traj with xskill header
        (traj_dir / "traj_0001.md").write_text(
            "<!-- xskill:skill=test_skill side=staging sha=abc123 -->\n# Traj\nagent did X"
        )

        register_dir(traj_dir, db_path=db_path)

        mock_llm = MagicMock()

        with patch("xskill.ux_score.score_and_record") as mock_score:
            watcher = DirectoryWatcher(
                llm=mock_llm, embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
            )
            watcher._poll_once()

            mock_score.assert_called_once()
            call_kw = mock_score.call_args[1]
            assert call_kw["skill_name"] == "test_skill"
            assert call_kw["side"] == "staging"
            assert call_kw["commit_sha"] == "abc123"
            assert call_kw["traj_id"] == "traj_0001"

    def test_skips_traj_without_header(self, tmp_path, db_path):
        traj_dir = tmp_path / "dataset"
        traj_dir.mkdir()
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()

        (traj_dir / "traj_0001.md").write_text("# No header\njust content")
        register_dir(traj_dir, db_path=db_path)

        with patch("xskill.ux_score.score_and_record") as mock_score:
            watcher = DirectoryWatcher(
                llm=MagicMock(), embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
            )
            watcher._poll_once()
            mock_score.assert_not_called()


class TestWatcherStartStop:
    """Test thread lifecycle."""

    def test_start_and_stop(self, tmp_path, db_path):
        watcher = DirectoryWatcher(
            llm=None, embed_client=None, config={},
            skill_dir=tmp_path, poll_interval=0.1, db_path=db_path,
        )
        assert not watcher.is_running
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running

    def test_double_start_noop(self, tmp_path, db_path):
        watcher = DirectoryWatcher(
            llm=None, embed_client=None, config={},
            skill_dir=tmp_path, poll_interval=0.1, db_path=db_path,
        )
        watcher.start()
        watcher.start()  # should not error
        watcher.stop()
