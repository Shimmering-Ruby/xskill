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

    def canary_sides(self) -> list[dict]:
        """灰度分桶分布:轨迹按 canary_side(staging/main) 计数 + 平均 ux(纯 registry)。"""
        conn = get_connection(self._db)
        try:
            rows = conn.execute(
                "SELECT COALESCE(canary_side,'main') side, COUNT(*) trajs,"
                " AVG(ux_score) avg_ux FROM trajectories"
                " GROUP BY COALESCE(canary_side,'main') ORDER BY trajs DESC"
            ).fetchall()
        finally:
            conn.close()
        return [{"side": r["side"], "trajs": r["trajs"],
                 "avg_ux": round(r["avg_ux"], 2) if r["avg_ux"] is not None else 0.0}
                for r in rows]

    def adoption_rate(self) -> dict:
        """原子采纳率 = 采纳原子(atom_adoption 去重) / 总原子(tasks_extracted 求和)。"""
        conn = get_connection(self._db)
        try:
            adopted = conn.execute(
                "SELECT COUNT(DISTINCT atom_id) FROM atom_adoption").fetchone()[0]
            total = conn.execute(
                "SELECT COALESCE(SUM(tasks_extracted),0) FROM trajectories").fetchone()[0]
        finally:
            conn.close()
        return {"adopted": adopted, "total": total, "rate": _pct(adopted, total)}

    def promotion_rate(self) -> dict:
        """canary 晋升率 = 晋升数 / 已裁决数(晋升+拒绝+超时丢弃)。"""
        conn = get_connection(self._db)
        try:
            rows = dict(conn.execute(
                "SELECT action, COUNT(*) n FROM canary_decision GROUP BY action").fetchall())
        finally:
            conn.close()
        promoted = rows.get("promoted", 0)
        decided = promoted + rows.get("rejected", 0) + rows.get("timeout_discarded", 0)
        return {"promoted": promoted, "decided": decided, "rate": _pct(promoted, decided)}

    def trigger_rate(self) -> dict:
        """推荐触发率 = 被推荐的 skill 里被采用的占比;另给单 skill 明细。

        近似:单 skill 触发率 = 该 skill 被采用次数 / 被推荐次数(封顶 100%);
        总触发率 = 被采用过的被推荐 skill 数 / 被推荐 skill 总数。
        """
        conn = get_connection(self._db)
        try:
            # 分母去重:同一 (用户, skill) 只算一次,防反复同步把分母滚大、触发率假性变小
            recs = dict(conn.execute(
                "SELECT skill, COUNT(DISTINCT client_id) n FROM recommendation_log"
                " GROUP BY skill").fetchall())
            used = dict(conn.execute(
                "SELECT skill_used, COUNT(*) n FROM trajectories"
                " WHERE skill_used IS NOT NULL AND skill_used!='' GROUP BY skill_used").fetchall())
        finally:
            conn.close()
        by_skill = []
        adopted_skills = 0
        for skill, rec in sorted(recs.items(), key=lambda kv: -kv[1]):
            u = used.get(skill, 0)
            if u > 0:
                adopted_skills += 1
            by_skill.append({"skill": skill, "recommended": rec, "used": u,
                             "rate": round(min(u / rec * 100, 100), 1) if rec else 0.0})
        return {"overall": _pct(adopted_skills, len(recs)), "by_skill": by_skill}

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
