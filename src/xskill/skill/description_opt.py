"""
skill/description_opt.py — description 触发优化（确定性 loop）
═══════════════════════════════════════════════════════════════════
把 Anthropic skill-creator 的"description 是唯一触发机制 + 触发准确率优化
+ held-out test 集选优防过拟合"那套，落进 xskill 的 commit workflow。

入口 :func:`optimize_description`，在 ``commit_baby_to_main`` /
``commit_to_staging`` 内部、git commit 之前调用（D1：硬编码进 workflow，
不做 agent tool——优化 loop 是确定性代码，agent 看返回值无意义）。

机制（同 skill-creator ``run_loop.py``）：
  1. case 生成：LLM 产 ~20 条 {query, should_trigger, topic}，缓存到
     ``.description_optimization/cases.json`` 复用。
  2. train/test split：按 should_trigger 分层，60/40，固定 seed。
  3. 触发判定 = LLM-as-judge（D2）：候选 desc + 其它 skill 拼伪
     available_skills catalog，问 LLM"会调哪个 skill"，跑 N 次算触发率。
  4. 进化（improve loop，≤max_iters）：把 train 失败拼进 improve prompt，
     产新候选 desc；<1024 字符硬闸 + 超长重写兜底。
  5. 筛选（D3/D4）：所有候选**按 TEST 集分**选 best（不看 train 分，防
     过拟合）。平手时偏好原始 desc（稳定性）。
  6. best 写回 SKILL.md frontmatter，全程 archive 到
     ``.description_optimization/{exp_id}_{ts}/``（D8）。

成本闸（D7）：max_iters / max_llm_calls 硬上限；走传入的 ``llm``（已含
rate_limit + retry），绝不另起进程/线程。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any

from xskill.skill import frontmatter as fm

logger = logging.getLogger("xskill.skill_edit_agent")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"

# description 硬限制（Claude 截断阈值）
DESC_HARD_LIMIT = 1024

# LLM-judge catalog 里给的通用 decoy skill（other_skill_catalog 为 None 时用），
# 让"选哪个 skill"这个判定不至于只有一个候选可选（那样永远触发）。
_GENERIC_DECOYS = [
    {
        "name": "general-coding-helper",
        "description": "Use this skill for general programming questions, writing "
        "or explaining code, and routine software development tasks that don't "
        "match any more specific skill.",
    },
    {
        "name": "web-search-research",
        "description": "Use this skill when the user needs up-to-date information "
        "from the web, fact-checking, or researching a topic across multiple "
        "online sources.",
    },
]


# ═══════════════════════════════════════════════════════════════════
# prompt 装载
# ═══════════════════════════════════════════════════════════════════

def _load_prompt(name: str) -> str:
    p = _PROMPTS_DIR / name
    return p.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# 公共入口
# ═══════════════════════════════════════════════════════════════════

def optimize_description(
    skill_dir: Path,
    *,
    llm: Any,
    config: dict,
    other_skill_catalog: list[dict] | None = None,
) -> dict:
    """优化 skill_dir/SKILL.md 的 frontmatter.description 触发准确率。

    参数
    ----
    skill_dir:
        skill 子目录（含 SKILL.md）。
    llm:
        ``xskill.utils.llm.LLMClient``（已含 rate_limit + retry）。只用
        ``llm.chat(prompt)``。
    config:
        全局 config dict；本函数只读 ``config["skill_opt"]``。
    other_skill_catalog:
        其它 skill 的 ``[{"name", "description"}, ...]``，拼进伪
        available_skills catalog 提高 LLM-judge 真实度。None → 用通用 decoy。

    返回
    ----
    dict 摘要 ``{"enabled", "best_description", "chosen_reason",
    "candidates", "exp_dir", ...}``；``enabled=False`` 时只返回
    ``{"enabled": False}``（no-op）。
    """
    skill_dir = Path(skill_dir)
    opt_cfg = dict(config.get("skill_opt", {}) or {})

    enabled = opt_cfg.get("enabled", True)
    if not enabled:
        logger.info("skill_opt disabled — skip description optimization (%s)",
                    skill_dir.name)
        return {"enabled": False}

    n_cases = int(opt_cfg.get("n_cases", 20))
    runs_per_case = int(opt_cfg.get("runs_per_case", 3))
    max_iters = int(opt_cfg.get("max_iters", 5))
    max_llm_calls = int(opt_cfg.get("max_llm_calls", 400))
    train_frac = float(opt_cfg.get("train_frac", 0.6))
    seed = int(opt_cfg.get("seed", 42))

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found: {skill_md}")

    fm_dict, body = fm.parse(skill_md.read_text(encoding="utf-8"))
    skill_name = str(fm_dict.get("name") or skill_dir.name).strip()
    current_description = str(fm_dict.get("description") or "").strip()
    skill_content = skill_md.read_text(encoding="utf-8")

    if not current_description:
        raise ValueError(f"SKILL.md 没有 description，无法优化: {skill_md}")

    catalog_others = _resolve_other_catalog(other_skill_catalog, skill_name)

    # LLM 调用计数器（闭包共享，命中 max_llm_calls 抛 _LLMBudgetExhausted）
    budget = _Budget(max_calls=max_llm_calls)

    opt_root = skill_dir / ".description_optimization"
    opt_root.mkdir(parents=True, exist_ok=True)

    # ── 1. case 生成（缓存复用）─────────────────────────────────
    cases = _load_or_generate_cases(
        opt_root, llm, budget, skill_name, current_description,
        skill_content, n_cases,
    )

    # ── 2. train/test split（分层 + 确定性）────────────────────
    train, test = stratified_split(cases, train_frac=train_frac, seed=seed)
    logger.info(
        "description_opt[%s]: %d cases → %d train / %d test",
        skill_name, len(cases), len(train), len(test),
    )

    # 实验目录
    exp_id = _next_exp_id(opt_root)
    ts = time.strftime("%Y%m%d-%H%M%S")
    exp_dir = opt_root / f"{exp_id}_{ts}"
    exp_dir.mkdir(parents=True, exist_ok=True)

    attempts_path = exp_dir / "attempts.jsonl"

    # ── 3+4. 进化（improve loop）─────────────────────────────────
    # 候选 = 原始 desc + 每轮 improve 产出。每个候选先在 train 上评（拿失败
    # 喂下一轮），最后统一在 test 上评选优。
    candidates: list[dict] = []

    def _record_candidate(iteration: int, desc: str) -> dict:
        """评 train，落 attempts.jsonl + per-case json，返回候选条目（test 分后填）。"""
        train_score, train_results = _score_description(
            desc, train, llm, budget, skill_name, catalog_others,
            runs_per_case, exp_dir, tag=f"iter{iteration}_train",
        )
        entry = {
            "iter": iteration,
            "description": desc,
            "train_score": train_score,
            "test_score": None,
            "_train_results": train_results,
        }
        candidates.append(entry)
        _append_jsonl(attempts_path, {
            "iter": iteration, "description": desc,
            "train_score": train_score, "test_score": None,
        })
        logger.info(
            "description_opt[%s] iter %d: train_score=%.3f desc=%r",
            skill_name, iteration, train_score, desc[:80],
        )
        return entry

    try:
        # 原始 desc 作为 iter 0 候选
        _record_candidate(0, current_description)

        improve_tmpl = _load_prompt("improve_description.txt")
        for it in range(1, max_iters + 1):
            prev = candidates[-1]
            scores_summary = (
                f"train_score={prev['train_score']:.3f}, "
                f"{len(train)} train cases"
            )
            scores_detail = _format_scores_detail(
                prev["_train_results"], candidates,
            )
            prompt = improve_tmpl.format(
                skill_name=skill_name,
                current_description=prev["description"],
                scores_summary=scores_summary,
                scores_detail=scores_detail,
                skill_content=skill_content,
            )
            raw = budget.chat(llm, prompt)
            new_desc = _parse_new_description(raw)
            if not new_desc:
                logger.warning(
                    "description_opt[%s] iter %d: LLM 未返回 <new_description>，停止进化",
                    skill_name, it,
                )
                break
            new_desc = _enforce_limit(new_desc, llm, budget)
            _record_candidate(it, new_desc)
    except _LLMBudgetExhausted:
        logger.warning(
            "description_opt[%s]: 命中 max_llm_calls=%d，提前停止进化，"
            "用已有候选选优", skill_name, max_llm_calls,
        )

    # ── 5. 筛选（test 选优；D3/D4）──────────────────────────────
    best = _select_best_on_test(
        candidates, test, llm, budget, skill_name, catalog_others,
        runs_per_case, exp_dir, current_description, attempts_path,
    )

    # ── 6. 写回 frontmatter ─────────────────────────────────────
    best_desc = best["description"]
    if best_desc != current_description:
        fm_dict["description"] = best_desc
        skill_md.write_text(fm.serialize(fm_dict, body), encoding="utf-8")
        logger.info(
            "description_opt[%s]: 写回 best desc (test_score=%.3f): %r",
            skill_name, best["test_score"], best_desc[:80],
        )
    else:
        logger.info(
            "description_opt[%s]: 原始 desc 已是 test 最优 (test_score=%.3f)，不改",
            skill_name, best["test_score"],
        )

    summary = _write_summary(
        exp_dir, skill_name, train, test, candidates, best,
        current_description,
    )
    return {
        "enabled": True,
        "best_description": best_desc,
        "chosen_reason": summary["chosen_reason"],
        "candidates": [
            {"iter": c["iter"], "description": c["description"],
             "train_score": c["train_score"], "test_score": c["test_score"]}
            for c in candidates
        ],
        "exp_dir": str(exp_dir),
        "n_llm_calls": budget.used,
    }


# ═══════════════════════════════════════════════════════════════════
# LLM 预算
# ═══════════════════════════════════════════════════════════════════

class _LLMBudgetExhausted(Exception):
    """命中 max_llm_calls 硬上限。"""


class _Budget:
    """计数每一次 llm.chat 调用，命中上限抛 _LLMBudgetExhausted。"""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0

    def chat(self, llm: Any, prompt: str) -> str:
        if self.used >= self.max_calls:
            raise _LLMBudgetExhausted()
        self.used += 1
        return llm.chat(prompt)


# ═══════════════════════════════════════════════════════════════════
# case 生成 / 缓存
# ═══════════════════════════════════════════════════════════════════

def _load_or_generate_cases(
    opt_root: Path, llm: Any, budget: _Budget, skill_name: str,
    description: str, skill_content: str, n_cases: int,
) -> list[dict]:
    cache = opt_root / "cases.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                logger.info("description_opt[%s]: 复用缓存 cases.json (%d 条)",
                            skill_name, len(data))
                return _normalize_cases(data)
        except (json.JSONDecodeError, OSError):
            logger.warning("cases.json 损坏，重新生成: %s", cache)

    # case_gen.txt 含一段字面 JSON 示例（带花括号），不能用 str.format（会被
    # 当占位符解析）。这里用显式 replace 注入三个占位符，prompt 文本保持原文。
    tmpl = _load_prompt("case_gen.txt")
    prompt = (
        tmpl.replace("{skill_name}", skill_name)
        .replace("{description}", description)
        .replace("{skill_content}", skill_content)
    )
    raw = budget.chat(llm, prompt)
    cases = _parse_cases_json(raw)
    if not cases:
        raise ValueError(
            f"case 生成失败：LLM 未返回合法 JSON 数组（skill={skill_name}）"
        )
    cases = _normalize_cases(cases)[:n_cases]
    cache.write_text(json.dumps(cases, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    logger.info("description_opt[%s]: 生成 %d cases → cases.json",
                skill_name, len(cases))
    return cases


def _parse_cases_json(raw: str) -> list[dict]:
    """从 LLM 回复里抠出 JSON 数组。"""
    text = raw.strip()
    # 去掉 ```json ... ``` 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 找第一个 [ 到最后一个 ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


def _normalize_cases(data: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(data):
        if not isinstance(c, dict):
            continue
        query = str(c.get("query") or "").strip()
        if not query:
            continue
        out.append({
            "query": query,
            "should_trigger": bool(c.get("should_trigger", False)),
            "topic": str(c.get("topic") or "misc").strip() or "misc",
        })
    return out


# ═══════════════════════════════════════════════════════════════════
# train/test split（分层 + 确定性）
# ═══════════════════════════════════════════════════════════════════

def stratified_split(
    cases: list[dict], *, train_frac: float, seed: int,
) -> tuple[list[dict], list[dict]]:
    """按 should_trigger 分层抽样，train_frac 进 train，其余进 test。

    确定性：同 seed + 同 cases 必出同样划分。
    """
    rng = random.Random(seed)
    pos = [c for c in cases if c.get("should_trigger")]
    neg = [c for c in cases if not c.get("should_trigger")]

    def _split_one(group: list[dict]) -> tuple[list[dict], list[dict]]:
        g = list(group)
        rng.shuffle(g)
        n_train = int(round(len(g) * train_frac))
        # 至少各留一个（只要 group ≥ 2）
        if len(g) >= 2:
            n_train = max(1, min(len(g) - 1, n_train))
        return g[:n_train], g[n_train:]

    pos_tr, pos_te = _split_one(pos)
    neg_tr, neg_te = _split_one(neg)
    return pos_tr + neg_tr, pos_te + neg_te


# ═══════════════════════════════════════════════════════════════════
# LLM-as-judge 触发判定
# ═══════════════════════════════════════════════════════════════════

def _resolve_other_catalog(
    other_skill_catalog: list[dict] | None, skill_name: str,
) -> list[dict]:
    if other_skill_catalog:
        out = []
        for s in other_skill_catalog:
            nm = str(s.get("name") or "").strip()
            ds = str(s.get("description") or "").strip()
            if nm and nm != skill_name and ds:
                out.append({"name": nm, "description": ds})
        if out:
            return out
    return list(_GENERIC_DECOYS)


def _judge_trigger(
    llm: Any, budget: _Budget, query: str, skill_name: str,
    candidate_desc: str, catalog_others: list[dict],
) -> str:
    """问 LLM：给定 available_skills catalog + 用户 query，会调哪个 skill？

    返回它选中的 skill name（或 "NONE"）。
    """
    entries = [{"name": skill_name, "description": candidate_desc}]
    entries.extend(catalog_others)
    catalog_lines = "\n".join(
        f"- {e['name']}: {e['description']}" for e in entries
    )
    valid_names = ", ".join(e["name"] for e in entries) + ", NONE"
    prompt = (
        "You are Claude Code deciding which skill (if any) to invoke for a "
        "user's query. Here is the available_skills list:\n\n"
        f"{catalog_lines}\n\n"
        f"User query:\n{query}\n\n"
        "Based solely on the skill names and descriptions, which single skill "
        "would you invoke? If none of them fit, answer NONE.\n"
        f"Respond with ONLY the skill name (one of: {valid_names}), nothing else."
    )
    raw = budget.chat(llm, prompt)
    return _match_skill_name(raw, [e["name"] for e in entries])


def _match_skill_name(raw: str, names: list[str]) -> str:
    """把 LLM 回复归一到某个已知 name 或 NONE。"""
    text = (raw or "").strip()
    low = text.lower()
    # 精确/包含匹配已知 name（取最长名优先，避免子串误命中）
    for nm in sorted(names, key=len, reverse=True):
        if nm.lower() in low:
            return nm
    return "NONE"


def _score_description(
    desc: str, split: list[dict], llm: Any, budget: _Budget,
    skill_name: str, catalog_others: list[dict], runs_per_case: int,
    exp_dir: Path, *, tag: str,
) -> tuple[float, list[dict]]:
    """对一个 split 评 desc：每 case 跑 runs_per_case 次判触发，
    triggered = 命中本 skill ≥ 0.5；case pass = triggered == should_trigger。

    返回 (pass_fraction, per_case_results)。per_case 同时落 {topic}/{job}.json。
    """
    results: list[dict] = []
    if not split:
        return 0.0, results

    n_pass = 0
    for idx, case in enumerate(split):
        query = case["query"]
        should = bool(case["should_trigger"])
        topic = case.get("topic", "misc")
        runs: list[dict] = []
        n_hit = 0
        for _ in range(runs_per_case):
            chosen = _judge_trigger(
                llm, budget, query, skill_name, desc, catalog_others,
            )
            hit = (chosen == skill_name)
            if hit:
                n_hit += 1
            runs.append({"triggered_skill": chosen, "hit": hit})
        did_trigger = (n_hit / runs_per_case) >= 0.5
        passed = (did_trigger == should)
        if passed:
            n_pass += 1
        triggered_skill = skill_name if did_trigger else "NONE"
        rec = {
            "should_trigger": should,
            "did_trigger": did_trigger,
            "passed": passed,
            "query": query,
            "topic": topic,
            "triggered_skill": triggered_skill,
            "runs": runs,
        }
        results.append(rec)
        _write_case_json(exp_dir, topic, f"{tag}_{idx:02d}", rec)

    return n_pass / len(split), results


# ═══════════════════════════════════════════════════════════════════
# improve loop helpers
# ═══════════════════════════════════════════════════════════════════

def _format_scores_detail(
    train_results: list[dict], all_candidates: list[dict],
) -> str:
    """拼 FAILED-TO-TRIGGER / FALSE-TRIGGERS / PREVIOUS-ATTEMPTS 块。"""
    failed_to_trigger = [
        r for r in train_results
        if r["should_trigger"] and not r["did_trigger"]
    ]
    false_triggers = [
        r for r in train_results
        if not r["should_trigger"] and r["did_trigger"]
    ]

    lines: list[str] = []
    lines.append("FAILED-TO-TRIGGER (should have triggered but did not):")
    if failed_to_trigger:
        for r in failed_to_trigger:
            lines.append(f"  - [{r['topic']}] {r['query']}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("FALSE-TRIGGERS (triggered but should NOT have):")
    if false_triggers:
        for r in false_triggers:
            lines.append(f"  - [{r['topic']}] {r['query']}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("PREVIOUS-ATTEMPTS (description → train_score):")
    for c in all_candidates:
        lines.append(f"  - ({c['train_score']:.2f}) {c['description']}")

    return "\n".join(lines)


def _parse_new_description(raw: str) -> str:
    m = re.search(r"<new_description>(.*?)</new_description>", raw or "",
                  re.DOTALL)
    if m:
        return m.group(1).strip().strip('"').strip()
    return ""


def _enforce_limit(desc: str, llm: Any, budget: _Budget) -> str:
    """<1024 字符硬闸：超了调 shorten prompt 一次；仍超则硬截断。"""
    if len(desc) <= DESC_HARD_LIMIT:
        return desc
    tmpl = _load_prompt("shorten_description.txt")
    raw = budget.chat(llm, tmpl.format(description=desc))
    shortened = _parse_new_description(raw)
    if shortened and len(shortened) <= DESC_HARD_LIMIT:
        return shortened
    # 兜底硬截断（绝不放过 >1024 的 desc 进 frontmatter）
    return (shortened or desc)[:DESC_HARD_LIMIT]


# ═══════════════════════════════════════════════════════════════════
# 选优（test 集；D3/D4）
# ═══════════════════════════════════════════════════════════════════

def _select_best_on_test(
    candidates: list[dict], test: list[dict], llm: Any, budget: _Budget,
    skill_name: str, catalog_others: list[dict], runs_per_case: int,
    exp_dir: Path, current_description: str, attempts_path: Path,
) -> dict:
    """每个候选在 TEST 上评分，选 test_score 最高；平手偏好原始 desc。"""
    for c in candidates:
        try:
            test_score, _ = _score_description(
                c["description"], test, llm, budget, skill_name,
                catalog_others, runs_per_case, exp_dir,
                tag=f"iter{c['iter']}_test",
            )
        except _LLMBudgetExhausted:
            # 预算耗尽：未评的候选 test_score 留 None，不参与选优
            logger.warning(
                "description_opt[%s]: test 评估命中预算上限，候选 iter %d 起未评",
                skill_name, c["iter"],
            )
            break
        c["test_score"] = test_score
        _append_jsonl(attempts_path, {
            "iter": c["iter"], "description": c["description"],
            "train_score": c["train_score"], "test_score": test_score,
            "phase": "test",
        })

    evaluated = [c for c in candidates if c["test_score"] is not None]
    if not evaluated:
        # 一个都没评上（极端预算耗尽）→ 守住原始 desc
        original = next(
            (c for c in candidates if c["description"] == current_description),
            candidates[0],
        )
        original["test_score"] = original["test_score"] or 0.0
        return original

    best_score = max(c["test_score"] for c in evaluated)
    tied = [c for c in evaluated if c["test_score"] == best_score]
    # 平手偏好原始 desc（稳定性）
    for c in tied:
        if c["description"] == current_description:
            return c
    # 否则取最早产生的（iter 最小）
    return min(tied, key=lambda c: c["iter"])


# ═══════════════════════════════════════════════════════════════════
# archive
# ═══════════════════════════════════════════════════════════════════

def _next_exp_id(opt_root: Path) -> str:
    n = 0
    for d in opt_root.iterdir():
        if d.is_dir() and "_" in d.name:
            head = d.name.split("_", 1)[0]
            if head.isdigit():
                n = max(n, int(head))
    return f"{n + 1:03d}"


def _slug_topic(topic: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", topic.strip().lower()).strip("_")
    return s or "misc"


def _write_case_json(exp_dir: Path, topic: str, job_name: str,
                     rec: dict) -> None:
    tdir = exp_dir / _slug_topic(topic)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{job_name}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _write_summary(
    exp_dir: Path, skill_name: str, train: list[dict], test: list[dict],
    candidates: list[dict], best: dict, current_description: str,
) -> dict:
    if best["description"] == current_description:
        reason = (
            f"original description kept (test_score={best['test_score']:.3f}, "
            "highest or tied-highest on held-out test set)"
        )
    else:
        reason = (
            f"iter {best['iter']} chosen: highest test_score="
            f"{best['test_score']:.3f} on held-out test set (anti-overfit: "
            "selected by TEST not TRAIN)"
        )
    summary = {
        "skill_name": skill_name,
        "split": {
            "train": [{"query": c["query"],
                       "should_trigger": c["should_trigger"],
                       "topic": c.get("topic")} for c in train],
            "test": [{"query": c["query"],
                      "should_trigger": c["should_trigger"],
                      "topic": c.get("topic")} for c in test],
        },
        "candidates": [
            {"iter": c["iter"], "description": c["description"],
             "train_score": c["train_score"], "test_score": c["test_score"]}
            for c in candidates
        ],
        "best": {
            "iter": best["iter"],
            "description": best["description"],
            "train_score": best["train_score"],
            "test_score": best["test_score"],
        },
        "chosen_reason": reason,
    }
    (exp_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return summary
