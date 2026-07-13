"""SkillEditAgent 多轮渐进式消化（batch + turn 分支）验收单测
================================================================

背景：``.candidates.yml`` buffer 攒满阈值时曾经把全部候选一次性塞进单个
``agent.run()``——``xskill rebuild --force`` 重放历史 / 团队突然活跃时 buffer
可能攒到几十上百条，单次会话被拖到小时级,且落盘判定弱（agent 半途收笔也会
被当成"成功"整批清空 buffer,未消化的候选静默丢失）。

止血设计（用户拍板，见 PR 描述）：
  1. ``maybe_run`` 对 buffer 做一次快照,按 ``(weightscore 降序, atom_id 升序)``
     切成 ``SKILL_EDIT_BATCH_SIZE`` 条一批,每批一轮全新上下文的 agent.run()。
  2. 除最后一轮外,每轮结束由宿主 Python 代码（不是 agent）commit 到
     ``<branch>_turn<N>`` 分支承接进度；中间轮的 agent 完全拿不到任何终态
     commit 工具——分支推进对它不可见也不可调用（结构性保证）。
  3. 最后一轮开跑前宿主代码把 HEAD 切回原分支（工作区不动），agent 此时才
     拿到终态 commit 工具，一次 commit 落地全部批次的累计改动。
  4. 成功后清理所有 turn 分支；用快照的 atom_id 集合从 buffer 摘除（而非
     清空整个文件）——消化期间新增的候选留给下一次 maybe_run。
  5. 崩溃恢复：发现残留 ``*_turn*`` 分支就重置（不续传），buffer 未清过，
     重新完整跑一遍不丢候选。

本文件验收：
  - 100+ 候选、真实 batch=20：多轮链条产出的 SKILL.md 融合全部批次内容，
    turn 分支跑完后清理干净。
  - 核心安全性质：中间轮不会推进 main / 不会创建 staging，只有最后一轮才推进。
  - 四个终态落地场景（baby 毕业 / main 开 staging / jam 强砍合并——jam 内部
    调用的 ``commit_update_main_branch`` 就是"main 直接更新"这第四种落地
    方式，本仓库现状里它只在 jam 路径可达，因此用同一个 jam 多轮用例覆盖）
    都各有一个多轮用例，共享同一套渐进式循环骨架。
  - 崩溃恢复：残留 turn 分支被重置，重新完整跑一遍，候选不丢不重复消化。
  - 快照语义：消化期间新增的候选本次不处理，运行结束后仍在 buffer 里。
  - ``remove_candidates`` 只摘除快照里的 atom_id，其余原样保留。
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
            "has_commit_baby": "commit_baby_to_main" in self.tools,
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

        if "commit_baby_to_main" in self.tools:
            _call_tool(self.tools["commit_baby_to_main"], skill, "progressive graduate")
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
    def test_100_plus_candidates_batch_20_merges_all_and_cleans_turn_branches(
        self, tmp_path,
    ):
        assert C.SKILL_EDIT_BATCH_SIZE == 20, "本用例故意验证真实默认 batch size"
        skill_dir = _make_baby_skill(tmp_path / "skill", "huge-skill")
        atom_ids = _seed_candidates(skill_dir, 100)

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True

        # 5 轮（100/20）
        assert len(observed) == 5, f"expected 5 turns, got {len(observed)}"

        # 毕业到 main
        assert current_branch(str(skill_dir)) == "main"

        # 正文融合了全部 5 批的标记行，且每批 20 个 atom_id，并集 = 全部 100 个
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        marker_lines = [ln for ln in body.splitlines() if ln.startswith("- round atoms:")]
        assert len(marker_lines) == 5
        seen_atoms: set[str] = set()
        for ln in marker_lines:
            ids = ln.split(":", 1)[1].strip().split(",")
            assert len(ids) == 20
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
    def test_baby_graduate_multi_turn_only_final_round_advances(
        self, tmp_path, small_batch,
    ):
        skill_dir = _make_baby_skill(tmp_path / "skill", "baby-multi")
        _seed_candidates(skill_dir, 10)  # batch=4 → 3 轮 (4,4,2)
        baby_sha_before = run_git(["rev-parse", "baby"], cwd=str(skill_dir))[1].strip()

        observed: list = []
        agent = SkillEditAgent(
            skill_dir=skill_dir, store=None,
            agno_agent_factory=_make_progressive_factory(observed),
            llm_cfg={}, traj_root=tmp_path,
        )
        assert agent.maybe_run() is True
        assert len(observed) == 3

        # 安全性质：只有最后一轮拿到 commit_baby_to_main；baby ref 在此之前
        # 全程原地不动（turn 分支不影响 baby 自己的 tip）。
        for round_info in observed[:-1]:
            assert round_info["has_commit_baby"] is False
            assert round_info["baby_sha"] == baby_sha_before
        assert observed[-1]["has_commit_baby"] is True

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
            llm_cfg={}, traj_root=tmp_path,
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
            llm_cfg={}, traj_root=tmp_path,
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

            def run(self, user_msg, **_kw):
                target = re.search(r"目标 SKILL\.md 路径:\s*(\S+)", user_msg).group(1)
                skill = re.search(r"skill_name:\s*([\w-]+)", user_msg).group(1)

                data = C.load_candidates(skill_dir)
                data, _ = C.add_atom_contribution(data, "atom_injected_concurrently", 5)
                C.save_candidates(skill_dir, data)

                _call_tool(
                    self.tools["write_file"], target,
                    f"---\nname: {skill}\ndescription: v1\nmetadata:\n  version: 1\n---\n# body\n",
                )
                _call_tool(self.tools["commit_baby_to_main"], skill, "v1")
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
        remaining_ids = {c["atom_id"] for c in remaining}
        # 快照里的 3 个已消化摘除；并发期间加进来的第 4 个还在,留给下次 maybe_run
        assert remaining_ids == {"atom_injected_concurrently"}
        assert not (remaining_ids & set(snapshot_ids))


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
