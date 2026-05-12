"""tests/test_watcher.py -- DirectoryWatcher unit tests

Tests the watcher's discover + meta + index pipeline with mocked LLM/embed.
Does NOT test actual LLM calls or process_traj (those are integration tests).

历史注记：watcher 在 fce9146 那版是同步 `_poll_once`，后来改成异步
ThreadPoolExecutor 流水线，入口改名 `_scan_once`，且 meta 提取等耗时步骤
被 submit 到 self._pool。这些测试现在通过 ``_drain`` 把 pending future
全部跑完再 assert，以契合新行为。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xskill.registry import register_dir, discover_trajectories, get_unindexed
from xskill.watcher import DirectoryWatcher


def _drain(watcher: DirectoryWatcher) -> None:
    """等所有提交到 watcher._pool 的 future 跑完，再触发一次 _harvest 把
    收割回调（更新 DB 状态、移除 future 记录）跑齐。供测试用。
    """
    for fut in list(watcher._futures):
        fut.result()
    watcher._harvest()


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
    """Test that _scan_once discovers files and updates DB."""

    def test_discover_new_files(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)

        watcher = DirectoryWatcher(
            llm=None, embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        watcher._scan_once()
        _drain(watcher)

        # File should be discovered (no LLM/embed means no meta extraction).
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
        watcher._scan_once()

        assert watcher.stats["polls"] == 1
        assert watcher.stats["new_trajs"] == 1


class TestWatcherMetaExtraction:
    """Test meta extraction step with mocked _process_one_meta."""

    def test_calls_process_one_meta(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)
        # Pre-discover so the file row already has status='discovered'.
        # Watcher's _scan_dir will then submit a meta-extract future for it.
        discover_trajectories(wid, traj_dir, db_path=db_path)

        mock_llm = MagicMock()

        with patch("xskill.index._process_one_meta") as mock_pom:
            watcher = DirectoryWatcher(
                llm=mock_llm, embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
            )
            watcher._scan_once()
            _drain(watcher)

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
            watcher._scan_once()
            _drain(watcher)

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
            watcher._scan_once()
            _drain(watcher)
            mock_score.assert_not_called()


class TestWatcherZombieCleanup:
    """meta_extracting / processing 这两个 in-flight 中间态如果 daemon
    重启后还留在 DB 里（上一次进程死时 future 被切），新 daemon 进程没有
    对应 in-flight future，必须回退到流水线前一阶段重新调度，否则永远
    卡死。"""

    def test_meta_extracting_zombie_rollback_to_discovered(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)
        discover_trajectories(wid, traj_dir, db_path=db_path)
        # 模拟"上一次 daemon 在跑这条 meta 但进程死了，DB 状态没回滚"
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trajectories SET status='meta_extracting' "
            "WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        )
        conn.commit()
        conn.close()

        watcher = DirectoryWatcher(
            llm=MagicMock(), embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        # 新 watcher 进程 self._futures 是空的——僵尸触发
        with patch("xskill.index._process_one_meta"):
            watcher._scan_once()
            _drain(watcher)

        # 应回退到 discovered，下一轮 watcher 才会重新提交 meta 任务
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status FROM trajectories WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        ).fetchone()
        conn.close()
        # _scan_once 同一轮里清理完僵尸后又提交了新 meta 任务，状态可能是
        # meta_extracting 或 meta_done 等更靠后状态——关键是它**前进**了，
        # 不再卡死在原 meta_extracting。
        assert row["status"] != "meta_extracting" or len(watcher._futures) > 0, (
            f"meta_extracting zombie not rolled back; status={row['status']}, "
            f"futures={len(watcher._futures)}"
        )

    def test_processing_zombie_rollback_to_indexed(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)
        discover_trajectories(wid, traj_dir, db_path=db_path)
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trajectories SET status='processing', has_meta=1, has_embedding=1 "
            "WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        )
        conn.commit()
        conn.close()

        watcher = DirectoryWatcher(
            llm=MagicMock(), embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        with patch("xskill.process.process_traj", return_value={"action": "skip"}):
            watcher._scan_once()
            _drain(watcher)

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status FROM trajectories WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        ).fetchone()
        conn.close()
        # 跟上面同样：要么已经回到 indexed 并被重新提交（status=processing
        # + future 在飞），要么已跑完 → action=skip → indexed
        assert row["status"] != "processing" or len(watcher._futures) > 0

    def test_does_not_touch_active_meta_extracting(self, traj_dir, skill_dir, db_path):
        """有对应 in-flight future 的 meta_extracting **不能**被误清理。"""
        wid = register_dir(traj_dir, db_path=db_path)
        discover_trajectories(wid, traj_dir, db_path=db_path)

        watcher = DirectoryWatcher(
            llm=MagicMock(), embed_client=None, config={},
            skill_dir=skill_dir, poll_interval=1, db_path=db_path,
        )
        # 注一个 in-flight future 进 _futures（模拟正在跑的 meta 任务）
        from concurrent.futures import Future
        fake_fut: Future = Future()
        # 不 set_result —— 让它"未完成"，模拟正在跑
        watcher._futures[fake_fut] = {
            "wd_id": wid, "fname": "traj_0001.md", "stage": "meta",
        }
        # 手动设状态
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trajectories SET status='meta_extracting' "
            "WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        )
        conn.commit()
        conn.close()

        # 跑 _scan_dir 的清理逻辑（不能 _scan_once 因为会去 harvest 这个永远不完成的 future）
        watcher._scan_dir({"id": wid, "path": str(traj_dir), "auto_index": 1})

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status FROM trajectories WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        ).fetchone()
        conn.close()
        assert row["status"] == "meta_extracting", (
            f"active meta_extracting (with in-flight future) should not be touched; "
            f"got status={row['status']}"
        )

        # cleanup: future never completes naturally; release it for test teardown
        fake_fut.set_result((traj_dir.name, False, "test teardown"))


class TestWatcherColdStartGate:
    """冷启动场景：批量发现 N 条 traj 时 process_traj 必须等所有前序
    上完索引才启动，否则 agent 调 search_similar_trajs 看到不完整索引，
    path-B 共识搜索几乎必然失败。"""

    def test_defers_process_when_many_pending_index(self, traj_dir, skill_dir, db_path):
        wid = register_dir(traj_dir, db_path=db_path)
        # 造 5 条新文件（这个 fixture 默认 2 条，再加 3 条让 pending_index 够触发）
        for i in range(3, 6):
            (traj_dir / f"traj_000{i}.md").write_text(
                f"# Traj {i}\nagent did X", encoding="utf-8"
            )
        # pre-discover 让它们全部进 discovered 状态
        discover_trajectories(wid, traj_dir, db_path=db_path)

        # 手动把 1 条改成 indexed —— 模拟"已经过索引的"，
        # 但其它 4 条还 discovered → pending_index=4 > threshold=3
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trajectories SET status='indexed', has_meta=1, has_embedding=1 "
            "WHERE watch_dir_id=? AND filename='traj_0001.md'",
            (wid,),
        )
        conn.commit()
        conn.close()

        # 启动 watcher 时显式给 cold_start_threshold=3
        with patch("xskill.index._process_one_meta"), \
             patch("xskill.process.process_traj") as mock_process:
            watcher = DirectoryWatcher(
                llm=MagicMock(), embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
                cold_start_threshold=3,
            )
            watcher._scan_once()
            # process_traj 应当被 defer——pending=4 >= 3
            for fut in list(watcher._futures):
                fut.result()  # 等其它 future 都跑完
            watcher._harvest()

            # 关键断言：本轮没提交任何 process 阶段的 future
            assert mock_process.call_count == 0, (
                f"cold-start gate failed: process_traj called {mock_process.call_count} times "
                f"while pending pre-index trajs exist"
            )
            assert watcher.stats["cold_start_deferrals"] >= 1

    def test_resumes_process_when_pending_below_threshold(self, traj_dir, skill_dir, db_path):
        """pending_index < threshold 时 process_traj 应当正常提交。"""
        wid = register_dir(traj_dir, db_path=db_path)
        discover_trajectories(wid, traj_dir, db_path=db_path)

        # 把全部 traj 标 indexed
        from xskill.registry import get_connection
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trajectories SET status='indexed', has_meta=1, has_embedding=1 "
            "WHERE watch_dir_id=?",
            (wid,),
        )
        conn.commit()
        conn.close()

        with patch("xskill.process.process_traj", return_value={"action": "skip"}) as mock_process:
            watcher = DirectoryWatcher(
                llm=MagicMock(), embed_client=None, config={},
                skill_dir=skill_dir, poll_interval=1, db_path=db_path,
                cold_start_threshold=3,
            )
            watcher._scan_once()
            for fut in list(watcher._futures):
                fut.result()
            watcher._harvest()

            # pending_index = 0 → 不 defer，process_traj 必被提交
            assert mock_process.call_count >= 1, (
                "process_traj should be submitted when no pending pre-index trajs"
            )


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
