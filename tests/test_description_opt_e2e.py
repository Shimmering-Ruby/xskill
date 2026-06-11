"""description 触发优化 端到端（stub llm + stub 探针驱动全链路 + commit 集成）。

不依赖真实网络 LLM/agno——stub 一个含 .chat 的对象驱动 case-gen/improve，
stub 一个探针工厂模拟"代理调了哪个 skill"。验证：best 写回 frontmatter、
archival 落齐、触发率入库；并验证 commit 工具内部确实调了优化器、且
.description_optimization/ 不会被 git stage。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xskill.skill import description_opt as DO
from xskill.skill import frontmatter as FM
from xskill.skill import trigger_probe as TP
from xskill.skill.git import init_skill_repo_on_baby, run_git, _stage_all, _open_repo


@pytest.fixture(autouse=True)
def _restore_skill_tools_ctx():
    """本文件里有用例调 init_context/init_context_v2，会污染模块级 _ctx——
    其它测试依赖 _ctx 未初始化。跑完恢复快照，避免跨文件状态泄漏。"""
    from xskill.agents import skill_tools as ST
    snap = dict(ST._ctx)
    snap_v2 = dict(ST._ctx_v2)
    yield
    ST._ctx.clear()
    ST._ctx.update(snap)
    ST._ctx_v2.clear()
    ST._ctx_v2.update(snap_v2)


class ScriptedLLM:
    """case-gen + improve 的 stub（触发判定不再走 llm，改走探针）。"""

    def __init__(self, cases, improved_desc):
        self._cases_json = json.dumps(cases)
        self._improved = improved_desc

    def chat(self, prompt: str, system: str = "") -> str:
        if "generating evaluation queries" in prompt:
            return self._cases_json
        if "write a new and improved description" in prompt:
            return f"<new_description>{self._improved}</new_description>"
        return ""


def _log_judge(query, desc):
    """log-analyzer 触发逻辑：improved desc（含 'log'）对 log 类 query 触发。"""
    improved = "log" in desc.lower()
    wants = "log" in query.lower() or "error trace" in query.lower()
    if improved and wants:
        return "log-analyzer"
    return "NONE"


class _StubProbeAgent:
    def __init__(self, tools, judge_fn):
        self.tools = tools
        self.judge_fn = judge_fn

    def run(self, query):
        chosen = self.judge_fn(query, self.tools[0].__doc__ or "")
        if chosen and chosen != "NONE":
            target = TP._slug_to_tool(chosen)
            for t in self.tools:
                if t.__name__ == target:
                    try:
                        t()
                    except Exception:
                        pass
                    return


class StubProbeFactory:
    def __init__(self, judge_fn):
        self.judge_fn = judge_fn

    def __call__(self, *, instructions, tools, **kwargs):  # noqa: ARG002
        return _StubProbeAgent(tools, self.judge_fn)


class _DummyEmbed:
    def encode(self, text):  # noqa: ARG002
        raise AssertionError("无 skill_index 时不该 embed")


def _opt(sd, llm, **skill_opt):
    return DO.optimize_description(
        sd, llm=llm, config={"skill_opt": skill_opt},
        agno_agent_factory=StubProbeFactory(_log_judge),
        embed_client=_DummyEmbed(), skill_root=sd.parent,
    )


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
    out = _opt(sd, llm, runs_per_case=1, max_iters=1, seed=42)
    assert out["enabled"] is True
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "log" in fm["description"].lower()
    assert fm["description"] != "does stuff with files"

    opt = sd / ".description_optimization"
    assert (opt / "cases.json").is_file()
    exp = [d for d in opt.iterdir() if d.is_dir()][0]
    assert (exp / "summary.json").is_file()
    assert (exp / "attempts.jsonl").is_file()
    lines = [json.loads(l) for l in
             (exp / "attempts.jsonl").read_text().splitlines() if l.strip()]
    assert any(l.get("phase") == "test" for l in lines)
    cands = out["candidates"]
    assert all(c["test_score"] is not None for c in cands)


def test_trigger_eval_persisted(tmp_path):
    """优化跑完，离线触发率落进 registry.skill_trigger_eval。"""
    from xskill.pipeline import registry
    sd = tmp_path / "log-analyzer"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: log-analyzer\ndescription: does stuff\n"
        "metadata:\n  version: 1\n---\n\n# x\n\nbody\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM(_CASES, "Use this skill to analyze log files and error traces.")
    _opt(sd, llm, runs_per_case=1, max_iters=1, seed=42)
    rows = registry.trigger_eval_for_skill("log-analyzer")
    assert len(rows) == 1
    assert rows[0]["skill"] if "skill" in rows[0] else True  # 形状
    assert rows[0]["n_cases"] == 8
    assert 0.0 <= rows[0]["test_score"] <= 1.0


def test_max_llm_calls_cap_does_not_hang(tmp_path):
    sd = tmp_path / "log-analyzer"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: log-analyzer\ndescription: original\n"
        "metadata:\n  version: 1\n---\n\n# x\n\nbody\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM(_CASES, "Use this skill to analyze log files.")
    out = _opt(sd, llm, runs_per_case=1, max_iters=5, max_llm_calls=5, seed=42)
    assert out["enabled"] is True
    assert out["n_llm_calls"] <= 5
    assert isinstance(out["best_description"], str) and out["best_description"]


# ════════════════════════════════════════════════════════════════════
# git: .description_optimization 永不被 stage
# ════════════════════════════════════════════════════════════════════

def test_description_optimization_dir_not_staged_preexisting_repo(tmp_path):
    sd = tmp_path / "skill-g"
    init_skill_repo_on_baby(str(sd), name="skill-g", description="d")
    gi = sd / ".gitignore"
    gi.write_text("# old repo without the entry\n.lock\n", encoding="utf-8")
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

    # 生产流里 _run_description_optimization 现建 make_default_factory(真 agno)——
    # 测试 monkeypatch 成 stub 探针工厂。
    monkeypatch.setattr(
        "xskill.agents.agno_factory.make_default_factory",
        lambda config: StubProbeFactory(_log_judge),
    )

    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    sd = skill_root / "log-analyzer"
    init_skill_repo_on_baby(str(sd), name="log-analyzer",
                            description="does stuff with files")

    ST.init_context_v2(skill_dir=skill_root,
                       store=AtomTaskStore(root=tmp_path / "store"),
                       embed_client=None, traj_root=tmp_path / "store")
    llm = ScriptedLLM(
        _CASES, "Use this skill to analyze and parse log files and error traces.")
    ST.init_context(skill_root, skill_root, llm, _DummyEmbed(),
                    {"skill_opt": {"runs_per_case": 1, "max_iters": 1, "seed": 42}})

    res = ST.commit_baby_to_main("log-analyzer", "v1: based on atom_x")
    assert res.startswith("graduated"), res
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "log" in fm["description"].lower()
    code, body, _ = run_git(["show", "main:SKILL.md"], cwd=str(sd))
    assert code == 0
    fm2, _ = FM.parse(body)
    assert "log" in fm2["description"].lower()


def test_commit_optimization_failure_does_not_block_commit(tmp_path):
    """优化器抛错（case-gen 阶段网络炸）→ commit 仍正常完成（best-effort）。"""
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

    ST.init_context(skill_root, skill_root, _BoomLLM(), _DummyEmbed(),
                    {"skill_opt": {"runs_per_case": 1, "max_iters": 1}})

    res = ST.commit_baby_to_main("boom", "v1")
    assert res.startswith("graduated"), res
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert fm["description"] == "orig desc"
