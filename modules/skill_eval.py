"""
skill_eval.py — Skill 两层评估
══════════════════════════════
Tier 1: SWE-bench 沙箱 A/B/C (有 instance 时)
Tier 2: LLM 多次打分取中位数 (常态)
"""

import json, re, logging, statistics
from pathlib import Path

logger = logging.getLogger("skill_eval")

SCORING_PROMPT = """你是一名严格的 AI Agent Skill 质量评审官。你的职责是判断一个 skill 是否达到生产可用标准。
你必须像审核生产代码一样严格——宁可打低分让 skill 被拒绝重写，也不要放过质量不达标的 skill。

━━ 评审材料 ━━

## Skill 内容
{skill_md}

## 该 skill 关联的原始轨迹（供交叉验证）
{sample_traj_md}

━━ 评分维度 ━━

请从以下 7 个维度独立评分（1-10 分），并在每个标签内先写一句话评语再写分数，格式为"评语|分数"。
你必须严格对照下面每个分数的特征描述来打分，不允许跳档或模糊处理。

<trigger_precision>评语|X</trigger_precision>
触发条件（trigger）的精度。逐分标准：
- 1分：没有 trigger 部分，或 trigger 是空话（如"当遇到问题时"）
- 2分：trigger 存在但完全无法区分场景（如"修复 Python bug"）
- 3分：trigger 指向了一个大的技术领域但没有任何场景限定（如"Django 表单相关问题"）
- 4分：trigger 提到了具体模块但场景描述模糊，可能误触（如"MoneyWidget 的问题"）
- 5分：trigger 描述了一个具体场景但缺少关键限定条件（如只说了"验证失败"但没说触发条件）
- 6分：trigger 场景清晰，但正向和反向边界不够明确，偶尔会误触
- 7分：trigger 场景清晰且有一个明确的限定条件，误触概率低
- 8分：trigger 有两个以上限定条件，精确圈定了适用范围
- 9分：trigger 精确到了错误模式级别（如"方法中存在提前 return 导致后续逻辑不可达"），几乎不会误触
- 10分：trigger 同时描述了正向匹配条件和反向排除条件，精度无可挑剔

<step_actionability>评语|X</step_actionability>
步骤的可执行性——一个从未见过此问题的 agent 拿到这个 skill，能否不靠猜测直接执行？逐分标准：
- 1分：没有 steps 部分，或 steps 只有一句"修复这个问题"
- 2分：steps 是纯自然语言叙述，没有任何具体操作（如"理解代码然后修改"）
- 3分：steps 有方向但无具体操作（如"找到相关文件，修复 bug"）
- 4分：steps 提到了文件名或模块名，但具体改什么、怎么改不清楚
- 5分：steps 有文件定位+笼统修改描述，agent 需要自己判断改哪里
- 6分：大部分步骤具体，但有 2-3 步需要 agent 自行推断
- 7分：大部分步骤具体到工具+操作，仅 1 步需要 agent 判断
- 8分：每步都指明了工具/命令和操作对象，agent 基本可以机械执行
- 9分：每步都有工具+操作+预期结果，agent 可以验证每步是否正确
- 10分：步骤精确到了行号/函数名/具体代码变更，完全可机械执行且可验证

<granularity>评语|X</granularity>
粒度是否合理——这个 skill 的范围是否恰到好处？逐分标准：
- 1分：粒度完全不合理（如一个 skill 试图覆盖"所有编程问题"）
- 2分：粒度极粗，覆盖了一整个技术栈（如"Django 开发"）
- 3分：粒度过粗，覆盖了一个大的问题类别（如"所有表单验证问题"）
- 4分：粒度偏粗，覆盖范围过大导致 steps 只能写得很泛
- 5分：粒度偏细，只适用于一个特定项目的特定文件，但问题模式本身有一定通用性
- 6分：粒度基本合理，覆盖一类问题，但边界有些模糊
- 7分：粒度合理，能复用于 3-5 个相似场景
- 8分：粒度恰当，问题类别定义清晰，既不过细也不过粗
- 9分：粒度精准，能覆盖一类明确的任务模式，且能预见其适用边界
- 10分：粒度完美，skill 自带清晰的适用/不适用说明

<generalizability>评语|X</generalizability>
通用性——这个 skill 能否迁移到同类但不完全相同的场景？逐分标准：
- 1分：完全是一条特定轨迹的逐步复述，没有任何抽象
- 2分：绑定了特定项目的文件路径和变量名，换个项目完全不适用
- 3分：核心逻辑绑定了特定实现细节，仅"同一个库的同一个 bug"能用
- 4分：有一点抽象但大部分步骤仍硬编码了具体细节
- 5分：核心修复思路可迁移，但步骤描述仍依赖具体文件名/类名
- 6分：步骤中混合了通用逻辑和特定细节，需要适配才能用于其他场景
- 7分：核心判断逻辑已抽象（如"检查是否存在提前 return"），硬编码细节较少
- 8分：步骤以通用模式描述为主，仅在示例中提及具体文件名
- 9分：完全以通用问题模式描述，不依赖任何特定项目细节
- 10分：不仅通用，还提供了不同场景下的变体处理建议

<pitfall_quality>评语|X</pitfall_quality>
Pitfalls（踩坑提醒）的质量。逐分标准：
- 1分：没有 pitfalls 部分
- 2分：pitfalls 存在但是废话（如"注意不要写错代码"）
- 3分：pitfalls 只是重复了 steps 里的内容，没有额外信息
- 4分：pitfalls 描述了一个显而易见的问题，没有实战价值
- 5分：pitfalls 提到了一个真实的坑，但没有说清楚如何规避
- 6分：pitfalls 描述了具体的错误模式，但缺少规避方法
- 7分：pitfalls 有错误模式+规避方法，但只来自一条轨迹的经验
- 8分：pitfalls 综合了多条轨迹的经验，有具体的错误模式和规避方式
- 9分：pitfalls 不仅有错误模式+规避方式，还解释了为什么容易犯这个错
- 10分：pitfalls 是从多次真实失败中提炼的系统性经验，包含错误模式、根因、规避方式和验证方法

<faithfulness>评语|X</faithfulness>
忠实度——skill 内容是否忠实于原始轨迹？逐分标准：
- 1分：skill 内容与轨迹完全无关或严重矛盾
- 2分：skill 的核心结论与轨迹矛盾（如轨迹失败了但 skill 写成成功的方案）
- 3分：skill 编造了轨迹中不存在的操作步骤或工具调用
- 4分：skill 的根因分析与轨迹中实际发现的不一致
- 5分：大体忠实但有 1-2 处过度推断（轨迹中未明确的被写成了确定结论）
- 6分：基本忠实，但有小的细节偏差（如文件路径写错、工具名不准确）
- 7分：忠实于轨迹，偶有合理推断但未标注为推断
- 8分：每个事实性声明都能在轨迹中找到依据
- 9分：完全忠实，且正确区分了"轨迹中明确的"和"推断的"内容
- 10分：完全忠实，无任何偏差，关键结论都标注了轨迹中的依据位置

<structural_quality>评语|X</structural_quality>
结构质量——格式是否规范、逻辑是否通顺？逐分标准：
- 1分：结构严重残缺，缺少两个以上必要部分
- 2分：只有 trigger 或只有 steps，其他部分缺失
- 3分：三段都有但内容极度简略（每段不到一句话）
- 4分：结构完整但逻辑混乱，步骤间有矛盾或顺序不对
- 5分：结构完整，逻辑基本通顺，但有冗余或重复内容
- 6分：结构完整，逻辑通顺，但步骤划分不够清晰
- 7分：trigger/steps/pitfalls 三段齐全，逻辑连贯，无明显问题
- 8分：结构清晰，步骤按阶段划分合理，无冗余无矛盾
- 9分：结构优秀，每个 subgoal 内聚且有明确的输入输出
- 10分：结构完美，可直接作为 agent 执行指令使用，无需任何人工调整

━━ 输出格式 ━━
严格输出 7 个 XML 标签，每个标签内容为"一句话评语|分数"，不要输出其他内容。"""


def _parse_scores(text: str) -> dict:
    dims = {}
    for dim in ["trigger_precision", "step_actionability", "granularity",
                "generalizability", "pitfall_quality", "faithfulness", "structural_quality"]:
        m = re.search(rf'<{dim}>(.*?)</{dim}>', text, re.DOTALL)
        if m:
            content = m.group(1).strip()
            # 解析 "评语|分数" 格式
            parts = content.rsplit('|', 1)
            if len(parts) == 2:
                try:
                    dims[dim] = min(max(int(parts[1].strip()), 1), 10)
                except ValueError:
                    # 尝试从内容中提取数字
                    nums = re.findall(r'\d+', parts[1])
                    if nums:
                        dims[dim] = min(max(int(nums[0]), 1), 10)
            else:
                # fallback: 只有数字
                nums = re.findall(r'\d+', content)
                if nums:
                    dims[dim] = min(max(int(nums[-1]), 1), 10)
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

    # 读 skill.md
    skill_md_path = skill_dir / "skill.md"
    if not skill_md_path.exists():
        return {"tier": "llm", "eval_score": 0, "error": "skill.md not found"}
    skill_md = skill_md_path.read_text(encoding="utf-8")

    # 读一条关联轨迹作示例
    abstract_path = skill_dir / ".abstract"
    sample_traj = "(无关联轨迹)"
    if abstract_path.exists():
        abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
        source_trajs = abstract.get("source_trajs", [])
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
    all_dims = ["trigger_precision", "step_actionability", "granularity",
                "generalizability", "pitfall_quality", "faithfulness", "structural_quality"]
    final_scores = {}
    for dim in all_dims:
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
# Tier 1: SWE-bench 沙箱 (占位)
# ═══════════════════════════════════════════════════════════════════

def eval_swe_sandbox(skill_dir: Path, instances: list[str], log_fn=None) -> dict:
    """
    SWE-bench 沙箱 A/B/C 测试

    需要: pip install swebench, Docker

    TODO: Phase 2 实现
    """
    _log = log_fn or (lambda *a: None)
    _log("  🚧 SWE-bench 沙箱评估尚未实现, fallback LLM 打分")
    return {"tier": "swe_sandbox", "eval_score": 0, "error": "not implemented", "instances": instances}


# ═══════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════

def run_eval(skill_dir: Path, llm_client, n_runs: int = 3, log_fn=None) -> dict:
    """
    两层 eval:
      source_trajs 有 ≥3 个 SWE-bench instance → Tier 1
      否则 → Tier 2 LLM 打分
    """
    _log = log_fn or (lambda *a: None)

    # 检查是否有 SWE-bench instance
    abstract_path = skill_dir / ".abstract"
    swe_instances = []
    if abstract_path.exists():
        abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
        source_trajs = abstract.get("source_trajs", [])
        # 从对应的 traj json 里提取 instance_id
        for traj_ref in source_trajs:
            for data_dir in (skill_dir.parent.parent / "data").iterdir():
                if not data_dir.is_dir():
                    continue
                json_file = data_dir / f"{traj_ref}.json"
                if json_file.exists():
                    traj_json = json.loads(json_file.read_text(encoding="utf-8"))
                    iid = traj_json.get("raw_metadata", {}).get("instance_id", "")
                    if iid:
                        swe_instances.append(iid)
                    break

    swe_instances = list(set(swe_instances))
    _log(f"  🔍 SWE-bench instances 关联: {len(swe_instances)} 个")

    if len(swe_instances) >= 3:
        result = eval_swe_sandbox(skill_dir, swe_instances, log_fn)
        if result.get("eval_score", 0) > 0:
            return result
        _log("  ↪ SWE sandbox 不可用, fallback LLM 打分")

    return eval_llm_scoring(skill_dir, llm_client, n_runs, log_fn)


def should_merge(eval_result: dict, old_eval: dict = None, is_new: bool = True) -> bool:
    """判断是否应该合入 main"""
    score = eval_result.get("eval_score", 0)

    if is_new:
        # 新建 skill: eval_score ≥ 6.0
        return score >= 6.0
    else:
        # 修改 skill: 必须优于上版本
        old_score = old_eval.get("eval_score", 0) if old_eval else 0
        return score > old_score
