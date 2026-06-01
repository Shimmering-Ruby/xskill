"""看板路由:静态壳 GET / + 聚合端点 /api/v1/dashboard/*。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from xskill.dashboard.metrics import DashboardMetrics

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
        return metrics.overview()

    @router.get("/api/v1/dashboard/by-domain")
    def by_domain() -> dict:
        return {"by_ecosystem": metrics.by_ecosystem(), "by_model": metrics.by_model()}

    return router
