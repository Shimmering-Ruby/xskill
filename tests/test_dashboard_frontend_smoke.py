"""test_dashboard_frontend_smoke.py —— 前端壳与取数脚本静态冒烟"""
from __future__ import annotations

from pathlib import Path

STATIC = Path("src/xskill/dashboard/static")


def test_index_references_appjs_and_sections():
    """壳页面引用取数脚本，且五个分区容器齐全。"""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "app.js" in html
    assert 'id="pg-overview"' in html   # 分区容器存在
    for pg in ("pg-skills", "pg-traj", "pg-users", "pg-canary"):
        assert f'id="{pg}"' in html


def test_index_is_fully_vendored():
    """零外联：内网 headless 环境不允许任何会发起网络请求的外部引用。

    Tailwind 用构建期编译产物内联进 <style id="twcss">（后端只服务 / 与
    /app.js 两个路径，单独的 css 文件会 404，见 static/BUILD.md）。
    minified CSS 里的 MIT license 注释含 URL，不算外部引用。
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for pat in ('src="http', "src='http", 'href="http', "href='http",
                "url(http", "@import"):
        assert pat not in html, f"external reference found: {pat}"
    assert "cdn.jsdelivr" not in html          # 旧 Tabler CDN 已移除
    assert 'id="twcss"' in html                # Tailwind 编译产物内联锚点
    assert "tailwindcss" in html               # 内联的确是编译产物


def test_appjs_fetches_overview_endpoint():
    """取数脚本以相对路径 fetch 核心端点。"""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # 前端 fetch 用相对路径(去前导 /)以支持经 nginx 子路径反代；断言不带前导
    # 斜杠，对相对/绝对两种写法都成立。
    assert "api/v1/dashboard/overview" in js
    assert "api/v1/dashboard/by-domain" in js
