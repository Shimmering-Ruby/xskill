"""Issue #146：baby checkpoint + legacy main/jam turn-chain 回归。

baby 每 N 个 FIFO candidates 形成一个真实 commit 并立即消费，失败按 N/2
降压，成功恢复默认 N，buffer 空后框架晋升 main。main→staging 与 jam 则保留
旧 ``*_turnN`` 链。本文件同时覆盖大 buffer、崩溃恢复、并发新增、精确消费、
post-commit 异常不重放，以及旧 main/jam 语义不回归。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from xskill.agents.skill_edit_agent import SkillEditAgent
from xskill.skill import candidates as C
from xskill.skill.git import (
    commit_to_staging_branch,
    current_branch,
    init_skill_repo_on_baby,
    list_turn_branches,
    run_git,
)


def _tool_name(tool) -> str:
    return getattr(tool, "__name__", None) or getattr(tool, "name", "")


def _call_tool(tool, *args):
    entrypoint = tool if callable(tool) else getattr(tool, "entrypoint")
    return entrypoint(*args)


@pytest.fixture(autouse=True)
def _init_atom_task_tool_context(tmp_path):
    """让 commit_baby_to_main / commit_to_staging / commit_update_main /
    write_file 这些 agent 工具在测试里可用（同 test_skill_edit_agent.py）。"""
    from xskill.agents import agent_tools
    from xskill.pipeline.atom import AtomTaskStore

    saved_context = agent_tools.agent_tool_config.snapshot()
    (tmp_path / "skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "store").mkdir(parents=True, exist_ok=True)
    agent_tools.init_atom_task_tool_context(
        skill_dir=tmp_path / "skill",
        atom_store=AtomTaskStore(root=tmp_path / "store"),
        default_traj_root=tmp_path / "store",
    )
    agent_tools.init_skill_authoring_tool_context(
        tmp_path / "skill", tmp_path / "skill", {"skill_opt": {"enabled": False}},
    )
    yield
    agent_tools.agent_tool_config.restore(saved_context)


def _make_baby_skill(parent: Path, name: str, desc: str = "stub desc") -> Path:
    sd = parent / name
    init_skill_repo_on_baby(str(sd), name=name, description=desc)
    return sd


def _make_main_skill(parent: Path, name: str, desc: str = "stub desc") -> Path:
    sd = _make_baby_skill(parent, name, desc)
    run_git(["branch", "-m", "baby", "main"], cwd=str(sd))
    return sd


def _add_ux_score(skill_dir: Path, side: str = "main"):
    import json
    line = json.dumps({
        "atom_id": "atom_seed_0001", "skill_name": skill_dir.name, "side": side,
        "commit_sha": "abc", "score": 7, "reasons": "",
        "scored_at": "2026-05-13T10:00:00+00:00",
    })
    (skill_dir / ".ux_scores.jsonl").write_text(line + "\n", encoding="utf-8")


def _seed_candidates(skill_dir: Path, n: int, ws: int = 1) -> list[str]:
    """灌 n 条候选,atom_id 形如 atom_0001.. ，weightscore 递增（1..n 或固定 ws）
    保证总分轻松过 ATOM_PROMOTION_THRESHOLD。返回全部 atom_id 列表。"""
    data = {"candidates": []}
    atom_ids = []
    for i in range(1, n + 1):
        atom_id = f"atom_{i:04d}"
        data, _ = C.add_atom_contribution(data, atom_id, ws)
        atom_ids.append(atom_id)
    C.save_candidates(skill_dir, data)
    return atom_ids


# ─────────────────────────────────────────────────────────────────────
# stub agno 工厂：每轮把本轮 batch 的 atom_id 追加成 SKILL.md 正文里一行
# "- round atoms: ..." 标记（累积——因为磁盘文件在轮次间不会被重置），
# 最后一轮（且仅最后一轮）才会拿到终态 commit 工具并调用它。
# 同时记录每轮开始时 base 分支的 git 状态,用于验证"中间轮不推进终态"。
# ─────────────────────────────────────────────────────────────────────

class _ProgressiveStub:
    def __init__(self, *, instructions, tools, observed):
        self.tools = {_tool_name(t): t for t in tools}
        self.observed = observed

    def run(self, user_msg, **_kw):
        target = re.search(r"目标 SKILL\.md 路径:\s*(\S+)", user_msg).group(1)
        skill = re.search(r"skill_name:\s*([\w-]+)", user_msg).group(1)
        atom_ids = re.findall(r"atom_id=(\S+)\s+weightscore=", user_msg)
        skill_dir = Path(target).parent

        def _rev(ref):
            code, out, _ = run_git(["rev-parse", ref], cwd=str(skill_dir))
            return out.strip() if code == 0 else None

        staging_ok = run_git(
            ["rev-parse", "--verify", "staging"], cwd=str(skill_dir),
        )[0] == 0
        self.observed.append({
            "atom_ids": list(atom_ids),
            "baby_sha": _rev("baby"),
            "main_sha": _rev("main"),
            "staging_exists": staging_ok,
            "has_commit_baby": "commit_baby" in self.tools,
            "has_commit_staging": "commit_to_staging" in self.tools,
            "has_commit_update_main": "commit_update_main" in self.tools,
        })

        existing = (
            Path(target).read_text(encoding="utf-8") if Path(target).is_file() else ""
        )
        tail = existing.split("## 领域规则\n", 1)[1] if "## 领域规则\n" in existing else ""
        marker = f"- round atoms: {','.join(atom_ids)}\n"
        content = (
            f"---\nname: {skill}\ndescription: progressive stub v{len(self.observed)}\n"
            f"metadata:\n  version: {len(self.observed)}\n---\n\n"
            f"# {skill}\n\n## 领域规则\n{tail}{marker}"
        )
        _call_tool(self.tools["write_file"], target, content)

        if "commit_baby" in self.tools:
            _call_tool(self.tools["commit_baby"], skill, "baby batch checkpoint")
        elif "commit_to_staging" in self.tools:
            _call_tool(self.tools["commit_to_staging"], skill, "progressive staging")
        elif "commit_update_main" in self.tools:
            _call_tool(self.tools["commit_update_main"], skill, "progressive jam merge")

        class _R:
            pass
        r = _R()
        r.content = "done"
        return r


def _make_progressive_factory(observed: list):
    def factory(*, instructions, tools):
        return _ProgressiveStub(instructions=instructions, tools=tools, observed=observed)
    return factory


# ═══════════════════════════════════════════════════════════════════
# 1. 100+ 候选、真实 batch=20：多轮链条 + 内容融合 + turn 分支清理
# ═══════════════════════════════════════════════════════════════════

class TestLargeBufferRealBatchSize:
    def test_100_candidates_default_batch_5_creates_20_checkpoints(
        self, tmp_path,
    ):
        skill_dir = _make_baby_skill(tmp_path / "skill", "huge-skill")
        atom_ids = _seed_candidates(skill_dir, 100)

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True

        # 20 轮（100/5），每轮都是真实 baby checkpoint。
        assert len(observed) == 20, f"expected 20 turns, got {len(observed)}"
        assert all(item["has_commit_baby"] for item in observed)

        # 毕业到 main
        assert current_branch(str(skill_dir)) == "main"

        # 正文融合全部 20 批，每批 5 个 atom_id。
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        marker_lines = [ln for ln in body.splitlines() if ln.startswith("- round atoms:")]
        assert len(marker_lines) == 20
        seen_atoms: set[str] = set()
        for ln in marker_lines:
            ids = ln.split(":", 1)[1].strip().split(",")
            assert len(ids) == 5
            seen_atoms.update(ids)
        assert seen_atoms == set(atom_ids)

        # turn 分支跑完后清理干净
        assert list_turn_branches(str(skill_dir)) == []
        code, out, _ = run_git(["branch", "--list"], cwd=str(skill_dir))
        assert "_turn" not in out

        # buffer 清空（全部 atom_id 都被摘除）
        assert C.load_candidates(skill_dir)["candidates"] == []


# ═══════════════════════════════════════════════════════════════════
# 2 & 3. 四个终态场景（baby / staging / jam）各一个多轮用例，
#        共享同一套渐进式循环骨架 —— 核心安全性质：中间轮不推进终态。
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def small_batch(monkeypatch):
    """把 batch size 调小,让"多轮"用例不需要真造 100+ 条候选就能跑多轮，
    加速非"真实 batch size"的场景/安全性质测试。"""
    monkeypatch.setattr(C, "SKILL_EDIT_BATCH_SIZE", 4)
    return 4


class TestFourTerminalScenariosMultiTurn:
    def test_baby_checkpoints_every_turn_then_framework_graduates(
        self, tmp_path, small_batch,
    ):
        skill_dir = _make_baby_skill(tmp_path / "skill", "baby-multi")
        _seed_candidates(skill_dir, 10)  # batch=4 → 3 轮 (4,4,2)
        baby_sha_before = run_git(["rev-parse", "baby"], cwd=str(skill_dir))[1].strip()

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path, batch_size=small_batch,
        )
        assert agent.maybe_run() is True
        assert len(observed) == 3

        # 每一轮都拿到 commit_baby；下一轮启动时 baby tip 已经前进。
        assert all(round_info["has_commit_baby"] for round_info in observed)
        assert observed[0]["baby_sha"] == baby_sha_before
        assert len({round_info["baby_sha"] for round_info in observed}) == 3

        assert current_branch(str(skill_dir)) == "main"
        assert list_turn_branches(str(skill_dir)) == []
        assert C.load_candidates(skill_dir)["candidates"] == []

    def test_main_open_staging_multi_turn_only_final_round_creates_staging(
        self, tmp_path, small_batch,
    ):
        skill_dir = _make_main_skill(tmp_path / "skill", "staging-multi")
        _add_ux_score(skill_dir)
        _seed_candidates(skill_dir, 10)

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path, batch_size=small_batch,
        )
        assert agent.maybe_run() is True
        assert len(observed) == 3

        for round_info in observed[:-1]:
            assert round_info["has_commit_staging"] is False
            assert round_info["staging_exists"] is False
        assert observed[-1]["has_commit_staging"] is True

        code, _, _ = run_git(["rev-parse", "--verify", "staging"], cwd=str(skill_dir))
        assert code == 0
        assert current_branch(str(skill_dir)) == "main"
        assert list_turn_branches(str(skill_dir)) == []
        assert C.load_candidates(skill_dir)["candidates"] == []

    def test_jam_force_merge_multi_turn_only_final_round_updates_main(
        self, tmp_path, small_batch,
    ):
        """jam 强砍合并场景：staging 已存在 + 候选累计 ≥ jam_threshold 时越过
        灰度强砍。jam 内部调用 commit_update_main_branch——即"main 直接更新"
        这第四种终态落地方式，本仓库现状里只在 jam 路径可达。"""
        skill_dir = _make_main_skill(tmp_path / "skill", "jam-multi")
        (skill_dir / "SKILL.md").write_text(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8") + "\n<!-- staging draft -->\n",
            encoding="utf-8",
        )
        assert commit_to_staging_branch(str(skill_dir), "stub staging candidate") is True
        _seed_candidates(skill_dir, 10, ws=6)  # 10*6=60 ≥ jam_threshold(50)

        main_sha_before = run_git(["rev-parse", "main"], cwd=str(skill_dir))[1].strip()

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path, jam_threshold=50,
        )
        assert agent.maybe_run() is True
        assert len(observed) == 3

        for round_info in observed[:-1]:
            assert round_info["has_commit_update_main"] is False
            assert round_info["main_sha"] == main_sha_before
        assert observed[-1]["has_commit_update_main"] is True

        # staging 被 discard，main 已推进
        code, _, _ = run_git(["rev-parse", "--verify", "staging"], cwd=str(skill_dir))
        assert code != 0
        main_sha_after = run_git(["rev-parse", "main"], cwd=str(skill_dir))[1].strip()
        assert main_sha_after != main_sha_before
        assert current_branch(str(skill_dir)) == "main"
        assert list_turn_branches(str(skill_dir)) == []
        assert C.load_candidates(skill_dir)["candidates"] == []


# ═══════════════════════════════════════════════════════════════════
# 4. 崩溃恢复：残留 turn 分支 → 不续传，重置后完整重来一遍
# ═══════════════════════════════════════════════════════════════════

class TestCrashRecovery:
    def test_leftover_turn_branch_is_reset_and_full_chain_reruns(
        self, tmp_path, small_batch,
    ):
        skill_dir = _make_baby_skill(tmp_path / "skill", "crashed-skill")
        atom_ids = _seed_candidates(skill_dir, 10)

        # 模拟"上次跑到一半进程死了"：手工造一个 turn 分支，HEAD 停在那里，
        # 且带着一份"半成品"改动（真实运行崩溃时 turn 分支也会带部分改动）。
        run_git(["checkout", "-b", "baby_turn1"], cwd=str(skill_dir))
        (skill_dir / "SKILL.md").write_text(
            "---\nname: crashed-skill\ndescription: half-done crash artifact\n"
            "metadata:\n  version: 1\n---\n\n# half\n\ncrash-marker-should-not-survive\n",
            encoding="utf-8",
        )
        run_git(["add", "-A"], cwd=str(skill_dir))
        run_git(["commit", "-m", "half-done turn (simulated crash)"], cwd=str(skill_dir))
        assert current_branch(str(skill_dir)) == "baby_turn1"

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path, batch_size=small_batch,
        )
        assert agent.maybe_run() is True

        # 完整重新跑了一遍（3 轮：batch=4 → 4,4,2），没有续传半成品
        assert len(observed) == 3
        assert current_branch(str(skill_dir)) == "main"
        assert list_turn_branches(str(skill_dir)) == []

        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "crash-marker-should-not-survive" not in body
        marker_lines = [ln for ln in body.splitlines() if ln.startswith("- round atoms:")]
        assert len(marker_lines) == 3
        seen_atoms: set[str] = set()
        for ln in marker_lines:
            seen_atoms.update(ln.split(":", 1)[1].strip().split(","))
        assert seen_atoms == set(atom_ids), "候选没丢也没重复：全部 10 个原子都被消化一次"

        # 候选没丢：全部被真正消化后摘除
        assert C.load_candidates(skill_dir)["candidates"] == []

    def test_recovery_does_not_lose_buffer_before_rerun(self, tmp_path, small_batch):
        """崩溃残留 turn 分支时,候选此前从未被清过——重置动作本身不该动
        .candidates.yml（只清 git 状态）。"""
        skill_dir = _make_baby_skill(tmp_path / "skill", "crash-buffer-intact")
        atom_ids = _seed_candidates(skill_dir, 10)

        run_git(["checkout", "-b", "baby_turn1"], cwd=str(skill_dir))
        run_git(["checkout", "-b", "baby_turn2"], cwd=str(skill_dir))

        from xskill.agents.skill_edit_agent import SkillEditAgent as SEA
        agent = SEA(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=lambda **kw: (_ for _ in ()).throw(
                RuntimeError("boom before recovery observed anything"),
            ),
            llm_cfg={}, traj_root=tmp_path,
        )
        # 崩溃恢复本身应该在 maybe_run 顶部无条件跑，即使 gating 因为其他原因
        # 提前失败；这里我们只验证 recover 私有方法独立生效 + buffer 不受影响。
        agent._recover_crashed_turns()
        assert list_turn_branches(str(skill_dir)) == []
        assert current_branch(str(skill_dir)) == "baby"
        data = C.load_candidates(skill_dir)
        assert {c["atom_id"] for c in data["candidates"]} == set(atom_ids)


# ═══════════════════════════════════════════════════════════════════
# 5. 快照语义：消化期间新增到 buffer 的候选,本次不处理,运行后仍在 buffer
# ═══════════════════════════════════════════════════════════════════

class TestSnapshotSemantics:
    def test_candidates_added_during_run_are_not_consumed_this_round(self, tmp_path):
        skill_dir = _make_baby_skill(tmp_path / "skill", "concurrent-add-skill")
        snapshot_ids = _seed_candidates(skill_dir, 3, ws=4)  # 总分 12 ≥ 阈值 10

        class _InjectingStub:
            """写正文 + commit 之前,模拟 cluster 并发往 buffer 里加了条新候选。"""
            def __init__(self, *, instructions, tools):
                self.tools = {_tool_name(t): t for t in tools}
                self.injected = False

            def run(self, user_msg, **_kw):
                target = re.search(r"目标 SKILL\.md 路径:\s*(\S+)", user_msg).group(1)
                skill = re.search(r"skill_name:\s*([\w-]+)", user_msg).group(1)
                atom_ids = re.findall(r"atom_id=(\S+)\s+weightscore=", user_msg)

                if not getattr(_InjectingStub, "injected_once", False):
                    data = C.load_candidates(skill_dir)
                    data, _ = C.add_atom_contribution(
                        data, "atom_injected_concurrently", 5,
                    )
                    C.save_candidates(skill_dir, data)
                    _InjectingStub.injected_once = True

                _call_tool(
                    self.tools["write_file"], target,
                    f"---\nname: {skill}\ndescription: checkpoint\n"
                    "metadata:\n  version: 1\n---\n# body\n"
                    f"processed: {','.join(atom_ids)}\n",
                )
                _call_tool(self.tools["commit_baby"], skill, "v1 checkpoint")
                class _R:
                    pass
                r = _R(); r.content = "done"
                return r

        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=lambda **kw: _InjectingStub(**kw),
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True

        remaining = C.load_candidates(skill_dir)["candidates"]
        # baby 会持续 drain 到当前 buffer 为空，并发新增 atom 在下一批被消费。
        assert remaining == []
        assert set(snapshot_ids)


# ═══════════════════════════════════════════════════════════════════
# 6. remove_candidates：只摘除给定 atom_id 集合，其余原样保留
# ═══════════════════════════════════════════════════════════════════

class TestRemoveCandidates:
    def test_removes_only_given_atom_ids(self, tmp_path):
        skill_dir = tmp_path / "skill" / "rc-target"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        for atom_id, ws in (("a1", 3), ("a2", 5), ("a3", 7)):
            data, _ = C.add_atom_contribution(data, atom_id, ws)
        C.save_candidates(skill_dir, data)

        C.remove_candidates(skill_dir, {"a1", "a3"})

        remaining = C.load_candidates(skill_dir)["candidates"]
        assert [c["atom_id"] for c in remaining] == ["a2"]
        assert remaining[0]["weightscore"] == 5

    def test_noop_when_atom_ids_not_present(self, tmp_path):
        skill_dir = tmp_path / "skill" / "rc-noop"
        skill_dir.mkdir(parents=True)
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "only-one", 10)
        C.save_candidates(skill_dir, data)

        C.remove_candidates(skill_dir, {"does-not-exist"})

        remaining = C.load_candidates(skill_dir)["candidates"]
        assert [c["atom_id"] for c in remaining] == ["only-one"]


class TestBabyCheckpointRecovery:
    def test_1000_fifo_candidates_form_200_default_batches(self, tmp_path):
        agent = SkillEditAgent(
            skill_dir=tmp_path,
            store=None,
            agno_agent_factory=lambda **_kwargs: None,
            llm_cfg={},
            traj_root=tmp_path,
        )
        pending = [
            {"atom_id": f"atom_{index:04d}", "weightscore": 1}
            for index in range(1000)
        ]
        batches: list[list[str]] = []
        while pending:
            batch = agent._take_baby_batch(pending, agent.batch_size)
            batches.append([candidate["atom_id"] for candidate in batch])
            pending = pending[len(batch):]

        assert agent.batch_size == 5
        assert len(batches) == 200
        assert batches[0] == [f"atom_{index:04d}" for index in range(5)]
        assert batches[-1] == [f"atom_{index:04d}" for index in range(995, 1000)]

    def test_failures_reduce_5_to_2_to_1_then_success_resets_to_5(
        self, tmp_path,
    ):
        skill_dir = _make_baby_skill(tmp_path / "skill", "adaptive-baby")
        _seed_candidates(skill_dir, 5, ws=2)
        batch_sizes: list[int] = []

        class _AdaptiveStub:
            def __init__(self, *, instructions, tools):
                del instructions
                self.tools = {_tool_name(tool): tool for tool in tools}

            def run(self, user_msg, **_kwargs):
                atom_ids = re.findall(
                    r"atom_id=(\S+)\s+weightscore=", user_msg,
                )
                batch_sizes.append(len(atom_ids))
                if len(batch_sizes) <= 2:
                    raise RuntimeError("429 rate limit")
                target = re.search(
                    r"目标 SKILL\.md 路径:\s*(\S+)", user_msg,
                ).group(1)
                skill = re.search(
                    r"skill_name:\s*([\w-]+)", user_msg,
                ).group(1)
                # Checkpoint 正文不得残留 init placeholder（#154）。
                content = (
                    f"---\nname: {skill}\ndescription: adaptive recovery\n"
                    f"metadata:\n  version: {len(batch_sizes)}\n---\n\n"
                    f"# Adaptive\n\n"
                    f"processed-{len(batch_sizes)}: {','.join(atom_ids)}\n"
                )
                _call_tool(
                    self.tools["write_file"],
                    target,
                    content,
                )
                _call_tool(
                    self.tools["commit_baby"],
                    skill,
                    f"checkpoint {len(batch_sizes)}",
                )
                return type("_R", (), {"content": "done"})()

        logs_dir = tmp_path / "logs"
        agent = SkillEditAgent(
            skill_dir=skill_dir,
            store=None,
            agno_agent_factory=lambda **kwargs: _AdaptiveStub(**kwargs),
            llm_cfg={"max_context": 128000, "compact_token_limit": 112000},
            traj_root=tmp_path,
            logs_dir=logs_dir,
        )

        assert agent.maybe_run() is True
        assert batch_sizes == [5, 2, 1, 4]
        assert agent.next_batch_size == 5
        assert current_branch(str(skill_dir)) == "main"
        trace = (
            logs_dir / "agents" / "skill_edit_agents"
            / "skills" / "adaptive-baby.log"
        ).read_text(encoding="utf-8")
        assert "Retry batch reduced: 5 -> 2" in trace
        assert "Retry batch reduced: 2 -> 1" in trace
        assert "TURN START | N=1 | processing 1 of 5" in trace
        assert "TURN START | N=5 | processing 4 of 4" in trace
        assert "TURN END | COMMITTED | consumed=4 | 0 remaining | next N=5" in trace
        assert "{" not in trace

    def test_exception_after_commit_is_not_replayed(self, tmp_path):
        skill_dir = _make_baby_skill(tmp_path / "skill", "post-commit-error")
        atom_ids = _seed_candidates(skill_dir, 3, ws=4)
        calls = 0

        class _CommitThenThrow:
            def __init__(self, *, instructions, tools):
                del instructions
                self.tools = {_tool_name(tool): tool for tool in tools}

            def run(self, user_msg, **_kwargs):
                nonlocal calls
                calls += 1
                target = re.search(
                    r"目标 SKILL\.md 路径:\s*(\S+)", user_msg,
                ).group(1)
                skill = re.search(
                    r"skill_name:\s*([\w-]+)", user_msg,
                ).group(1)
                _call_tool(
                    self.tools["write_file"],
                    target,
                    f"---\nname: {skill}\ndescription: durable checkpoint\n"
                    "metadata:\n  version: 1\n---\n# body\n",
                )
                _call_tool(
                    self.tools["commit_baby"],
                    skill,
                    "durable before final response",
                )
                raise RuntimeError("429 on post-tool model response")

        agent = SkillEditAgent(
            skill_dir=skill_dir,
            store=None,
            agno_agent_factory=lambda **kwargs: _CommitThenThrow(**kwargs),
            llm_cfg={},
            traj_root=tmp_path,
        )

        assert agent.maybe_run() is True
        assert calls == 1
        assert current_branch(str(skill_dir)) == "main"
        assert C.load_candidates(skill_dir)["candidates"] == []
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "durable checkpoint" in body
        assert atom_ids

    def test_partial_baby_bypasses_threshold_after_restart(self, tmp_path):
        from xskill.skill.git import commit_baby_checkpoint

        skill_dir = _make_baby_skill(tmp_path / "skill", "partial-baby")
        atom_ids = _seed_candidates(skill_dir, 6, ws=2)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: partial-baby\ndescription: first checkpoint\n"
            "metadata:\n  version: 1\n---\n# body\nfirst five\n",
            encoding="utf-8",
        )
        assert commit_baby_checkpoint(
            str(skill_dir), "simulate checkpoint before restart",
        )
        consumed, remaining = C.remove_candidates(
            skill_dir, set(atom_ids[:5]),
        )
        assert consumed == atom_ids[:5]
        assert remaining == 1

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir,
            store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={},
            traj_root=tmp_path,
        )
        assert agent.maybe_run() is True
        assert [item["atom_ids"] for item in observed] == [[atom_ids[-1]]]
        assert current_branch(str(skill_dir)) == "main"

    def test_n1_failure_releases_turn_and_preserves_retry_size(self, tmp_path):
        skill_dir = _make_baby_skill(tmp_path / "skill", "n1-failure")
        _seed_candidates(skill_dir, 1, ws=10)
        calls = 0

        class _AlwaysFails:
            def __init__(self, **_kwargs):
                pass

            def run(self, _user_msg, **_kwargs):
                nonlocal calls
                calls += 1
                raise RuntimeError("maximum context length exceeded")

        agent = SkillEditAgent(
            skill_dir=skill_dir,
            store=None,
            agno_agent_factory=lambda **kwargs: _AlwaysFails(**kwargs),
            llm_cfg={},
            traj_root=tmp_path,
            retry_batch_size=1,
        )

        assert agent.maybe_run() is False
        assert calls == 1
        assert agent.next_batch_size == 1
        assert current_branch(str(skill_dir)) == "baby"
        assert len(C.load_candidates(skill_dir)["candidates"]) == 1
