"""description 触发优化（skill/description_opt.py）单测。

覆盖：
  - train/test split 分层 + 确定性
  - LLM-as-judge 触发判定（stub llm）
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


# ════════════════════════════════════════════════════════════════════
# 工具：造一个最小 skill + 可编程 stub llm
# ════════════════════════════════════════════════════════════════════

def _write_skill(skill_dir: Path, name: str, desc: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = (
        f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  version: 1\n---\n"
        f"\n# {name}\n\nbody content for {name}.\n"
    )
    (skill_dir / "SKILL.md").write_text(md, encoding="utf-8")


class StubLLM:
    """可编程 stub：按 prompt 内容路由到 case-gen / judge / improve / shorten。

    - case_gen_response: 生成 cases 时返回的 JSON 字符串
    - judge_fn(query, desc) -> 选中的 skill name（模拟触发判定）
    - improve_descs: improve loop 每轮依次返回的新 desc（list）
    - shorten_response: shorten prompt 的返回
    """

    def __init__(self, *, case_gen_response, judge_fn,
                 improve_descs=None, shorten_response=None):
        self.case_gen_response = case_gen_response
        self.judge_fn = judge_fn
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
        if "which single skill" in prompt:
            # judge：从 prompt 里抠出 user query，再交给 judge_fn
            chosen = self._judge(prompt)
            return chosen
        return ""

    def _judge(self, prompt: str) -> str:
        # prompt 里 "User query:\n<query>\n\nBased solely" 之间是 query
        marker = "User query:\n"
        i = prompt.find(marker)
        j = prompt.find("\n\nBased solely", i)
        query = prompt[i + len(marker):j].strip()
        # 从 catalog 行里抠当前候选 desc（第一行 "- <name>: <desc>"）
        desc = ""
        for line in prompt.splitlines():
            if line.startswith("- ") and ":" in line:
                desc = line.split(":", 1)[1].strip()
                break
        return self.judge_fn(query, desc)


def _cases_json(specs):
    """specs: [(query, should_trigger, topic), ...] → JSON 字符串。"""
    arr = [{"query": q, "should_trigger": s, "topic": t} for q, s, t in specs]
    return json.dumps(arr)


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
        # 无重叠、无遗漏
        assert len(train) + len(test) == len(cases)


# ════════════════════════════════════════════════════════════════════
# 2. LLM-judge 触发判定
# ════════════════════════════════════════════════════════════════════

class TestJudge:
    def test_trigger_detection_uses_majority(self):
        budget = DO._Budget(max_calls=100)
        # judge 永远返回 my-skill → triggered
        calls = {"n": 0}

        class _LLM:
            def chat(self, prompt, system=""):
                calls["n"] += 1
                return "my-skill"

        score, results = DO._score_description(
            "desc", [{"query": "q", "should_trigger": True, "topic": "t"}],
            _LLM(), budget, "my-skill", [{"name": "o", "description": "d"}],
            runs_per_case=3, exp_dir=Path("/tmp"), tag="x",
        )
        # 3 runs all hit → did_trigger True, should True → pass
        assert score == 1.0
        assert results[0]["did_trigger"] is True
        assert results[0]["passed"] is True

    def test_match_skill_name_none(self):
        assert DO._match_skill_name("none of these fit", ["a", "b"]) == "NONE"
        assert DO._match_skill_name("I'd pick b here", ["a", "b"]) == "b"


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
            raise AssertionError("LLM should not be called when disabled")

    out = DO.optimize_description(
        sd, llm=_LLM(), config={"skill_opt": {"enabled": False}},
    )
    assert out == {"enabled": False}
    # SKILL.md 未变 + 没建实验目录
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
        # improved desc 触发所有 csv，positive；负例永不触发
        if "clean" in desc.lower() or "csv" in desc.lower():
            return "csv-cleaner" if "csv" in query.lower() or "column" in query.lower() else "NONE"
        # 原始 "bad original" 谁都不触发
        return "NONE"

    llm = StubLLM(
        case_gen_response=_cases_json(specs),
        judge_fn=judge,
        improve_descs=["Use this skill to clean and dedupe CSV files and columns."],
    )
    out = DO.optimize_description(
        sd, llm=llm,
        config={"skill_opt": {"runs_per_case": 1, "max_iters": 1}},
    )
    assert out["enabled"] is True

    opt_root = sd / ".description_optimization"
    assert (opt_root / "cases.json").is_file()
    exp_dirs = [d for d in opt_root.iterdir() if d.is_dir()]
    assert len(exp_dirs) == 1
    exp = exp_dirs[0]
    assert (exp / "summary.json").is_file()
    assert (exp / "attempts.jsonl").is_file()
    # 至少一个 {topic}/{job}.json，结构含指定字段
    case_jsons = list(exp.rglob("*.json"))
    case_jsons = [p for p in case_jsons if p.name not in ("summary.json",)]
    sample = None
    for p in exp.iterdir():
        if p.is_dir():
            files = list(p.glob("*.json"))
            if files:
                sample = json.loads(files[0].read_text())
                break
    assert sample is not None
    for key in ("should_trigger", "did_trigger", "query", "topic",
                "triggered_skill", "runs"):
        assert key in sample

    summary = json.loads((exp / "summary.json").read_text())
    assert "split" in summary and "candidates" in summary and "best" in summary
    assert "train" in summary["split"] and "test" in summary["split"]

    # best 写回 frontmatter（improved desc 应胜出原始）
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert "CSV" in fm["description"] or "csv" in fm["description"].lower()
    assert fm["description"] != "bad original"


# ════════════════════════════════════════════════════════════════════
# 6. test 集选优防过拟合：train 偏 A、test 偏 B → 必须选 B
# ════════════════════════════════════════════════════════════════════

def test_selects_by_test_not_train(tmp_path):
    """构造场景：候选 A(=原始) 在 train 上全过、test 上全挂；
    候选 B(=improve 产出) 在 train 上挂、test 上全过。
    test 选优 → 必须选 B（写回 B）。"""
    sd = tmp_path / "skill-x"
    _write_skill(sd, "skill-x", "DESC_A")

    # 8 cases，split(seed=42,0.6) 把哪些进 train/test 是确定的；用 query 名标记。
    # 我们让 judge 的命中规则依赖 (desc, query 所属 split)。
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
    test_q = {c["query"] for c in test}

    def judge(query, desc):
        is_train = query in train_q
        should = query.startswith(("trainpos", "testpos"))
        if desc == "DESC_A":
            # A: train 上完美（pos→trigger, neg→not），test 上全反
            if is_train:
                return "skill-x" if should else "NONE"
            else:
                return "NONE" if should else "skill-x"
        else:  # DESC_B
            # B: test 上完美，train 上全反
            if not is_train:
                return "skill-x" if should else "NONE"
            else:
                return "NONE" if should else "skill-x"

    llm = StubLLM(
        case_gen_response=json.dumps(
            [{"query": q, "should_trigger": s, "topic": t}
             for q, s, t in specs]),
        judge_fn=judge,
        improve_descs=["DESC_B"],
    )
    out = DO.optimize_description(
        sd, llm=llm,
        config={"skill_opt": {"runs_per_case": 1, "max_iters": 1, "seed": 42,
                              "train_frac": 0.6}},
    )
    # B 在 test 上更高 → best 应为 DESC_B
    assert out["best_description"] == "DESC_B"
    fm, _ = FM.parse((sd / "SKILL.md").read_text())
    assert fm["description"] == "DESC_B"
    # sanity：A 的 train 分应高于 B 的 train 分（验证构造正确）
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
