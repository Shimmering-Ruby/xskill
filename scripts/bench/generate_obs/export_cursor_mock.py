#!/usr/bin/env python3
"""把本机 Cursor 的历史会话全量导出成一个 mock xskill home。

原汁原味：用的是 xskill 自己的 Cursor 桥接器
（``xskill.ecosystems.cursor``），产出就是产品里那套 ``traj_cursor_*.md``
加同名 ``.json``，落在真实 server 收上传的那个位置::

    <mock home>/.xskill/team_trajectories/clients/<client>/sessions/

不另造"会话卡片"之类的中间概念，不裁剪、不摘要、不改格式。generate 在
容器里看到的目录结构和现网 server 上一模一样，只是数据来自本机 Cursor。

怎么跑（``HOME`` 必须先指到 mock home——xskill 的 ``XSKILL_HOME`` 是
import 时按 ``Path.home()`` 定的）::

    HOME=/tmp/xskill-generate-obs/mock \\
      python export_cursor_mock.py --source-home /home/admin

已经导过就跳过：``scan_and_bridge`` 按 session id 去重，重跑只补新增的。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# 最小可用 config：ingest 阶段要读 mask_patterns，generate 阶段要读 llm。
# api_key 留占位，真跑时由环境变量覆盖（见 run_generate_job.py）。
_MOCK_CONFIG = """\
llm:
  base_url: {base_url}
  model: {model}
  api_key: {api_key}
  max_context: {max_context}
  compact_token_limit: {compact_token_limit}
  enable_spill: false
  request_timeout: 300
  connect_timeout: 15
# generate 不做检索，这段只为过 load_config 的必填校验。
embedding:
  base_url: {base_url}
  model: text-embedding-not-used
  api_key: {api_key}
skill_dir: skill
ingest:
  settle_seconds: 0
"""


def write_mock_config(xskill_home: Path, args: argparse.Namespace) -> Path:
    """写 mock home 的 config.yaml。已存在就不动（允许手工微调后重跑）。"""
    config_path = xskill_home / "config.yaml"
    if config_path.exists() and not args.force_config:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _MOCK_CONFIG.format(
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key,
            max_context=args.max_context,
            compact_token_limit=args.compact_token_limit,
        ),
        encoding="utf-8",
    )
    return config_path


# Cursor 有两种落盘布局，实测本机是后者：
#   <proj>/agent-transcripts/<sid>.jsonl          —— CURSOR_SPEC.sessions_glob 认的
#   <proj>/agent-transcripts/<sid>/<sid>.jsonl    —— 本机 Cursor 实际用的
# 产品的 spec 只写了前者，所以 ~/.xskill/cursor_sessions 一直是空的。这里
# 两种都收，并用 scan_and_bridge 的 candidate_paths 绕开 spec 的 glob
# （这是它的正式参数，不是绕后门）。sid 取文件名 stem，两种布局都对。
_TRANSCRIPT_GLOBS = (
    "*/agent-transcripts/*.jsonl",
    "*/agent-transcripts/*/*.jsonl",
)


def _find_transcripts(source_home: Path) -> list[Path]:
    projects = source_home / ".cursor" / "projects"
    found: dict[str, Path] = {}
    for pattern in _TRANSCRIPT_GLOBS:
        for path in projects.glob(pattern):
            if path.is_file():
                found[str(path)] = path
    return [found[key] for key in sorted(found)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 Cursor 历史会话全量导出到 mock xskill home",
    )
    parser.add_argument(
        "--source-home",
        default=os.environ.get("XSKILL_MOCK_SOURCE_HOME", ""),
        help="真实 HOME（含 .cursor/projects）。默认读 XSKILL_MOCK_SOURCE_HOME",
    )
    parser.add_argument(
        "--client",
        default="cursor-local",
        help="mock 出来的 client 目录名（现网是每个上传客户端一个目录）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只导前 N 条，0 = 全量。冒烟用",
    )
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--api-key", default="set-me-at-runtime")
    parser.add_argument("--max-context", type=int, default=200_000)
    parser.add_argument("--compact-token-limit", type=int, default=100_000)
    parser.add_argument(
        "--force-config",
        action="store_true",
        help="覆盖已存在的 config.yaml",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="先清空已有 traj_*.md/.json 再导，避免 unknown 旧文件留下",
    )
    args = parser.parse_args()

    if not args.source_home:
        parser.error(
            "必须给 --source-home（真实 HOME）；mock home 由 $HOME 决定，"
            "两者不能是同一个目录"
        )
    source_home = Path(args.source_home).expanduser().resolve()
    if not source_home.is_dir():
        parser.error(f"--source-home 不存在: {source_home}")

    from xskill.config import XSKILL_HOME

    xskill_home = Path(XSKILL_HOME)
    if xskill_home.resolve() == (source_home / ".xskill").resolve():
        parser.error(
            f"拒绝往真实 xskill home 写: {xskill_home}。"
            "先把 HOME 指到 mock 目录再跑"
        )
    xskill_home.mkdir(parents=True, exist_ok=True)
    write_mock_config(xskill_home, args)

    # skill_dir 要先在：generate 直接往这里新建 skill 目录。
    (xskill_home / "skill").mkdir(parents=True, exist_ok=True)

    sessions_dir = (
        xskill_home / "team_trajectories" / "clients" / args.client / "sessions"
    )
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if args.wipe:
        removed = 0
        for path in sessions_dir.glob("traj_*"):
            path.unlink()
            removed += 1
        print(f"已清空旧轨迹 {removed} 个文件")

    from xskill.ecosystems._shared import JsonlIngester
    from xskill.ecosystems.cursor import CURSOR_SPEC

    transcripts = _find_transcripts(source_home)
    if not transcripts:
        print(
            f"没在 {source_home}/.cursor/projects 下找到 agent-transcripts",
            file=sys.stderr,
        )
        return 2
    selected = transcripts[: args.limit] if args.limit > 0 else transcripts

    # settle_seconds=0：一次性导出不需要"还在写就等下一轮"的屏障，
    # 否则刚用过的 Cursor 会话会被跳过。
    ingester = JsonlIngester(CURSOR_SPEC, settle_seconds=0)
    records = ingester.scan_and_bridge(
        target_traj_dir=sessions_dir,
        home_root=source_home,
        candidate_paths=selected,
    )

    bridged = sorted(p.name for p in sessions_dir.glob("traj_*.md"))
    total_bytes = sum(
        (sessions_dir / name).stat().st_size for name in bridged
    )
    manifest = {
        "source_home": str(source_home),
        "sessions_dir": str(sessions_dir),
        "client": args.client,
        "source_transcripts_found": len(transcripts),
        "source_transcripts_selected": len(selected),
        "bridged_this_run": len(records),
        "traj_total": len(bridged),
        "traj_total_bytes": total_bytes,
        "traj_ids": [name[: -len(".md")] for name in bridged],
    }
    manifest_path = xskill_home / "mock_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"mock xskill home : {xskill_home}")
    print(f"轨迹目录         : {sessions_dir}")
    print(
        f"源会话 {len(transcripts)} 条，本次选 {len(selected)} 条，"
        f"新桥接 {len(records)} 条，目录里现共 {len(bridged)} 条 "
        f"({total_bytes / 1e6:.1f} MB)"
    )
    print(f"清单             : {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
