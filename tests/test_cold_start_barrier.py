"""冷启动 request/barrier 回归测试。

验证：
  1. standalone rebuild 默认会请求 cold start；team server 默认不请求，但显式
     request/barrier 文件仍可触发。
  2. request 文件存在时，watcher hold 增量 SkillEdit，直到流水线空闲才按现有
     ATOM_PROMOTION_THRESHOLD flush。
  3. 外部 barrier 文件可立即触发一次 flush。
"""
from __future__ import annotations

import threading

from xskill.pipeline.atom import AtomTaskStore
from xskill.pipeline.cold_start import (
    ColdStartController,
    DEFAULT_BARRIER_FILENAME,
    DEFAULT_REQUEST_FILENAME,
    request_cold_start_flush,
)
from xskill.pipeline.registry import register_dir
from xskill.pipeline.runner import DirectoryWatcher
from xskill.skill import candidates as C
from xskill.skill.git import init_skill_repo_on_baby, current_branch
from tests.test_atom_task_store import _FakeEmbed
from tests.test_task_agent import _AutoSplitLLM
from tests.test_skilledit_parallel import _make_barrier_agno


def _seed_baby(skill_root, slug, weights):
    sd = skill_root / slug
    init_skill_repo_on_baby(str(sd), name=slug, description="stub")
    data = {"candidates": []}
    for idx, weightscore in enumerate(weights, start=1):
        data, _ = C.add_atom_contribution(
            data, f"atom_{slug}_{idx:04d}", weightscore,
        )
    C.save_candidates(sd, data)
    return sd


def _serial_factory():
    return _make_barrier_agno(1, threading.Barrier(1))


def _make_watcher(tmp_path, skill_root, cold_start_cfg=None, *, server_mode=False):
    db = tmp_path / "test.db"
    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    register_dir(wd, db_path=db)
    store = AtomTaskStore(root=wd)
    cfg = {"llm": {"base_url": "x", "model": "y", "api_key": "z"}}
    if cold_start_cfg is not None:
        cfg["cold_start"] = cold_start_cfg
    return DirectoryWatcher(
        llm=_AutoSplitLLM(),
        embed_client=_FakeEmbed(),
        config=cfg,
        skill_dir=skill_root,
        poll_interval=0.0,
        max_concurrent=4,
        db_path=db,
        store=store,
        agno_agent_factory=_serial_factory(),
        home_root=tmp_path,
        server_mode=server_mode,
    )


class TestColdStartController:
    def test_default_policy_by_mode(self, tmp_path):
        standalone = ColdStartController.from_config(
            {}, tmp_path, server_mode=False,
        )
        server = ColdStartController.from_config(
            {}, tmp_path, server_mode=True,
        )

        assert standalone.enabled is True
        assert server.enabled is False
        assert standalone.active is False
        assert server.active is False

    def test_explicit_false_disables_file_signals(self, tmp_path):
        cs = ColdStartController.from_config(
            {"cold_start": {"enabled": False}},
            tmp_path,
            server_mode=False,
        )
        (tmp_path / DEFAULT_REQUEST_FILENAME).touch()
        (tmp_path / DEFAULT_BARRIER_FILENAME).touch()

        assert cs.explicitly_disabled is True
        assert cs.active is False
        assert cs.barrier_reached(pipeline_idle=True) is False

    def test_request_waits_for_pipeline_idle(self, tmp_path):
        cs = ColdStartController.from_config({}, tmp_path, server_mode=True)
        request_cold_start_flush({}, tmp_path)

        assert cs.active is True
        assert cs.barrier_reached(pipeline_idle=False) is False
        assert cs.barrier_reached(pipeline_idle=True) is True
        cs.consume_barrier()
        assert not (tmp_path / DEFAULT_REQUEST_FILENAME).exists()

    def test_direct_barrier_flushes_without_idle_wait(self, tmp_path):
        cs = ColdStartController.from_config({}, tmp_path, server_mode=True)
        (tmp_path / DEFAULT_BARRIER_FILENAME).touch()

        assert cs.active is True
        assert cs.barrier_reached(pipeline_idle=False) is True
        cs.consume_barrier()
        assert not (tmp_path / DEFAULT_BARRIER_FILENAME).exists()


class TestColdStartBarrierFlush:
    def test_jam_reproduced_without_cold_start(self, tmp_path):
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        sd = _seed_baby(skill_root, "sk-jam", [3])
        w = _make_watcher(tmp_path, skill_root)

        w._run_skill_edit_step()

        assert current_branch(str(sd)) == "baby"
        assert C.load_candidates(sd)["candidates"]

    def test_request_holds_until_idle_then_flushes_at_existing_threshold(self, tmp_path):
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        sd = _seed_baby(skill_root, "sk-cold", [4, 4, 4])
        w = _make_watcher(tmp_path, skill_root)
        request_cold_start_flush({}, tmp_path)

        w._cold_start_pipeline_idle = lambda: False
        w._run_skill_edit_step()
        assert current_branch(str(sd)) == "baby"
        assert C.load_candidates(sd)["candidates"]

        w._cold_start_pipeline_idle = lambda: True
        w._run_skill_edit_step()
        assert current_branch(str(sd)) == "main"
        assert C.load_candidates(sd)["candidates"] == []
        assert not (tmp_path / DEFAULT_REQUEST_FILENAME).exists()

    def test_direct_barrier_flushes_once(self, tmp_path):
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        first = _seed_baby(skill_root, "sk-first", [5, 5])
        w = _make_watcher(tmp_path, skill_root, server_mode=True)

        (tmp_path / DEFAULT_BARRIER_FILENAME).touch()
        w._run_skill_edit_step()
        assert current_branch(str(first)) == "main"
        assert not (tmp_path / DEFAULT_BARRIER_FILENAME).exists()

        second = _seed_baby(skill_root, "sk-second", [3])
        w._run_skill_edit_step()
        assert current_branch(str(second)) == "baby"
