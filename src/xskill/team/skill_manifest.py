"""skill_manifest.py — 给一个 client 现算它该持有的 ≤100 个 skill slot（SP1）

server 端**不存"账本表"**。manifest = ``pick_side`` 纯函数 + skill git
状态（has_staging / main_sha / staging_sha）的实时投影，每次 sync 现算。

slot 结构 = 80 ranked + 20 recommended：
- ranked      —— 按 ux_score（main 侧近 30 天均分）滑窗取高分。
- recommended —— SP3 = 用户画像质心推荐位。SP1 占位：按 ux 继续往下取 20 个。
                 slot 结构本身（bucket 字段）SP1 就落地，SP3 只换 recommended 的选法。

灰度归因：某 skill 有 staging 分支 → side = pick_side(client_id, name, p)，
确定性伪随机，同 client 同 skill 在整轮灰度内 side 钉死。无 staging → main。
"""
from __future__ import annotations

import time
from pathlib import Path

from xskill.canary import has_staging, main_sha, pick_side, staging_sha
from xskill.entities.skill import Skill
from xskill.entities.skill_repo import SkillRepo
from xskill.team.sync_protocol import SkillSlot, SyncResponse


def _rank_key(skill: Skill) -> tuple[float, int]:
    """排序键：(main 侧近 30 天 ux 均分, use_count)，都缺则 (0.0, 0)。"""
    avg = skill.ux_avg(side="main", days=30)
    return (avg if avg is not None else 0.0, skill.use_count)


def _resolve_slot(skill: Skill, client_id: str, probability: float, bucket: str) -> SkillSlot:
    """对一个 skill 现算它对该 client 的 side + sha。"""
    if has_staging(skill.path):
        side = pick_side(client_id, skill.name, probability)
        sha = staging_sha(skill.path) if side == "staging" else main_sha(skill.path)
    else:
        side = "main"
        sha = main_sha(skill.path)
    if not sha:
        raise RuntimeError(f"skill {skill.name!r}: cannot resolve sha for side={side}")
    return SkillSlot(skill_name=skill.name, side=side, sha=sha, bucket=bucket)


def build_manifest(
    *,
    client_id: str,
    skill_dir: Path | str,
    probability: float,
    ranked_slots: int = 80,
    total_slots: int = 100,
) -> SyncResponse:
    """为 ``client_id`` 现算 manifest。skill 总数不足 total_slots 时全发。"""
    repo = SkillRepo(Path(skill_dir))
    skills = sorted(repo, key=_rank_key, reverse=True)
    chosen = skills[:total_slots]
    slots: list[SkillSlot] = []
    for idx, skill in enumerate(chosen):
        bucket = "ranked" if idx < ranked_slots else "recommended"
        slots.append(_resolve_slot(skill, client_id, probability, bucket))
    return SyncResponse(slots=slots, server_time=time.time())
