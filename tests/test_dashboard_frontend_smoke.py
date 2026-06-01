"""test_dashboard_frontend_smoke.py —— 前端壳与取数脚本静态冒烟"""
from __future__ import annotations

from pathlib import Path

STATIC = Path("src/xskill/dashboard/static")


def test_index_references_appjs_and_tabler():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "app.js" in html
    assert "tabler" in html.lower()
    assert 'id="pg-overview"' in html   # 分区容器存在


def test_appjs_fetches_overview_endpoint():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/v1/dashboard/overview" in js
    assert "/api/v1/dashboard/by-domain" in js
