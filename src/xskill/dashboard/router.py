"""看板路由:静态壳 GET / + 自包含只读聚合端点 /api/v1/dashboard/*。

所有数据端点只读 registry(纯 SQL 聚合),不依赖主 app 的端点、不碰 git/LLM,
所以看板既能挂进 serve,也能作为独立只读实例跑。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from xskill.dashboard.metrics import DashboardMetrics
from xskill.pipeline.registry import usage_summary, model_share, list_watch_dirs

_STATIC = Path(__file__).with_name("static")


def build_dashboard_router(db_path: Optional[Path] = None) -> APIRouter:
    router = APIRouter()
    metrics = DashboardMetrics(db_path=db_path)

    @router.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @router.get("/app.js")
    def appjs() -> Response:
        return Response((_STATIC / "app.js").read_text(encoding="utf-8"),
                        media_type="application/javascript")

    @router.get("/api/v1/dashboard/overview")
    def overview() -> dict:
        return {**metrics.overview(), "price_health": _price_health()}

    @router.get("/api/v1/dashboard/by-domain")
    def by_domain() -> dict:
        return {"by_ecosystem": metrics.by_ecosystem(), "by_model": metrics.by_model()}

    @router.get("/api/v1/dashboard/rates")
    def rates() -> dict:
        """三个需埋点的衍生率:推荐触发率 / 原子采纳率 / canary 晋升率。"""
        return {"trigger": metrics.trigger_rate(),
                "adoption": metrics.adoption_rate(),
                "promotion": metrics.promotion_rate()}

    @router.get("/api/v1/dashboard/cost")
    def cost() -> dict:
        return usage_summary(db_path)

    @router.get("/api/v1/dashboard/models")
    def models() -> dict:
        return {"models": model_share(db_path)}

    @router.get("/api/v1/dashboard/dirs")
    def dirs() -> dict:
        rows = list_watch_dirs(db_path=db_path)
        return {"dirs": [{"ecosystem": r.get("ecosystem"), "path": r.get("path"),
                          "label": r.get("label"), "traj_count": r.get("traj_count"),
                          "indexed_count": r.get("indexed_count")} for r in rows]}

    @router.get("/api/v1/dashboard/canary")
    def canary() -> dict:
        return {"sides": metrics.canary_sides()}

    return router


def _price_health() -> Optional[dict]:
    try:
        from xskill import prices
        return prices.refresh_health()
    except Exception:  # pylint: disable=broad-exception-caught
        return None
