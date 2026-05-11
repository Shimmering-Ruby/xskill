#!/usr/bin/env python3
"""
cli.py — xskill 紧凑 CLI
═══════════════════════════════════════════════════════
仅 5 个子命令（无 --no-watch / --no-ui / --skill-dir / --llm-* 这类散 flag）：
    xskill serve [--host] [--port]
    xskill registry add|remove|list <path>
    xskill search traj|skill <query> [--top-k]

所有筛选/格式化交给 shell（grep/awk）。状态/配置全在 ~/.xskill/。
"""

from __future__ import annotations

import argparse
import logging
import sys

from xskill.config import set_overrides


# ═══════════════════════════════════════════════════════════════
# 子命令
# ═══════════════════════════════════════════════════════════════

def cmd_serve(args, xskill) -> int:
    xskill.serve(host=args.host, port=args.port)
    return 0


def cmd_registry(args, xskill) -> int:
    action = args.registry_action
    if action == "add":
        wd = xskill.registry.add(args.path, label=args.label or "")
        print(f"Registered: {wd.path}  id={wd.id}  label={wd.label!r}")
        return 0
    if action == "remove":
        ok = xskill.registry.remove(args.path)
        print("Removed." if ok else "Not found.")
        return 0 if ok else 1
    if action == "list":
        dirs = xskill.registry.list()
        if not dirs:
            print("(no registered directories)")
            return 0
        # 列序: id  ecosystem  traj  indexed  label  path
        # ecosystem 是来源标签：``manual`` = 用户手动注册；其他如
        # ``claude_code`` = daemon 启动时自动 detect 出来的生态目录。
        # 同时用 codex / opencode 等其他工具时一眼能区分来源。
        for w in dirs:
            print(
                f"{w.id}\t{w.ecosystem}\t{w.traj_count}\t{w.indexed_count}\t"
                f"{w.label or '-'}\t{w.path}"
            )
        return 0
    return 1


def cmd_search(args, xskill) -> int:
    target = args.search_target
    if target == "traj":
        hits = xskill.search_trajectories(args.query, top_k=args.top_k)
        for h in hits:
            traj = h.trajectory
            status = traj.status or "-"
            skill_used = traj.skill_used or "-"
            side = traj.canary_side or "-"
            print(f"{h.similarity:.3f}\t{status}\t{skill_used}\t{side}\t{traj.path}")
        return 0
    if target == "skill":
        hits = xskill.search_skills(args.query, top_k=args.top_k)
        for h in hits:
            s = h.skill
            avg = s.ux_avg(side="main", days=30)
            n = len([x for x in s.recent_ux_scores(side="main", days=30)
                     if x.get("score") is not None])
            ux_col = f"{avg:.1f}({n})" if avg is not None else "-"
            canary = s.canary_status()
            canary_col = "staging" if canary == "staging_active" else "-"
            print(f"{h.similarity:.3f}\t{s.name}\t{s.use_count}\t{ux_col}\t{canary_col}")
        return 0
    return 1


# ═══════════════════════════════════════════════════════════════
# argparse
# ═══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xskill",
        description="xskill — distill reusable Skills from AI Agent trajectories",
    )
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--quiet", action="store_true", help="quiet mode")
    sub = p.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="Start daemon (FastAPI + watcher)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)

    p_reg = sub.add_parser("registry", help="Manage watched directories")
    p_reg.add_argument("registry_action", choices=["add", "remove", "list"])
    p_reg.add_argument("path", nargs="?", type=str,
                       help="directory path (for add/remove)")
    p_reg.add_argument("--label", type=str, default="",
                       help="human-friendly label (for add)")

    p_search = sub.add_parser(
        "search", help="Search trajectories or skills (cross-registry)"
    )
    p_search.add_argument("search_target", choices=["traj", "skill"])
    p_search.add_argument("query", type=str)
    p_search.add_argument("--top-k", "-k", type=int, default=5)

    return p


def _setup_logging(debug: bool, quiet: bool) -> None:
    if debug:
        level, fmt = logging.DEBUG, "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    elif quiet:
        level, fmt = logging.WARNING, "%(message)s"
    else:
        level, fmt = logging.INFO, "%(asctime)s [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "openai", "xskill.llm_client", "agno"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    set_overrides(debug=args.debug, quiet=args.quiet)
    _setup_logging(args.debug, args.quiet)

    if args.command == "registry" and args.registry_action in ("add", "remove"):
        if not args.path:
            parser.error(f"path is required for 'registry {args.registry_action}'")

    from xskill import XSkill
    xskill = XSkill()

    handler = {
        "serve":    cmd_serve,
        "registry": cmd_registry,
        "search":   cmd_search,
    }.get(args.command)
    return handler(args, xskill) if handler else (parser.print_help() or 1)


if __name__ == "__main__":
    sys.exit(main() or 0)
