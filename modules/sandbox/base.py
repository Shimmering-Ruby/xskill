"""
sandbox/base.py — 沙箱评测抽象层
════════════════════════════════════
定义 Sandbox 抽象基类和所有数据结构。
任何沙箱实现（SWE-smith、自定义沙箱等）都继承 Sandbox 并实现 load_tasks + run_trial。
"""

from __future__ import annotations

import json, logging, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sandbox")


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TaskSpec:
    """一道评测题"""
    task_id: str                                    # 唯一 ID（如 SWE-smith instance_id）
    problem_statement: str                          # agent 看到的问题描述
    environment: dict = field(default_factory=dict) # 沙箱特定配置（image_name, repo 等）
    gold_patch: Optional[str] = None                # 正确的 diff
    test_commands: list[str] = field(default_factory=list)      # 修复后应 pass 的测试
    regression_tests: list[str] = field(default_factory=list)   # 修复前后都应 pass 的测试


@dataclass
class TrialResult:
    """一次 agent 运行的结果"""
    task_id: str
    passed: bool                            # test_commands 是否全 pass
    regression_ok: bool = True              # regression_tests 是否仍 pass
    duration_seconds: float = 0.0
    error: Optional[str] = None
    agent_patch: Optional[str] = None       # agent 实际产生的 diff


@dataclass
class ABResult:
    """一道题的 A/B 对比结果"""
    task_id: str
    baseline_trials: list[TrialResult] = field(default_factory=list)
    skill_trials: list[TrialResult] = field(default_factory=list)
    baseline_pass_at_1: float = 0.0         # any(baseline passed) → 1.0 else 0.0
    skill_pass_at_1: float = 0.0


@dataclass
class SandboxResult:
    """沙箱评测聚合结果"""
    sandbox_type: str
    ab_results: list[ABResult] = field(default_factory=list)
    baseline_pass_rate: float = 0.0         # 所有 task 的 baseline pass@1 平均
    skill_pass_rate: float = 0.0
    delta: float = 0.0                      # skill - baseline
    eval_score: float = 0.0                 # 映射到 0-10
    metadata: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# 抽象基类
# ═══════════════════════════════════════════════════════════════════

class Sandbox(ABC):
    """
    沙箱抽象基类。

    子类必须实现:
      - name (property): 沙箱标识
      - load_tasks(instance_ids): 加载评测题
      - run_trial(task, skill_md, llm_config): 跑一次 agent

    evaluate() 是模板方法，调 run_trial 做 A/B 对比，子类一般不需要覆写。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """沙箱标识，如 'swe_smith'"""

    @abstractmethod
    def load_tasks(self, instance_ids: list[str]) -> list[TaskSpec]:
        """从数据集加载评测题"""

    @abstractmethod
    def run_trial(
        self,
        task: TaskSpec,
        skill_md: Optional[str],
        llm_config: dict,
    ) -> TrialResult:
        """
        在沙箱中跑一次 agent。

        Args:
            task: 评测题
            skill_md: skill 内容。None 表示 baseline（不注入 skill）
            llm_config: LLM 配置（base_url, model, api_key）供容器内 agent 使用

        Returns:
            TrialResult
        """

    def evaluate(
        self,
        skill_dir: Path,
        instance_ids: list[str],
        llm_config: dict,
        n_trials: int = 10,
        baseline_cache_dir: Optional[Path] = None,
        log_fn=None,
    ) -> SandboxResult:
        """
        A/B 评测模板方法：对每道题跑 n 次 baseline + n 次 with-skill，计算 pass@1 delta。

        Args:
            skill_dir: skill 目录（包含 skill.md）
            instance_ids: 要评测的 instance ID 列表
            llm_config: LLM 配置
            n_trials: 每个条件跑几次（pass@1 的 n）
            baseline_cache_dir: baseline 结果缓存目录，None 则不缓存
            log_fn: 日志回调
        """
        _log = log_fn or (lambda *a: None)

        # 读 skill.md
        skill_md_path = skill_dir / "skill.md"
        if not skill_md_path.exists():
            return SandboxResult(sandbox_type=self.name, eval_score=0.0,
                                 metadata={"error": "skill.md not found"})
        skill_md = skill_md_path.read_text(encoding="utf-8")

        # 加载题目
        tasks = self.load_tasks(instance_ids)
        if not tasks:
            return SandboxResult(sandbox_type=self.name, eval_score=0.0,
                                 metadata={"error": f"no tasks found for {len(instance_ids)} instance_ids"})

        _log(f"  沙箱 [{self.name}]: {len(tasks)} 道题, n={n_trials}", "eval")

        ab_results = []
        for task in tasks:
            _log(f"  📋 {task.task_id}", "eval")

            # ── Baseline ──
            baseline_trials = self._load_or_run_baseline(
                task, llm_config, n_trials, baseline_cache_dir, _log
            )

            # ── With Skill ──
            skill_trials = []
            for i in range(n_trials):
                _log(f"    skill run {i+1}/{n_trials}", "eval")
                try:
                    result = self.run_trial(task, skill_md=skill_md, llm_config=llm_config)
                except Exception as e:
                    logger.error(f"skill trial failed: {e}")
                    result = TrialResult(task_id=task.task_id, passed=False, error=str(e))
                skill_trials.append(result)

            baseline_pass1 = 1.0 if any(t.passed for t in baseline_trials) else 0.0
            skill_pass1 = 1.0 if any(t.passed for t in skill_trials) else 0.0

            _log(f"    → baseline pass@1={baseline_pass1}, skill pass@1={skill_pass1}", "eval")

            ab_results.append(ABResult(
                task_id=task.task_id,
                baseline_trials=baseline_trials,
                skill_trials=skill_trials,
                baseline_pass_at_1=baseline_pass1,
                skill_pass_at_1=skill_pass1,
            ))

        # 聚合
        n_tasks = len(ab_results)
        baseline_rate = sum(r.baseline_pass_at_1 for r in ab_results) / max(n_tasks, 1)
        skill_rate = sum(r.skill_pass_at_1 for r in ab_results) / max(n_tasks, 1)
        delta = skill_rate - baseline_rate

        # delta → eval_score (0-10): delta=0→5, +0.1→6, +0.5→10
        eval_score = max(0.0, min(10.0, 5.0 + delta * 10.0))

        _log(f"  沙箱结果: baseline={baseline_rate:.2f} skill={skill_rate:.2f} "
             f"delta={delta:+.2f} eval_score={eval_score:.1f}", "eval")

        return SandboxResult(
            sandbox_type=self.name,
            ab_results=ab_results,
            baseline_pass_rate=round(baseline_rate, 4),
            skill_pass_rate=round(skill_rate, 4),
            delta=round(delta, 4),
            eval_score=round(eval_score, 2),
            metadata={"n_trials": n_trials, "n_tasks": n_tasks},
        )

    def _load_or_run_baseline(
        self, task: TaskSpec, llm_config: dict, n_trials: int,
        cache_dir: Optional[Path], _log,
    ) -> list[TrialResult]:
        """加载缓存的 baseline 结果，没有则现跑"""
        # 尝试读缓存
        if cache_dir:
            cache_file = cache_dir / f"{task.task_id}.json"
            if cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    if len(cached) >= n_trials:
                        _log(f"    baseline: 从缓存加载 ({len(cached)} trials)", "eval")
                        return [TrialResult(**r) for r in cached[:n_trials]]
                except Exception:
                    pass

        # 现跑
        trials = []
        for i in range(n_trials):
            _log(f"    baseline run {i+1}/{n_trials}", "eval")
            try:
                result = self.run_trial(task, skill_md=None, llm_config=llm_config)
            except Exception as e:
                logger.error(f"baseline trial failed: {e}")
                result = TrialResult(task_id=task.task_id, passed=False, error=str(e))
            trials.append(result)

        # 写缓存
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{task.task_id}.json"
            serializable = []
            for t in trials:
                serializable.append({
                    "task_id": t.task_id, "passed": t.passed,
                    "regression_ok": t.regression_ok,
                    "duration_seconds": t.duration_seconds,
                    "error": t.error, "agent_patch": t.agent_patch,
                })
            cache_file.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))

        return trials
