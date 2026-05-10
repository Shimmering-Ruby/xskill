"""
entities/skill.py — Skill 实体类（含内部 CandidateBuffer + CanaryGitOps）
═══════════════════════════════════════════════════════════════════════════
单个 skill 的视图。包 SKILL.md frontmatter + .candidates.yml + 子仓 git。
"""

from __future__ import annotations

from datetime import datetime, date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from xskill import canary as _canary
from xskill import candidates as _candidates
from xskill.frontmatter import parse as _fm_parse
from xskill.types import Candidate

if TYPE_CHECKING:
    from xskill.entities.registry import Registry
    from xskill.entities.trajectory import Trajectory


# ═════════════════════════════════════════════════════════════════
# CandidateBuffer (internal — 不暴露)
# ═════════════════════════════════════════════════════════════════
class CandidateBuffer:
    """每个 Skill 的 .candidates.yml 视图。只读视图 + 元信息。
    add/promote/archive 等"重操作"由 watcher / Pipeline 直接调底层模块函数，
    不通过本类。"""

    def __init__(self, skill_path: Path):
        self.skill_path = skill_path

    def view(self) -> list[Candidate]:
        data = _candidates.load_candidates(self.skill_path)
        out: list[Candidate] = []
        for c in data.get("candidates", []) or []:
            out.append(Candidate(
                pattern=c.get("pattern", ""),
                kind=c.get("type", "step"),
                attach_to=c.get("attach_to"),
                supporting_trajs=c.get("supporting_trajs", []) or [],
                first_seen=_parse_iso_date(c.get("first_seen")),
                promoted=bool(c.get("promoted", False)),
            ))
        return out


def _parse_iso_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).date()
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
# CanaryGitOps (internal — 不暴露)
# ═════════════════════════════════════════════════════════════════
class CanaryGitOps:
    """单个 skill 的 git 子仓 + .ux_scores.jsonl 操作。

    只暴露给 Skill.canary 内部使用。CLI / SDK 用户通过 skill.canary_status() /
    skill.recent_ux_scores() 间接观察。
    """

    def __init__(self, skill_path: Path):
        self.skill_path = skill_path

    def has_staging(self) -> bool:
        return _canary.has_staging(self.skill_path)

    def main_sha(self) -> Optional[str]:
        return _canary.main_sha(self.skill_path)

    def staging_sha(self) -> Optional[str]:
        return _canary.staging_sha(self.skill_path)

    def staging_created_at(self) -> Optional[datetime]:
        return _canary.staging_created_at(self.skill_path)

    def ux_scores(self, side: Optional[str] = None,
                  days: int = 30) -> list[dict]:
        scores = _canary.load_ux_scores(self.skill_path)
        if side is not None:
            scores = [s for s in scores if s.get("side") == side]
        if days > 0:
            cutoff = datetime.utcnow().timestamp() - days * 86400
            kept = []
            for s in scores:
                ts = s.get("scored_at", "")
                try:
                    if datetime.fromisoformat(ts.rstrip("Z")).timestamp() >= cutoff:
                        kept.append(s)
                except Exception:
                    kept.append(s)
            scores = kept
        return scores


# ═════════════════════════════════════════════════════════════════
# Skill (public)
# ═════════════════════════════════════════════════════════════════
class Skill:
    """单个 skill 的视图。

    - read() / frontmatter / use_count — 来自 SKILL.md
    - candidates                       — 来自 .candidates.yml
    - canary_status() / recent_ux_scores() — 来自子仓 git + .ux_scores.jsonl
    - supporting_trajectories()        — 来自 frontmatter.metadata.source_trajs
    """

    def __init__(self, path: Path, registry: Optional["Registry"] = None):
        self.path = Path(path)
        self.name = self.path.name
        self._registry = registry
        self._fm_cache: Optional[dict] = None
        self._body_cache: Optional[str] = None
        self.candidates_buffer = CandidateBuffer(self.path)
        self.canary_ops = CanaryGitOps(self.path)

    # ─── SKILL.md 访问 ───────────────────────────────────────────
    @property
    def _skill_md_path(self) -> Path:
        return self.path / "SKILL.md"

    def read(self) -> str:
        return self._skill_md_path.read_text(encoding="utf-8")

    def _parse(self) -> tuple[dict, str]:
        if self._fm_cache is None:
            text = self.read()
            self._fm_cache, self._body_cache = _fm_parse(text)
        return self._fm_cache, self._body_cache or ""

    @property
    def frontmatter(self) -> dict:
        fm, _ = self._parse()
        return fm

    @property
    def description(self) -> str:
        return self.frontmatter.get("description", "")

    @property
    def use_count(self) -> int:
        meta = self.frontmatter.get("metadata", {}) or {}
        return int(meta.get("use_count", 0))

    @property
    def source_trajs(self) -> list[str]:
        meta = self.frontmatter.get("metadata", {}) or {}
        return list(meta.get("source_trajs", []) or [])

    # ─── candidates 视图 ─────────────────────────────────────────
    @property
    def candidates(self) -> list[Candidate]:
        return self.candidates_buffer.view()

    # ─── 灰度状态 + UX 分 ────────────────────────────────────────
    def canary_status(self) -> Literal["main_only", "staging_active", "expired"]:
        if not self.canary_ops.has_staging():
            return "main_only"
        # 简化：看 staging_created_at 是否过期（>14 天）
        created = self.canary_ops.staging_created_at()
        if created is None:
            return "staging_active"
        age = (datetime.utcnow() - created.replace(tzinfo=None)).days
        return "expired" if age > 14 else "staging_active"

    def recent_ux_scores(self, side: Optional[str] = None,
                         days: int = 30) -> list[dict]:
        return self.canary_ops.ux_scores(side=side, days=days)

    def ux_avg(self, side: Optional[str] = None, days: int = 30) -> Optional[float]:
        scores = [s.get("score") for s in self.recent_ux_scores(side, days)
                  if isinstance(s.get("score"), (int, float))]
        if not scores:
            return None
        return sum(scores) / len(scores)

    # ─── 反向关联 ────────────────────────────────────────────────
    def supporting_trajectories(self) -> list["Trajectory"]:
        """frontmatter.metadata.source_trajs 中的 traj id 解析为 Trajectory 实体。
        需要 registry 注入才能反查具体路径；否则返回空列表。"""
        if self._registry is None:
            return []
        from xskill.entities.trajectory import Trajectory as _Traj
        out: list[_Traj] = []
        for traj_id in self.source_trajs:
            # 在 registry 中按 filename 反查（traj_id 形如 "traj_0042"）
            paths = self._registry.trajectories_using(self.name)  # 备选反查
            # 直接按 filename 找
            from xskill import registry as _r
            conn = _r.get_connection(self._registry._db_path)
            try:
                rows = conn.execute(
                    "SELECT w.path, t.filename FROM trajectories t "
                    "JOIN watch_dirs w ON t.watch_dir_id=w.id "
                    "WHERE t.filename = ? OR t.filename = ?",
                    (f"{traj_id}.md", traj_id),
                ).fetchall()
                for r in rows:
                    out.append(_Traj(path=Path(r["path"]) / r["filename"],
                                     registry=self._registry))
            finally:
                conn.close()
        return out

    def __repr__(self) -> str:
        return f"Skill({self.name})"
