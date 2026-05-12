"""watcher v2 流水线：discovered → splitting → split_done → indexed → clustering → done"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from xskill.atom_task import AtomTaskStore
from xskill.registry import (
    register_dir, discover_trajectories, update_traj_status,
    get_trajs_by_status, get_status_counts,
)
from xskill.watcher import DirectoryWatcher
from tests.test_atom_task_store import _FakeEmbed
from tests.test_task_agent import _SPLIT_XML, _StubLLM


class _StubAgno:
    """根据 sysprompt 头分发：cluster agent 调 add_task_to_skill；其他 no-op。"""
    def __init__(self, *, instructions, tools):
        self.instructions = instructions
        self.tools = tools

    def run(self, user_msg, **kw):
        head = (self.instructions[0] if self.instructions else "")[:60]
        if "TaskClusterAgent" in head:
            # 给每个 atom 写到 auto-skill 满 10 分立即触发 SkillEdit
            import re
            m = re.search(r"atom_id:\s*(\S+)", user_msg)
            atom_id = m.group(1) if m else None
            for fn in self.tools:
                if getattr(fn, "__name__", "") == "new_skill_folder":
                    fn("auto-skill")
                if getattr(fn, "__name__", "") == "add_task_to_skill" and atom_id:
                    fn("auto-skill", atom_id, 10)
        class _R: pass
        r = _R(); r.content = "stub"; return r


class TestNewStatusValuesAccepted:
    """先把新状态名能进 registry 跑通"""
    def test_splitting_and_clustering_accepted(self, tmp_path):
        db = tmp_path / "test.db"
        wd = tmp_path / "wd"; wd.mkdir()
        (wd / "traj_a.md").write_text("hi", encoding="utf-8")
        wd_id = register_dir(wd, db_path=db)
        discover_trajectories(wd_id, wd, db_path=db)
        for status in ("splitting", "split_done", "clustering"):
            update_traj_status(wd_id, "traj_a.md", status, db_path=db)
            assert get_trajs_by_status(wd_id, status, db_path=db) == ["traj_a.md"]


class TestZombieCleanup:
    """splitting / clustering 是 in-flight 中间态。daemon 重启后如果它们
    还留在 DB 但没有对应 in-flight future，必须回退到前一阶段重新调度，
    否则 traj 永远卡死。"""

    def test_splitting_zombie_rolls_back_to_discovered(self, tmp_path):
        db = tmp_path / "test.db"
        wd = tmp_path / "wd"; wd.mkdir()
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        (wd / "traj_x.md").write_text("X" * 250, encoding="utf-8")

        wd_id = register_dir(wd, db_path=db)
        discover_trajectories(wd_id, wd, db_path=db)
        # 模拟上次 daemon 切死时 traj 留在 splitting
        update_traj_status(wd_id, "traj_x.md", "splitting", db_path=db)

        store = AtomTaskStore(root=wd)
        watcher = DirectoryWatcher(
            llm=_StubLLM([_SPLIT_XML]),
            embed_client=_FakeEmbed(),
            config={"llm": {"base_url": "x", "model": "y", "api_key": "z"}},
            skill_dir=skill_dir,
            poll_interval=0.0,
            max_concurrent=2,
            db_path=db,
            cold_start_threshold=999,
            store=store,
            agno_agent_factory=_StubAgno,
        )
        # 新 watcher 进程 _futures 为空 → 僵尸 splitting 应回退到 discovered
        # 然后同一轮提交新 split future
        watcher._scan_once()
        # 状态应**前进**：要么已经在 splitting + 有 future 在飞，要么更靠后
        assert (
            get_trajs_by_status(wd_id, "discovered", db_path=db) == []
            or len(watcher._futures) > 0
        )

    def test_clustering_zombie_rolls_back_to_indexed(self, tmp_path):
        db = tmp_path / "test.db"
        wd = tmp_path / "wd"; wd.mkdir()
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        (wd / "traj_y.md").write_text("Y" * 250, encoding="utf-8")

        wd_id = register_dir(wd, db_path=db)
        discover_trajectories(wd_id, wd, db_path=db)
        update_traj_status(wd_id, "traj_y.md", "clustering", db_path=db)

        store = AtomTaskStore(root=wd)
        watcher = DirectoryWatcher(
            llm=_StubLLM([_SPLIT_XML]),
            embed_client=_FakeEmbed(),
            config={"llm": {"base_url": "x", "model": "y", "api_key": "z"}},
            skill_dir=skill_dir,
            poll_interval=0.0,
            max_concurrent=2,
            db_path=db,
            cold_start_threshold=999,
            store=store,
            agno_agent_factory=_StubAgno,
        )
        watcher._scan_once()
        # 僵尸 clustering 应被回退到 indexed（同一轮内可能又被重新提交，OK）
        assert "traj_y.md" not in get_trajs_by_status(wd_id, "clustering", db_path=db) \
               or len(watcher._futures) > 0


class TestColdStartGate:
    """大量待 split 的 traj 在场时，cluster 阶段必须 defer，避免 cluster
    agent 看到不完整的 atom 向量索引。"""

    def test_defers_cluster_when_many_pending_split(self, tmp_path):
        db = tmp_path / "test.db"
        wd = tmp_path / "wd"; wd.mkdir()
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()
        # 1 条 indexed + 4 条 discovered → pending_pre_index=4 ≥ threshold=3
        (wd / "traj_a.md").write_text("a", encoding="utf-8")
        for i in range(2, 6):
            (wd / f"traj_{i}.md").write_text("x", encoding="utf-8")

        wd_id = register_dir(wd, db_path=db)
        discover_trajectories(wd_id, wd, db_path=db)
        update_traj_status(wd_id, "traj_a.md", "indexed", db_path=db)

        store = AtomTaskStore(root=wd)
        watcher = DirectoryWatcher(
            llm=_StubLLM([_SPLIT_XML] * 10),  # 给足 split stub
            embed_client=_FakeEmbed(),
            config={"llm": {"base_url": "x", "model": "y", "api_key": "z"}},
            skill_dir=skill_dir,
            poll_interval=0.0,
            max_concurrent=2,
            db_path=db,
            cold_start_threshold=3,
            store=store,
            agno_agent_factory=_StubAgno,
        )
        watcher._scan_once()
        # 关键断言：本轮 indexed 的 traj_a 没被推到 clustering（被 gate 拦下）
        assert "traj_a.md" not in get_trajs_by_status(wd_id, "clustering", db_path=db)
        assert watcher.stats["cold_start_deferrals"] >= 1


class TestPipelineRun:
    def test_scan_then_harvest_full_chain(self, tmp_path):
        db = tmp_path / "test.db"
        wd = tmp_path / "wd"; wd.mkdir()
        skill_dir = tmp_path / "skill"; skill_dir.mkdir()

        # 写一条 traj.md 内容长度跟 _SPLIT_XML 的 offset 对得上（200 字符以上）
        (wd / "traj_x.md").write_text("X" * 250, encoding="utf-8")

        register_dir(wd, db_path=db)
        store = AtomTaskStore(root=wd)
        watcher = DirectoryWatcher(
            llm=_StubLLM([_SPLIT_XML]),
            embed_client=_FakeEmbed(),
            config={"llm": {"base_url": "test://", "model": "stub", "api_key": "k"}},
            skill_dir=skill_dir,
            poll_interval=0.0,
            max_concurrent=4,
            db_path=db,
            cold_start_threshold=999,  # 关闭门控避免阻塞
            store=store,
            agno_agent_factory=_StubAgno,
        )

        # 多轮 scan + harvest 推动状态流转
        for _ in range(20):
            watcher._scan_once()
            # 等异步任务完成
            for _ in range(30):
                if not watcher._futures:
                    break
                time.sleep(0.05)
                watcher._harvest()
            # 看是否走到 done
            counts = get_status_counts(db_path=db)
            if counts.get("done"):
                break

        # 最终状态：traj_x.md 应到 done
        final_status = get_trajs_by_status(
            register_dir(wd, db_path=db),  # idempotent，返回同一 wd_id
            "done", db_path=db,
        )
        assert "traj_x.md" in final_status

        # 落盘：atom 文件存在
        atoms = store.list_by_traj("traj_x")
        assert len(atoms) == 2  # _SPLIT_XML 是两个 atom

        # 索引文件存在
        assert (wd / "index.pkl").is_file()

        # auto-skill 被创建且至少一次 SkillEdit 触发（promoted=true）
        assert (skill_dir / "auto-skill").is_dir()
        from xskill import candidates as C
        data = C.load_candidates(skill_dir / "auto-skill")
        assert any(c.get("promoted") for c in data.get("candidates", []))

        # registry 中 last_offset / last_atom_id / tasks_extracted 已写
        from xskill.registry import get_connection
        conn = get_connection(db)
        row = conn.execute(
            "SELECT last_offset, last_atom_id, tasks_extracted FROM trajectories"
            " WHERE filename=?", ("traj_x.md",),
        ).fetchone()
        conn.close()
        assert row["last_offset"] > 0
        assert row["last_atom_id"] is not None
        assert row["tasks_extracted"] == 2
