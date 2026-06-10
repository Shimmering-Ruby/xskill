#!/usr/bin/env python3.11
"""Tiny markdown browser. Lists directories and renders .md files.

Stdlib only. Markdown is rendered client-side via marked.js from a CDN so the
server doesn't need any Python markdown library installed.
"""
from __future__ import annotations

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(os.environ.get("MDSERVE_ROOT", os.getcwd())).resolve()
HOST = os.environ.get("MDSERVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("MDSERVE_PORT", "8002"))
# Path prefix the server is mounted under by an upstream reverse proxy. All
# generated links get this prepended; incoming requests get it stripped.
# Example: MDSERVE_BASE_PATH=/report -> server lives at https://host/report/...
BASE = "/" + os.environ.get("MDSERVE_BASE_PATH", "").strip("/")
BASE = "" if BASE == "/" else BASE  # empty means mounted at root

# marked.js for client-side markdown rendering. Pinned for reproducibility.
MARKED_CDN = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
# mermaid.js renders ```mermaid fenced blocks (class diagrams, flowcharts) into
# SVG client-side. Pinned. Only activates on pages that actually use it.
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

# Directories we skip when listing (noisy / huge / not interesting to browse).
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}


def url(path: str) -> str:
    """Prepend the mount prefix to a server-absolute URL (path starts with '/')."""
    if not path.startswith("/"):
        path = "/" + path
    return (BASE + path) if BASE else path


def safe_resolve(rel: str) -> Path | None:
    """Resolve a request path against ROOT; return None if it escapes."""
    rel = unquote(rel).lstrip("/")
    target = (ROOT / rel).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return None
    return target


def breadcrumb_html(rel: Path) -> str:
    parts = [(url("/"), "root")]
    acc = ""
    for seg in rel.parts:
        acc = f"{acc}/{seg}" if acc else seg
        parts.append((url("/" + quote(acc)), seg))
    return " / ".join(f'<a href="{href}">{html.escape(label)}</a>' for href, label in parts)


def render_dir(target: Path, rel: Path) -> bytes:
    entries = []
    try:
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith(".") and p.name not in {".claude"}:
                continue
            if p.is_dir() and p.name in SKIP_DIRS:
                continue
            if p.is_dir():
                entries.append((p.name + "/", True))
            elif p.suffix.lower() in {".md", ".markdown"}:
                entries.append((p.name, False))
    except PermissionError:
        entries = []

    rows = []
    for name, is_dir in entries:
        bare = name.rstrip("/")
        sub = str(rel / bare) if rel != Path() else bare
        href = url("/" + quote(sub))
        icon = "📁" if is_dir else "📄"
        rows.append(f'<li>{icon} <a href="{href}">{html.escape(name)}</a></li>')

    listing = "\n".join(rows) or "<li><em>(no markdown files or subdirectories)</em></li>"
    body = f"""
    <h2>{breadcrumb_html(rel)}</h2>
    <p style="color:#888">{html.escape(str(target))}</p>
    <ul class="listing">{listing}</ul>
    """
    return page("mdserve — " + (str(rel) or "root"), body).encode("utf-8")


def render_md(target: Path, rel: Path) -> bytes:
    raw_url = url("/__raw/" + quote(str(rel)))
    parent_url = url("/" + quote(str(rel.parent))) if rel.parent != Path() else url("/")
    body = f"""
    <h2>{breadcrumb_html(rel.parent)} / <strong>{html.escape(rel.name)}</strong>
        <a class="back" href="{parent_url}">↩ up</a></h2>
    <article id="content">loading…</article>
    <script src="{MARKED_CDN}"></script>
    <script src="{MERMAID_CDN}"></script>
    <script>
      mermaid.initialize({{ startOnLoad: false, theme: 'neutral' }});
      fetch({raw_url!r}).then(r => r.text()).then(md => {{
        const el = document.getElementById('content');
        el.innerHTML = marked.parse(md, {{ gfm: true, breaks: false }});
        // marked emits ```mermaid as <pre><code class="language-mermaid">.
        // Rewrite those to <pre class="mermaid"> so mermaid.run() picks them up.
        el.querySelectorAll('code.language-mermaid').forEach(code => {{
          const pre = code.parentElement;
          const holder = document.createElement('pre');
          holder.className = 'mermaid';
          holder.textContent = code.textContent;
          pre.replaceWith(holder);
        }});
        mermaid.run({{ querySelector: 'pre.mermaid' }});
      }}).catch(e => {{
        document.getElementById('content').textContent = 'load error: ' + e;
      }});
    </script>
    """
    return page("mdserve — " + str(rel), body).encode("utf-8")


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         max-width: 920px; margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.55; }}
  h2 a {{ color: #0366d6; text-decoration: none; }}
  h2 a:hover {{ text-decoration: underline; }}
  ul.listing {{ list-style: none; padding-left: 0; }}
  ul.listing li {{ padding: 2px 0; }}
  a.back {{ font-size: 0.8em; margin-left: 0.5em; }}
  article {{ border-top: 1px solid #eee; padding-top: 1rem; }}
  article pre {{ background: #f6f8fa; padding: 12px; overflow: auto; border-radius: 6px; }}
  article code {{ background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }}
  article pre code {{ background: transparent; padding: 0; }}
  article table {{ border-collapse: collapse; }}
  article th, article td {{ border: 1px solid #d0d7de; padding: 4px 8px; }}
  article blockquote {{ border-left: 4px solid #d0d7de; color: #57606a; padding-left: 1em; margin-left: 0; }}
</style></head><body>{body}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if BASE:
            if path == BASE:
                # bare prefix without trailing slash -> redirect to canonical form
                self.send_response(301)
                self.send_header("Location", BASE + "/")
                self.end_headers()
                return
            if path.startswith(BASE + "/"):
                path = path[len(BASE):]
            else:
                return self.send_error(404)

        if path.startswith("/__raw/"):
            target = safe_resolve(path[len("/__raw/"):])
            if not target or not target.is_file():
                return self.send_error(404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/" or path == "":
            target, rel = ROOT, Path()
        else:
            target = safe_resolve(path)
            if not target or not target.exists():
                return self.send_error(404)
            rel = target.relative_to(ROOT)

        if target.is_dir():
            body = render_dir(target, rel)
        elif target.suffix.lower() in {".md", ".markdown"}:
            body = render_md(target, rel)
        elif target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}:
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
                    ".ico": "image/x-icon"}[target.suffix.lower()]
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return
        else:
            return self.send_error(415, "only directories and .md files are rendered")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"mdserve: serving {ROOT} on http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
