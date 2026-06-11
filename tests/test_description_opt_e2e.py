"""description 触发优化 端到端（stub llm 驱动全链路 + commit 集成）。

不依赖真实网络 LLM——stub 一个含 .chat 的对象驱动 optimize_description，
验证：best 写回 frontmatter、archival 目录落齐；并验证 commit 工具内部确实
调了优化器、且 .description_optimization/ 不会被 git stage。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xskill.skill import description_opt as DO
from xskill.skill import frontmatter as FM
from xskill.skill.git import init_skill_repo_on_baby, run_git, _stage_all, _open_repo


@pytest.fixture(autouse=True)
def _restore_skill_tools_ctx():
    """本文件里有用例调 init_context/init_context_v2，会污染模块级 _ctx——
    其它测试（如 test_skill_repo 的 fail-loud 用例）依赖 _ctx 处于未初始化
    状态。跑完恢复快照，避免跨文件状态泄漏。"""
    from xskill.agents import skill_tools as ST
    snap = dict(ST._ctx)
    snap_v2 = dict(ST._ctx_v2)
    yield
    ST._ctx.clear()
    ST._ctx.update(snap)
    ST._ctx_v2.clear()
    ST._ctx_v2.update(snap_v2)


class ScriptedLLM:
    """全链路 stub：case-gen → judge → improve。improved desc 触发更准。"""

    def __init__(self, cases, improved_desc):
        self._cases_json = json.dumps(cases)
        self._improved = improved_desc

    def chat(self, prompt: str, system: str = "") -> str:
        if "generating evaluation queries" in prompt:
            return self._cases_json
        if "write a new and improved description" in prompt:
            return f"<new_description>{self._improved}</new_description>"
        if "which single skill" in prompt:
            return self._judge(prompt)
        return ""

    def _judge(self, prompt: str) -> str:
        marker = "User query:\n"
        i = prompt.find(marker)
        j = prompt.find("\n\nBased solely", i)
        query = prompt[i + len(marker):j].strip().lower()
        # 当前候选 desc（catalog 第一行）
        desc = ""
        for line in prompt.splitlines():
            if line.startswith("- ") and ":" in line:
                desc = line.split(":", 1)[1].strip().lower()
                break
        improved = "log" in desc  # improved desc 含 "log"
        wants = "log" in query or "error trace" in query
        if improved:
            return "log-analyzer" if wants else "NONE"
        # 原始差 desc：谁都不触发（漏触发）
        return "NONE"


_CASES = [
    {"query": "parse the error log at /var/log/app.log", "should_trigger": True, "topic": "logs"},
    {"query": "find the error trace in my stack output", "should_trigger": True, "topic": "logs"},
    {"query": "summarize what went wrong in this log file", "should_trigger": True, "topic": "logs"},
    {"query": "grep the log for OOM kills", "should_trigger": True, "topic": "logs"},
    {"query": "write a react component for a login form", "should_trigger": False, "topic": "frontend"},
    {"query": "set up a postgres database schema", "should_trigger": False, "topic": "db"},
    {"query": "deploy my app to aws", "should_trigger": False, "topic": "infra"},
    {"query": "explain how merge sort works", "should_trigger": False, "topic": "algo"},
]


def test_full_optimize_writes_back_and_archives(tmp_path):
    sd = tmp_path / "log-analyzer"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: log-analyzer\ndescription: does stuff with files\n"
        "metadata:\n  version: 1\n---\n\n# log-analyzer\n\nAnalyze logs.\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM(
        _CASES, "Use this skill to analyze and parse log files and error traces.")
    out = DO.optimize_description(
        sd, llm=llm,
        config={"skill_opt": {"runs_per_case": 1, "max_iters": 1, "seed": 42}},
    )
    assert out["enabled"] is True
    # best 写回 frontmatter
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "log" in fm["description"].lower()
    assert fm["description"] != "does stuff with files"

    opt = sd / ".description_optimization"
    assert (opt / "cases.json").is_file()
    exp = [d for d in opt.iterdir() if d.is_dir()][0]
    assert (exp / "summary.json").is_file()
    assert (exp / "attempts.jsonl").is_file()
    # attempts.jsonl 至少 2 行（iter0 train + iter1 train），含 test 阶段行
    lines = [json.loads(l) for l in
             (exp / "attempts.jsonl").read_text().splitlines() if l.strip()]
    assert any(l.get("phase") == "test" for l in lines)
    # 每个候选都被 test 评过
    cands = out["candidates"]
    assert all(c["test_score"] is not None for c in cands)


def test_max_llm_calls_cap_does_not_hang(tmp_path):
    """硬上限：把 max_llm_calls 设很小，仍能正常返回（提前停 + 选优）。"""
    sd = tmp_path / "log-analyzer"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: log-analyzer\ndescription: original\n"
        "metadata:\n  version: 1\n---\n\n# x\n\nbody\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM(_CASES, "Use this skill to analyze log files.")
    out = DO.optimize_description(
        sd, llm=llm,
        config={"skill_opt": {"runs_per_case": 1, "max_iters": 5,
                              "max_llm_calls": 5, "seed": 42}},
    )
    assert out["enabled"] is True
    assert out["n_llm_calls"] <= 5
    # 仍写回一个 best_description（不崩）
    assert isinstance(out["best_description"], str) and out["best_description"]


# ════════════════════════════════════════════════════════════════════
# git: .description_optimization 永不被 stage
# ════════════════════════════════════════════════════════════════════

def test_description_optimization_dir_not_staged_preexisting_repo(tmp_path):
    """模拟预先存在的 skill 仓（.gitignore 没有 .description_optimization 条目）：
    硬编码 skip 仍要拦住它，绝不 stage。"""
    sd = tmp_path / "skill-g"
    init_skill_repo_on_baby(str(sd), name="skill-g", description="d")
    # 去掉 .gitignore 里的 .description_optimization 行，模拟旧仓
    gi = sd / ".gitignore"
    gi.write_text("# old repo without the entry\n.lock\n", encoding="utf-8")
    # 落一个优化实验目录
    opt = sd / ".description_optimization" / "001_x" / "logs"
    opt.mkdir(parents=True)
    (opt / "case.json").write_text("{}", encoding="utf-8")
    (sd / ".description_optimization" / "cases.json").write_text("[]", encoding="utf-8")

    with _open_repo(str(sd)) as repo:
        _stage_all(repo, sd)
        index = repo.open_index()
        staged = [p.decode("utf-8") if isinstance(p, bytes) else p for p in index]
    assert not any(".description_optimization" in p for p in staged), staged


def test_commit_baby_to_main_runs_optimization(tmp_path, monkeypatch):
    """commit_baby_to_main 内部确实调了 description 优化（best 写回后再 commit）。"""
    from xskill.agents import skill_tools as ST
    from xskill.pipeline.atom import AtomTaskStore

    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    sd = skill_root / "log-analyzer"
    init_skill_repo_on_baby(str(sd), name="log-analyzer",
                            description="does stuff with files")

    # 注入 v2 + v1 ctx（commit 工具 + 优化器分别读）
    ST.init_context_v2(skill_dir=skill_root,
                       store=AtomTaskStore(root=tmp_path / "store"),
                       embed_client=None, traj_root=tmp_path / "store")
    llm = ScriptedLLM(
        _CASES, "Use this skill to analyze and parse log files and error traces.")
    ST.init_context(skill_root, skill_root, llm, None,
                    {"skill_opt": {"runs_per_case": 1, "max_iters": 1, "seed": 42}})

    res = ST.commit_baby_to_main("log-analyzer", "v1: based on atom_x")
    assert res.startswith("graduated"), res
    # commit 后 SKILL.md 的 desc 被优化过（含 log）
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "log" in fm["description"].lower()
    # main 分支 HEAD 的 SKILL.md 也已是优化后的（desc 进了 commit）
    code, body, _ = run_git(["show", "main:SKILL.md"], cwd=str(sd))
    assert code == 0
    fm2, _ = FM.parse(body)
    assert "log" in fm2["description"].lower()


def test_commit_optimization_failure_does_not_block_commit(tmp_path):
    """优化器抛错 → commit 仍正常完成（best-effort，不阻断）。"""
    from xskill.agents import skill_tools as ST
    from xskill.pipeline.atom import AtomTaskStore

    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    sd = skill_root / "boom"
    init_skill_repo_on_baby(str(sd), name="boom", description="orig desc")

    ST.init_context_v2(skill_dir=skill_root,
                       store=AtomTaskStore(root=tmp_path / "store"),
                       embed_client=None, traj_root=tmp_path / "store")

    class _BoomLLM:
        def chat(self, *a, **k):
            raise RuntimeError("network down")

    ST.init_context(skill_root, skill_root, _BoomLLM(), None,
                    {"skill_opt": {"runs_per_case": 1, "max_iters": 1}})

    res = ST.commit_baby_to_main("boom", "v1")
    assert res.startswith("graduated"), res
    # desc 保持 agent 写的（优化失败回退），但 commit 成功
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert fm["description"] == "orig desc"
