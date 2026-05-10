"""
skill_eval.py — Skill 两层评估
══════════════════════════════
Tier 1: SWE-bench 沙箱 A/B/C (有 instance 时)
Tier 2: LLM 多次打分取中位数 (常态)
"""

import json, re, logging, statistics
from pathlib import Path

logger = logging.getLogger("skill_eval")

# ─────────────────────────────────────────────────────────────────────────────
# v2 format evaluation dimensions
# ─────────────────────────────────────────────────────────────────────────────
# description_precision  ← renamed from trigger_precision (v1)
# warning_coverage       ← renamed from pitfall_quality    (v1)
DIMENSIONS = [
    "description_precision",
    "step_actionability",
    "granularity",
    "generalizability",
    "warning_coverage",
    "faithfulness",
    "structural_quality",
]

# Old→new name aliases. Kept for two reasons:
#  1) Parsing LLM output that was produced by the legacy prompt
#  2) Reading legacy .abstract.eval_result.scores in migration
LEGACY_ALIASES = {
    "trigger_precision": "description_precision",
    "pitfall_quality": "warning_coverage",
}


SCORING_PROMPT = """你正在评估 SKILL.md 的质量。SKILL.md 是一个面向 agent 运行时的技能文档，由 YAML frontmatter + markdown body 组成。

格式要点（用于评分）：
- frontmatter: name, description, compatibility, metadata
- description 是 agent 路由的主信号：应 pushy 地列出典型用户表述 + 工具依赖
- body: 按执行阶段用 `## <stage-name>` 分节，不应有 `## trigger` 或 `## pitfalls`
- warnings 内联为 `> ⚠️ ...` 出现在相应 step 下，应引用轨迹证据（如 "3/7 failure trajectories showed..."）

SKILL.md 内容：
{skill_md}

该 skill 关联的原始轨迹（供交叉验证 faithfulness / warning_coverage 的证据）：
{sample_traj_md}

请从以下 7 维独立打分（1-10），每维用独立 XML 标签包裹一个 1-10 的整数:

<description_precision>X</description_precision>
<step_actionability>X</step_actionability>
<granularity>X</granularity>
<generalizability>X</generalizability>
<warning_coverage>X</warning_coverage>
<faithfulness>X</faithfulness>
<structural_quality>X</structural_quality>

评分标准细节：
- description_precision（frontmatter `description` 的精度）:
  1分 = 描述空洞，只写 "fix xxx 问题"，无典型表述，无工具依赖
  4分 = 写了一段话但没有列出用户可能的触发表述
  7分 = 列出 2-3 种典型表述 + 边界较清晰
  10分 = 列出 ≥3 种用户触发表述、工具依赖明确、边界清晰（什么场景用 / 什么场景不用）
- step_actionability（body 中步骤的可执行性）:
  1分 = 只有自然语言 ("检查一下")
  4分 = 有方向但无具体命令或路径
  7分 = 大部分步骤有具体工具+操作，少数需 agent 推断
  10分 = 精确到文件名:行号、函数名、命令参数，agent 可直接机械执行
- granularity:
  1分 = 覆盖"所有问题"或只覆盖当前这条轨迹
  4分 = 粒度偏粗/偏细，边界模糊
  7分 = 恰好覆盖一类相似问题
  10分 = 精确到 3-5 个相似场景，自带适用/不适用边界说明
- generalizability:
  1分 = 完全硬编码特定项目路径/库名
  4分 = 有抽象但大部分硬编码
  7分 = 核心逻辑已抽象，仅示例中提及具体项目
  10分 = 模式通用，可迁移到同类项目，且附变体处理建议
- warning_coverage（内联 `> ⚠️` 的证据覆盖度）:
  1分 = 无 `> ⚠️` 或 warning 全是 "记得备份" 式泛泛之谈
  4分 = 有 warning 但未引用轨迹证据
  7分 = 关键 step 大多有 warning，部分引用轨迹证据
  10分 = 关键 step 都有 `> ⚠️` 且引用轨迹证据 (N/M trajs) + 规避方法 + 根因
- faithfulness:
  1分 = 步骤与源轨迹矛盾或凭空发明
  4分 = 大体忠实但有过度推断
  7分 = 每条事实性声明都能在轨迹中找到依据
  10分 = 完全忠实，且正确区分"轨迹中明确的"和"推断的"内容
- structural_quality:
  1分 = frontmatter 缺失 / body 无 `##` 分节 / 步骤未编号
  4分 = frontmatter 部分字段缺失，分节不清
  7分 = frontmatter 完整、有阶段分节、步骤编号
  10分 = frontmatter 完整（name + description + compatibility + metadata）、阶段 `##` 分节清晰、步骤连续编号、代码块格式正确

━━ 输出格式 ━━
严格输出上述 7 个 XML 标签，每个标签内容为一个 1-10 的整数，不要输出其他内容。"""


def _parse_scores(text: str) -> dict:
    """Parse 7-dim scores from LLM output.

    Accepts both the current v2 tag names and the legacy v1 ones
    (``trigger_precision`` / ``pitfall_quality``). New names win if both appear.
    """
    dims: dict = {}

    # Accept any dimension name we know about (current or legacy alias)
    wanted = list(DIMENSIONS) + list(LEGACY_ALIASES.keys())

    for dim in wanted:
        m = re.search(rf'<{dim}>(.*?)</{dim}>', text, re.DOTALL)
        if not m:
            continue
        content = m.group(1).strip()
        # New prompt: content is just an integer. Legacy prompt was "评语|分数".
        parts = content.rsplit('|', 1)
        value: int | None = None
        if len(parts) == 2:
            try:
                value = min(max(int(parts[1].strip()), 1), 10)
            except ValueError:
                nums = re.findall(r'\d+', parts[1])
                if nums:
                    value = min(max(int(nums[0]), 1), 10)
        else:
            nums = re.findall(r'\d+', content)
            if nums:
                value = min(max(int(nums[-1]), 1), 10)

        if value is None:
            continue

        # Canonicalize legacy names, but don't overwrite if new already present.
        canonical = LEGACY_ALIASES.get(dim, dim)
        if canonical not in dims:
            dims[canonical] = value

    return dims


# ═══════════════════════════════════════════════════════════════════
# Tier 2: LLM 打分
# ═══════════════════════════════════════════════════════════════════

def eval_llm_scoring(skill_dir: Path, llm_client, n_runs: int = 3, log_fn=None) -> dict:
    """
    LLM 多次打分，取中位数

    Returns:
        {"tier": "llm", "scores": {...}, "eval_score": float, "runs": int, "all_runs": [...]}
    """
    _log = log_fn or (lambda *a: None)

    # 读 SKILL.md（v2 uppercase；回退到 legacy skill.md）
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        legacy = skill_dir / "skill.md"
        if legacy.exists():
            skill_md_path = legacy
        else:
            return {"tier": "llm", "eval_score": 0, "error": "SKILL.md not found"}
    skill_md = skill_md_path.read_text(encoding="utf-8")

    # 读一条关联轨迹作示例 — 从 frontmatter.metadata.source_trajs
    from xskill.frontmatter import parse as fm_parse
    fm, _body = fm_parse(skill_md)
    sample_traj = "(no linked trajectory)"
    source_trajs = (fm.get("metadata", {}) or {}).get("source_trajs", []) or []
    # legacy fallback: read .abstract if frontmatter has no source_trajs
    if not source_trajs:
        abstract_path = skill_dir / ".abstract"
        if abstract_path.exists():
            try:
                abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
                source_trajs = abstract.get("source_trajs", []) or []
            except Exception:
                pass
    if source_trajs:
        # 尝试找到对应的 traj md
        for traj_ref in source_trajs[:1]:
            # traj_ref 可能是 "traj_0023" 或完整路径
            for data_dir in (skill_dir.parent.parent / "data").iterdir():
                if not data_dir.is_dir():
                    continue
                md_file = data_dir / f"{traj_ref}.md"
                if md_file.exists():
                    sample_traj = md_file.read_text(encoding="utf-8")[:3000]
                    break

    prompt = SCORING_PROMPT.format(skill_md=skill_md[:4000], sample_traj_md=sample_traj)

    # 多次打分
    all_runs = []
    for i in range(n_runs):
        try:
            _log(f"  📊 LLM 打分 run {i+1}/{n_runs}...")
            resp = llm_client.chat(prompt)
            scores = _parse_scores(resp)
            if len(scores) >= 5:  # 7 维中至少解析出 5 个
                all_runs.append(scores)
                short = {k[:4]: v for k, v in scores.items()}
                _log(f"     → {short}")
            else:
                _log(f"     ⚠ 解析不完整 ({len(scores)}/7), 重试...")
                resp = llm_client.chat(prompt)
                scores = _parse_scores(resp)
                if len(scores) >= 5:
                    all_runs.append(scores)
                    short = {k[:4]: v for k, v in scores.items()}
                    _log(f"     → {short} (重试成功)")
        except Exception as e:
            _log(f"     ❌ run {i+1} 失败: {e}")

    if not all_runs:
        return {"tier": "llm", "eval_score": 0, "error": "all runs failed", "runs": 0}

    # 取中位数
    final_scores = {}
    for dim in DIMENSIONS:
        values = [r[dim] for r in all_runs if dim in r]
        final_scores[dim] = statistics.median(values) if values else 0

    eval_score = sum(final_scores.values()) / max(len(final_scores), 1)

    return {
        "tier": "llm",
        "scores": final_scores,
        "eval_score": round(eval_score, 2),
        "runs": len(all_runs),
        "all_runs": all_runs,
    }


# ═══════════════════════════════════════════════════════════════════
# Tier 1: 沙箱评测
# ═══════════════════════════════════════════════════════════════════

def eval_sandbox(
    skill_dir: Path,
    instances: list[str],
    llm_config: dict,
    sandbox_config: dict = None,
    log_fn=None,
) -> dict:
    """
    沙箱 A/B 评测。

    Args:
        skill_dir: skill 目录
        instances: SWE-bench instance_id 列表
        llm_config: LLM 配置（传入容器供 agent 使用）
        sandbox_config: 沙箱配置（n_trials, max_instances, timeout 等）
        log_fn: 日志回调
    """
    _log = log_fn or (lambda *a: None)

    try:
        from xskill.sandbox import get_sandbox
    except ImportError as e:
        _log(f"  沙箱模块不可用: {e}", "eval")
        return {"tier": "swe_sandbox", "eval_score": 0, "error": str(e)}

    cfg = sandbox_config or {}
    n_trials = cfg.get("n_trials", 10)
    max_instances = cfg.get("max_instances", 5)
    timeout = cfg.get("timeout_per_trial", 300)

    # 限制 instance 数量
    instances = instances[:max_instances]

    try:
        sandbox = get_sandbox("swe_smith", timeout=timeout)
        cache_dir = Path.home() / ".cache" / "xskill" / "swe_smith" / "baselines"
        result = sandbox.evaluate(
            skill_dir=skill_dir,
            instance_ids=instances,
            llm_config=llm_config,
            n_trials=n_trials,
            baseline_cache_dir=cache_dir,
            log_fn=log_fn,
        )
        return {
            "tier": "swe_sandbox",
            "eval_score": result.eval_score,
            "baseline_pass_rate": result.baseline_pass_rate,
            "skill_pass_rate": result.skill_pass_rate,
            "delta": result.delta,
            "n_tasks": len(result.ab_results),
            "n_trials": n_trials,
            "details": [
                {"task_id": r.task_id,
                 "baseline_pass@1": r.baseline_pass_at_1,
                 "skill_pass@1": r.skill_pass_at_1}
                for r in result.ab_results
            ],
        }
    except Exception as e:
        _log(f"  沙箱执行失败: {e}", "eval")
        return {"tier": "swe_sandbox", "eval_score": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════

def run_eval(
    skill_dir: Path,
    llm_client,
    n_runs: int = 3,
    log_fn=None,
    config: dict = None,
    force_sandbox: bool = False,
    force_no_sandbox: bool = False,
) -> dict:
    """
    两层 eval:
      Tier 1: 沙箱 A/B（有 SWE-bench instance 且 sandbox enabled）
      Tier 2: LLM 7 维打分（常态）

    Args:
        config: 完整配置（包含 llm 和 sandbox 段），供沙箱传入容器
        force_sandbox: 强制走沙箱（即使 instance 不足）
        force_no_sandbox: 强制跳过沙箱
    """
    _log = log_fn or (lambda *a: None)
    config = config or {}
    sandbox_cfg = config.get("sandbox", {})
    sandbox_enabled = sandbox_cfg.get("enabled", True) and not force_no_sandbox
    trigger_threshold = sandbox_cfg.get("trigger_threshold", 3)

    # 收集 SWE-bench instance_id
    swe_instances = _collect_swe_instances(skill_dir)
    _log(f"  🔍 SWE-bench instances 关联: {len(swe_instances)} 个")

    # Tier 1: 沙箱评测
    if sandbox_enabled and (len(swe_instances) >= trigger_threshold or force_sandbox):
        if swe_instances:
            llm_config = config.get("llm", {})
            result = eval_sandbox(skill_dir, swe_instances, llm_config, sandbox_cfg, log_fn)
            if result.get("eval_score", 0) > 0:
                return result
            _log("  ↪ 沙箱不可用, fallback LLM 打分")

    # Tier 2: LLM 打分
    return eval_llm_scoring(skill_dir, llm_client, n_runs, log_fn)


def _collect_swe_instances(skill_dir: Path) -> list[str]:
    """Collect SWE-bench instance_ids from SKILL.md frontmatter.source_trajs
    (with legacy .abstract fallback) by resolving each traj_id to its JSON."""
    from xskill.frontmatter import parse as fm_parse
    swe_instances: list[str] = []

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_dir / "skill.md"

    source_trajs: list[str] = []
    if skill_md.exists():
        fm, _body = fm_parse(skill_md.read_text(encoding="utf-8"))
        source_trajs = (fm.get("metadata", {}) or {}).get("source_trajs", []) or []

    if not source_trajs:
        abstract_path = skill_dir / ".abstract"
        if abstract_path.exists():
            try:
                abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
                source_trajs = abstract.get("source_trajs", []) or []
            except Exception:
                source_trajs = []
    data_dir = skill_dir.parent.parent / "data"

    if not data_dir.exists():
        return swe_instances

    for traj_ref in source_trajs:
        for d in data_dir.iterdir():
            if not d.is_dir():
                continue
            json_file = d / f"{traj_ref}.json"
            if json_file.exists():
                try:
                    traj_json = json.loads(json_file.read_text(encoding="utf-8"))
                    iid = traj_json.get("raw_metadata", {}).get("instance_id", "")
                    if iid:
                        swe_instances.append(iid)
                except Exception:
                    pass
                break

    return list(set(swe_instances))


def should_merge(eval_result: dict, threshold: float = 6.0) -> bool:
    """门控：eval_score ≥ threshold 即合入 main。

    历史还有 is_new=False 走相对比较的分支，但 process.process_traj 实际从未传过它，
    全是 is_new=True 路径。已删，单签名。"""
    return float(eval_result.get("eval_score", 0)) >= threshold
