"""冷启动 COLD_START 信号回归测试。

验证：
  1. rebuild 写入的单一 ``COLD_START`` 文件是唯一触发入口。
  2. 信号存在时 watcher hold 增量 SkillEdit，直到流水线空闲才按既有
     ATOM_PROMOTION_THRESHOLD flush。
"""
from __future__ import annotations

import threading

from xskill.pipeline.atom import AtomTaskStore
from xskill.pipeline.cold_start import COLD_START_FILENAME, ColdStartSignal
from xskill.pipeline.registry import register_dir
from xskill.pipeline.runner import DirectoryWatcher
from xskill.skill import candidates
from xskill.skill.git import init_skill_repo_on_baby, current_branch
from tests.test_atom_task_store import _FakeEmbed
from tests.test_task_agent import _AutoSplitLLM
from tests.test_skilledit_parallel import _make_barrier_agno


def _seed_baby_skill(skill_root, skill_name, weights):
    skill_directory = skill_root / skill_name
    init_skill_repo_on_baby(
        str(skill_directory), name=skill_name, description="stub",
    )
    candidate_data = {"candidates": []}
    for atom_index, weightscore in enumerate(weights, start=1):
        candidate_data, _ = candidates.add_atom_contribution(
            candidate_data, f"atom_{skill_name}_{atom_index:04d}", weightscore,
        )
    candidates.save_candidates(skill_directory, candidate_data)
    return skill_directory


def _make_watcher(tmp_path, skill_root):
    database_path = tmp_path / "test.db"
    watch_dir_path = tmp_path / "watch-dir"
    watch_dir_path.mkdir(exist_ok=True)
    register_dir(watch_dir_path, db_path=database_path)
    atom_store = AtomTaskStore(root=watch_dir_path)
    config = {"llm": {"base_url": "x", "model": "y", "api_key": "z"}}
    return DirectoryWatcher(
        llm=_AutoSplitLLM(),
        embed_client=_FakeEmbed(),
        config=config,
        skill_dir=skill_root,
        poll_interval=0.0,
        max_concurrent=4,
        db_path=database_path,
        store=atom_store,
        agno_agent_factory=_make_barrier_agno(1, threading.Barrier(1)),
        home_root=tmp_path,
    )


class TestColdStartSignal:
    def test_signal_path_is_fixed_under_home(self, tmp_path):
        cold_start_signal = ColdStartSignal(tmp_path)

        assert cold_start_signal.file_path == tmp_path / COLD_START_FILENAME
        assert cold_start_signal.exists is False

    def test_signal_waits_for_pipeline_idle(self, tmp_path):
        cold_start_signal = ColdStartSignal(tmp_path)
        created_path = cold_start_signal.create()

        assert created_path == tmp_path / COLD_START_FILENAME
        assert cold_start_signal.exists is True
        assert cold_start_signal.ready_to_flush(pipeline_idle=False) is False
        assert cold_start_signal.ready_to_flush(pipeline_idle=True) is True

        cold_start_signal.consume()
        assert cold_start_signal.exists is False


class TestColdStartFlush:
    def test_jam_reproduced_without_cold_start(self, tmp_path):
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        skill_directory = _seed_baby_skill(skill_root, "sk-jam", [3])
        watcher = _make_watcher(tmp_path, skill_root)

        watcher._run_skill_edit_step()

        assert current_branch(str(skill_directory)) == "baby"
        assert candidates.load_candidates(skill_directory)["candidates"]

    def test_signal_holds_until_idle_then_flushes_at_existing_threshold(self, tmp_path):
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        skill_directory = _seed_baby_skill(skill_root, "sk-cold", [4, 4, 4])
        watcher = _make_watcher(tmp_path, skill_root)
        ColdStartSignal(tmp_path).create()

        watcher._cold_start_pipeline_idle = lambda: False
        watcher._run_skill_edit_step()
        assert current_branch(str(skill_directory)) == "baby"
        assert candidates.load_candidates(skill_directory)["candidates"]

        watcher._cold_start_pipeline_idle = lambda: True
        watcher._run_skill_edit_step()
        assert current_branch(str(skill_directory)) == "main"
        assert candidates.load_candidates(skill_directory)["candidates"] == []
        assert not (tmp_path / COLD_START_FILENAME).exists()
