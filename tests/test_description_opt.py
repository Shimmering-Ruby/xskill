"""description 触发优化（skill/description_opt.py）单测。

触发判定已换成真跑代理探针（trigger_probe）；这里用 StubProbeFactory 模拟
"代理调了哪个 skill 工具"，不调真 LLM/agno。

覆盖：
  - train/test split 分层 + 确定性
  - 探针触发判定（majority over runs）
  - <1024 字符硬闸 + 超长重写兜底
  - archival 文件结构（cases.json / summary.json / attempts.jsonl / {topic}/{job}.json）
  - test 集选优（构造 train 偏 A、test 偏 B → 必须选 B）防过拟合
  - enabled=false → no-op
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xskill.skill import description_opt as DO
from xskill.skill import frontmatter as FM
from xskill.skill import trigger_probe as TP


# ════════════════════════════════════════════════════════════════════
# 工具：最小 skill + 可编程 stub llm + stub 探针工厂
# ════════════════════════════════════════════════════════════════════

def _write_skill(skill_dir: Path, name: str, desc: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = (
        f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  version: 1\n---\n"
        f"\n# {name}\n\nbody content for {name}.\n"
    )
    (skill_dir / "SKILL.md").write_text(md, encoding="utf-8")


class StubLLM:
    """case-gen / improve / shorten 三类 prompt 的可编程 stub（触发判定不再走 llm）。"""

    def __init__(self, *, case_gen_response,
                 improve_descs=None, shorten_response=None):
        self.case_gen_response = case_gen_response
        self.improve_descs = list(improve_descs or [])
        self.shorten_response = shorten_response
        self.calls = []

    def chat(self, prompt: str, system: str = "") -> str:
        self.calls.append(prompt)
        if "generating evaluation queries" in prompt:
            return self.case_gen_response
        if "over the 1024-character limit" in prompt:
            return f"<new_description>{self.shorten_response}</new_description>"
        if "write a new and improved description" in prompt:
            if self.improve_descs:
                d = self.improve_descs.pop(0)
                return f"<new_description>{d}</new_description>"
            return "no change"
        return ""


class _StubProbeAgent:
    def __init__(self, tools, judge_fn):
        self.tools = tools
        self.judge_fn = judge_fn

    def run(self, query):
        # tools[0] 是候选 skill 工具，__doc__ = 截断后的候选描述
        candidate_desc = self.tools[0].__doc__ or ""
        chosen = self.judge_fn(query, candidate_desc)  # 返回 skill name 或 "NONE"
        if chosen and chosen != "NONE":
            target = TP._slug_to_tool(chosen)
            for t in self.tools:
                if t.__name__ == target:
                    try:
                        t()
                    except Exception:  # StopAgentRun —— agno 内部会吞
                        pass
                    return


class StubProbeFactory:
    """模拟 agno 工厂：用 judge_fn(query, candidate_desc) 决定调哪个 skill 工具。"""

    def __init__(self, judge_fn):
        self.judge_fn = judge_fn

    def __call__(self, *, instructions, tools, **kwargs):  # noqa: ARG002
        return _StubProbeAgent(tools, self.judge_fn)


class _DummyEmbed:
    def encode(self, text):  # noqa: ARG002
        raise AssertionError("无 skill_index 时不该 embed")


def _cases_json(specs):
    arr = [{"query": q, "should_trigger": s, "topic": t} for q, s, t in specs]
    return json.dumps(arr)


def _opt(sd, llm, judge_fn, **skill_opt):
    """优化入口的测试包装：注入 stub 探针工厂 + dummy embed + skill_root。"""
    return DO.optimize_description(
        sd, llm=llm, config={"skill_opt": skill_opt},
        agno_agent_factory=StubProbeFactory(judge_fn),
        embed_client=_DummyEmbed(), skill_root=sd.parent,
    )


# ════════════════════════════════════════════════════════════════════
# 1. train/test split
# ════════════════════════════════════════════════════════════════════

class TestSplit:
    def test_deterministic(self):
        cases = [{"query": f"q{i}", "should_trigger": i % 2 == 0,
                  "topic": "t"} for i in range(10)]
        a = DO.stratified_split(cases, train_frac=0.6, seed=42)
        b = DO.stratified_split(cases, train_frac=0.6, seed=42)
        assert [c["query"] for c in a[0]] == [c["query"] for c in b[0]]
        assert [c["query"] for c in a[1]] == [c["query"] for c in b[1]]

    def test_stratified_both_classes_in_each_split(self):
        cases = [{"query": f"q{i}", "should_trigger": i % 2 == 0,
                  "topic": "t"} for i in range(10)]
        train, test = DO.stratified_split(cases, train_frac=0.6, seed=42)
        assert any(c["should_trigger"] for c in train)
        assert any(not c["should_trigger"] for c in train)
        assert any(c["should_trigger"] for c in test)
        assert any(not c["should_trigger"] for c in test)
        assert len(train) + len(test) == len(cases)


# ════════════════════════════════════════════════════════════════════
# 2. 探针触发判定（majority over runs）
# ════════════════════════════════════════════════════════════════════

class TestProbeScore:
    def test_trigger_detection_uses_majority(self, tmp_path):
        budget = DO._Budget(max_calls=100)
        factory = StubProbeFactory(lambda q, d: "my-skill")  # 永远触发
        score, results = DO._score_description(
            "desc", [{"query": "q", "should_trigger": True, "topic": "t"}],
            budget, "my-skill", {"q": []},
            runs_per_case=3, exp_dir=tmp_path,
            agno_agent_factory=factory, desc_cap=256, tag="x",
        )
        assert score == 1.0
        assert results[0]["did_trigger"] is True
        assert results[0]["passed"] is True
        assert budget.used == 3  # 每 run 计一次预算

    def test_no_trigger_when_agent_picks_nothing(self, tmp_path):
        budget = DO._Budget(max_calls=100)
        factory = StubProbeFactory(lambda q, d: "NONE")
        score, results = DO._score_description(
            "desc", [{"query": "q", "should_trigger": False, "topic": "t"}],
            budget, "my-skill", {"q": []},
            runs_per_case=1, exp_dir=tmp_path,
            agno_agent_factory=factory, desc_cap=256, tag="x",
        )
        assert score == 1.0  # should_trigger False & 未触发 → pass
        assert results[0]["did_trigger"] is False


# ════════════════════════════════════════════════════════════════════
# 3. <1024 字符硬闸
# ════════════════════════════════════════════════════════════════════

class TestLimit:
    def test_under_limit_unchanged(self):
        budget = DO._Budget(max_calls=10)
        d = "short desc"
        assert DO._enforce_limit(d, None, budget) == d
        assert budget.used == 0

    def test_over_limit_rewritten(self):
        budget = DO._Budget(max_calls=10)
        long_desc = "x" * 1500

        class _LLM:
            def chat(self, prompt, system=""):
                return "<new_description>" + ("y" * 500) + "</new_description>"

        out = DO._enforce_limit(long_desc, _LLM(), budget)
        assert len(out) <= DO.DESC_HARD_LIMIT
        assert out == "y" * 500
        assert budget.used == 1

    def test_over_limit_rewrite_still_long_hard_truncates(self):
        budget = DO._Budget(max_calls=10)
        long_desc = "x" * 1500

        class _LLM:
            def chat(self, prompt, system=""):
                return "<new_description>" + ("y" * 2000) + "</new_description>"

        out = DO._enforce_limit(long_desc, _LLM(), budget)
        assert len(out) == DO.DESC_HARD_LIMIT


# ════════════════════════════════════════════════════════════════════
# 4. enabled=false → no-op
# ════════════════════════════════════════════════════════════════════

def test_disabled_is_noop(tmp_path):
    sd = tmp_path / "my-skill"
    _write_skill(sd, "my-skill", "original desc")

    class _LLM:
        def chat(self, *a, **k):
            raise AssertionError("disabled 时不该调 LLM")

    out = _opt(sd, _LLM(), lambda q, d: "NONE", enabled=False)
    assert out == {"enabled": False}
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert fm["description"] == "original desc"
    assert not (sd / ".description_optimization").exists()


# ════════════════════════════════════════════════════════════════════
# 5. archival 结构 + 写回 frontmatter（端到端 stub）
# ════════════════════════════════════════════════════════════════════

def test_archival_and_writeback(tmp_path):
    sd = tmp_path / "csv-cleaner"
    _write_skill(sd, "csv-cleaner", "bad original")

    specs = [
        ("clean my sales.csv file", True, "csv"),
        ("dedupe rows in data.csv", True, "csv"),
        ("strip whitespace from columns", True, "csv"),
        ("fix encoding in export.csv", True, "csv"),
        ("write a python web server", False, "unrelated"),
        ("deploy to kubernetes", False, "unrelated"),
        ("set up nginx reverse proxy", False, "unrelated"),
        ("explain quicksort", False, "unrelated"),
    ]

    def judge(query, desc):
        if "clean" in desc.lower() or "csv" in desc.lower():
            return ("csv-cleaner"
                    if "csv" in query.lower() or "column" in query.lower()
                    else "NONE")
        return "NONE"  # 原始 "bad original" 谁都不触发

    llm = StubLLM(
        case_gen_response=_cases_json(specs),
        improve_descs=["Use this skill to clean and dedupe CSV files and columns."],
    )
    out = _opt(sd, llm, judge, runs_per_case=1, max_iters=1)
    assert out["enabled"] is True

    opt_root = sd / ".description_optimization"
    assert (opt_root / "cases.json").is_file()
    exp_dirs = [d for d in opt_root.iterdir() if d.is_dir()]
    assert len(exp_dirs) == 1
    exp = exp_dirs[0]
    assert (exp / "summary.json").is_file()
    assert (exp / "attempts.jsonl").is_file()

    sample = None
    for p in exp.iterdir():
        if p.is_dir():
            files = list(p.glob("*.json"))
            if files:
                sample = json.loads(files[0].read_text())
                break
    assert sample is not None
    for key in ("should_trigger", "did_trigger", "query", "topic",
                "triggered_skill", "catalog", "runs"):
        assert key in sample

    summary = json.loads((exp / "summary.json").read_text())
    assert "split" in summary and "candidates" in summary and "best" in summary
    assert "train" in summary["split"] and "test" in summary["split"]

    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "csv" in fm["description"].lower()
    assert fm["description"] != "bad original"


# ════════════════════════════════════════════════════════════════════
# 6. test 集选优防过拟合：train 偏 A、test 偏 B → 必须选 B
# ════════════════════════════════════════════════════════════════════

def test_selects_by_test_not_train(tmp_path):
    sd = tmp_path / "skill-x"
    _write_skill(sd, "skill-x", "DESC_A")

    specs = [
        ("trainpos1", True, "g"),
        ("trainpos2", True, "g"),
        ("trainneg1", False, "g"),
        ("trainneg2", False, "g"),
        ("testpos1", True, "g"),
        ("testpos2", True, "g"),
        ("testneg1", False, "g"),
        ("testneg2", False, "g"),
    ]
    cases = [{"query": q, "should_trigger": s, "topic": t} for q, s, t in specs]
    train, test = DO.stratified_split(cases, train_frac=0.6, seed=42)
    train_q = {c["query"] for c in train}

    def judge(query, desc):
        is_train = query in train_q
        should = query.startswith(("trainpos", "testpos"))
        if desc == "DESC_A":      # A：train 完美、test 全反
            if is_train:
                return "skill-x" if should else "NONE"
            return "NONE" if should else "skill-x"
        # DESC_B：test 完美、train 全反
        if not is_train:
            return "skill-x" if should else "NONE"
        return "NONE" if should else "skill-x"

    llm = StubLLM(case_gen_response=_cases_json(specs), improve_descs=["DESC_B"])
    out = _opt(sd, llm, judge, runs_per_case=1, max_iters=1, seed=42,
               train_frac=0.6)

    assert out["best_description"] == "DESC_B"
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert fm["description"] == "DESC_B"
    cand = {c["iter"]: c for c in out["candidates"]}
    assert cand[0]["train_score"] > cand[1]["train_score"]
    assert cand[1]["test_score"] > cand[0]["test_score"]


# ════════════════════════════════════════════════════════════════════
# 7. cases 缓存复用
# ════════════════════════════════════════════════════════════════════

def test_cases_cached(tmp_path):
    sd = tmp_path / "skill-c"
    _write_skill(sd, "skill-c", "desc")
    opt_root = sd / ".description_optimization"
    opt_root.mkdir(parents=True)
    cached = [{"query": "q1", "should_trigger": True, "topic": "t"},
              {"query": "q2", "should_trigger": False, "topic": "t"}]
    (opt_root / "cases.json").write_text(json.dumps(cached), encoding="utf-8")

    budget = DO._Budget(max_calls=10)

    class _LLM:
        def chat(self, *a, **k):
            raise AssertionError("should not regenerate cases")

    got = DO._load_or_generate_cases(
        opt_root, _LLM(), budget, "skill-c", "desc", "content", 20,
    )
    assert len(got) == 2
    assert budget.used == 0
