"""DashboardMetrics — 衍生质量指标(纯读 registry,无 FastAPI 依赖,可单测)。

只算"现在数据就能算"的指标;需埋点的(推荐触发率/原子采纳率精确值/canary 晋升率)
不在此层,见 docs/superpowers/specs/2026-06-01-dashboard-design.md §5 backlog。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from xskill.pipeline.registry import get_connection


def _pct(num: float, den: float) -> float:
    return round(num / den * 100, 1) if den else 0.0


class DashboardMetrics:
    def __init__(self, db_path: Optional[Path] = None):
        self._db = db_path

    def overview(self) -> dict:
        conn = get_connection(self._db)
        try:
            r = conn.execute(
                "SELECT COUNT(*) trajs, COALESCE(SUM(tasks_extracted),0) atoms,"
                " SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done,"
                " SUM(CASE WHEN skill_generated IS NOT NULL AND skill_generated!='' THEN 1 ELSE 0 END) skilled,"
                " SUM(CASE WHEN retry_count>0 THEN 1 ELSE 0 END) retried,"
                " AVG(ux_score) avg_ux FROM trajectories"
            ).fetchone()
        finally:
            conn.close()
        n = r["trajs"] or 0
        return {
            "trajs": n,
            "atoms": r["atoms"] or 0,
            "avg_atoms_per_traj": round((r["atoms"] or 0) / n, 2) if n else 0.0,
            "success_rate": _pct(r["done"] or 0, n),
            "skill_yield": _pct(r["skilled"] or 0, n),
            "retry_rate": _pct(r["retried"] or 0, n),
            "avg_ux": round(r["avg_ux"], 2) if r["avg_ux"] is not None else 0.0,
        }

    def by_ecosystem(self) -> list[dict]:
        conn = get_connection(self._db)
        try:
            rows = conn.execute(
                "SELECT wd.ecosystem ecosystem, COUNT(t.id) trajs,"
                " COALESCE(SUM(t.tasks_extracted),0) atoms,"
                " SUM(CASE WHEN t.skill_generated IS NOT NULL AND t.skill_generated!='' THEN 1 ELSE 0 END) skills,"
                " AVG(t.ux_score) avg_ux"
                " FROM watch_dirs wd LEFT JOIN trajectories t ON t.watch_dir_id=wd.id"
                " GROUP BY wd.ecosystem ORDER BY trajs DESC"
            ).fetchall()
        finally:
            conn.close()
        return [self._row(r, "ecosystem") for r in rows]

    def by_model(self) -> list[dict]:
        conn = get_connection(self._db)
        try:
            rows = conn.execute(
                "SELECT COALESCE(source_model,'unknown') model, COUNT(*) trajs,"
                " COALESCE(SUM(tasks_extracted),0) atoms,"
                " SUM(CASE WHEN skill_generated IS NOT NULL AND skill_generated!='' THEN 1 ELSE 0 END) skills,"
                " AVG(ux_score) avg_ux FROM trajectories"
                " GROUP BY COALESCE(source_model,'unknown') ORDER BY trajs DESC"
            ).fetchall()
        finally:
            conn.close()
        return [self._row(r, "model") for r in rows]

    @staticmethod
    def _row(r, key: str) -> dict:
        t = r["trajs"] or 0
        return {
            key: r[key],
            "trajs": t,
            "atoms": r["atoms"] or 0,
            "avg_atoms": round((r["atoms"] or 0) / t, 2) if t else 0.0,
            "skills": r["skills"] or 0,
            "avg_ux": round(r["avg_ux"], 2) if r["avg_ux"] is not None else 0.0,
        }
