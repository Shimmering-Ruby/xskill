"""AtomCanary —— 灰度分数落盘以 atom_id 为主键
================================================

底层复用 ``xskill.canary`` 的 git 分支管理 + 判定逻辑（grain-agnostic）；
本模块只换 ``.ux_scores.jsonl`` 文件的主键字段：从 ``traj_id`` 改成 ``atom_id``。

为什么换主键
============
旧 traj-level 打分一条 traj 一条分；同一条 traj 内多个 atom 的体验差异被均化。
atom-level 后每条 atom 独立打分，能更准确反映"用户在哪个意图段对 skill 的体验"。
``(atom_id, skill_name, side)`` 三元组保证幂等（同一 atom 在同侧 skill 上只
打分一次）。

判定 / 翻牌仍走 ``canary.check_and_decide``：它只依赖 ``side`` + ``commit_sha``
+ ``score`` + ``scored_at``，不关心主键字段叫什么。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from xskill import canary


@dataclass
class AtomCanary:
    skill_dir: Path

    def append(self, *, atom_id: str, skill_name: str, side: str,
               commit_sha: str, score: float, reasons: str) -> bool:
        """幂等追加一条 atom 体验分。

        同一 (atom_id, skill_name, side) 三元组已存在则返回 False，不重复写入。
        """
        existing = canary.load_ux_scores(self.skill_dir)
        for e in existing:
            if (e.get("atom_id") == atom_id
                    and e.get("skill_name") == skill_name
                    and e.get("side") == side):
                return False
        record = {
            "atom_id": atom_id,
            "skill_name": skill_name,
            "side": side,
            "commit_sha": commit_sha,
            "score": float(score),
            "reasons": reasons,
            "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        p = self.skill_dir / canary.UX_SCORES_FILENAME
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    def recent(self, *, side: str, commit_sha: str, n: int) -> list[dict]:
        """与 ``canary.recent_scores`` 同语义，但读 atom_id 字段。"""
        all_ = canary.load_ux_scores(self.skill_dir)
        filtered = [
            s for s in all_
            if s.get("side") == side and s.get("commit_sha") == commit_sha
        ]
        filtered.sort(key=lambda s: s.get("scored_at", ""), reverse=True)
        return filtered[:n]

    def check_and_decide(self, *, config: canary.CanaryConfig | None = None) -> dict:
        """代理 ``canary.check_and_decide``——判定逻辑不区分 atom/traj 粒度。"""
        return canary.check_and_decide(self.skill_dir, config=config)
