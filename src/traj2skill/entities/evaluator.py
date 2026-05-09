"""
entities/evaluator.py — Evaluator 类
═══════════════════════════════════════════
封装 L1（LLM 7 维打分）+ L2（SWE-bench 沙箱）评估。
should_merge 砍掉 is_new=False 死分支，单签名绝对阈值。
"""

from __future__ import annotations

from typing import Optional

from traj2skill import skill_eval as _se
from traj2skill.entities.skill import Skill
from traj2skill.llm_client import LLMClient
from traj2skill.types import EvalScore


class Evaluator:
    """L1 + L2 评估器。

    用法：
        from traj2skill import T2S, Evaluator
        t2s = T2S()
        ev = Evaluator(t2s.llm, t2s.config)
        score = ev.evaluate(t2s.skill_repo['fix-foo'])
        if ev.should_merge(score):
            ...
    """

    def __init__(self, llm: LLMClient, config: dict):
        self.llm = llm
        self.config = config

    def evaluate(self,
                 skill: Skill,
                 *,
                 n_runs: int = 3,
                 force_sandbox: bool = False,
                 force_no_sandbox: bool = False) -> EvalScore:
        """跑 L1 / L2 自动选择，返回归一化的 EvalScore。"""
        result = _se.run_eval(
            skill_dir=skill.path,
            llm_client=self.llm,
            n_runs=n_runs,
            log_fn=None,
            config=self.config,
            force_sandbox=force_sandbox,
            force_no_sandbox=force_no_sandbox,
        )
        return self._dict_to_eval_score(result)

    @staticmethod
    def should_merge(score: EvalScore, threshold: float = 6.0) -> bool:
        """门控：eval_score ≥ threshold 即 merge。

        注：现状 process.process_traj 永远以 is_new=True 调，无 prev 比较的真实路径，
        故砍掉 old_eval 参数。修改 skill 用同一阈值。"""
        return score.eval_score >= threshold

    @staticmethod
    def _dict_to_eval_score(d: dict) -> EvalScore:
        tier = d.get("tier", "llm")
        eval_score = float(d.get("eval_score", 0.0) or 0.0)
        if tier == "llm":
            return EvalScore(
                tier="llm",
                eval_score=eval_score,
                scores=d.get("scores"),
                runs=d.get("runs"),
            )
        return EvalScore(
            tier="sandbox",
            eval_score=eval_score,
            baseline_pass_rate=d.get("baseline_pass_rate"),
            skill_pass_rate=d.get("skill_pass_rate"),
            delta=d.get("delta"),
            instances=d.get("instances"),
        )
