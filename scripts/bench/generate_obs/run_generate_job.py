#!/usr/bin/env python3
"""跑一趟 generate 并把行为特征落成 JSON。

装配跟产品完全同路：``collect_read_roots`` 算可读目录、
``create_agent_tool_context`` 绑工具上下文、``make_default_factory`` 造
agno agent、``GenerateAgent.run`` 跑。和 ``team/server/generate_jobs.py``
里的 ``_run_generate_job_body`` 是同一套零件，只是不写 job 状态、不 pin
skill——这里要观测的是 agent 自己的行为，不是 server 的作业管理。

``HOME`` 必须先指到 mock home：xskill 的 ``XSKILL_HOME`` 是 import 时按
``Path.home()`` 定的。容器里由 run.sh 负责。

输出（``--out`` 目录）：
    features.json   这趟的行为特征（compact 次数、工具调用次数、读了哪些轨迹）
    spans.jsonl     OTel span 原始记录
    run.json        这趟的入参与结果摘要
    trace/          人读的逐轮 agent trace
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_INSTRUCTION = (
    "看看我最近这些 Cursor 会话里反复出现的做法，挑一个最值得复用的，"
    "写成一个 skill 提交到 main。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="跑一趟 generate 并统计行为特征",
    )
    parser.add_argument(
        "--job",
        required=True,
        help="这趟实验的名字。features.json 和 Phoenix 都按它标记",
    )
    parser.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    parser.add_argument(
        "--instruction-file",
        default="",
        help="从文件读指令，优先于 --instruction",
    )
    parser.add_argument("--user-id", default="obs-user")
    parser.add_argument(
        "--out",
        default="",
        help="输出目录。默认 <mock home>/obs-runs/<job>",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不打模型：换掉 HTTP 层，其余原路跑。用来验埋点和工具链",
    )
    parser.add_argument(
        "--fake-reads",
        type=int,
        default=8,
        help="dry-run 时假模型读几条轨迹。读得多会真触发 compact",
    )
    parser.add_argument(
        "--max-context",
        type=int,
        default=0,
        help="覆盖 config 的 llm.max_context，0 = 用 config 的",
    )
    parser.add_argument(
        "--compact-token-limit",
        type=int,
        default=0,
        help="覆盖 config 的 llm.compact_token_limit，0 = 用 config 的",
    )
    parser.add_argument(
        "--phoenix-endpoint",
        default=os.environ.get("XSKILL_OTEL_ENDPOINT", ""),
        help="Phoenix / OTLP HTTP 接收端，如 http://172.17.0.1:6006",
    )
    parser.add_argument(
        "--capture-content",
        action="store_true",
        help="span 里记截断后的提示词与回答（默认不记）",
    )
    return parser.parse_args()


def resolve_instruction(args: argparse.Namespace) -> str:
    if args.instruction_file:
        return Path(args.instruction_file).read_text(encoding="utf-8").strip()
    return args.instruction.strip()


def _graphql(endpoint: str, query: str, variables: dict | None = None) -> dict:
    import urllib.request

    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        base = base[: -len("/v1/traces")]
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{base}/graphql",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload.get("data") or {}


def _trace_id_from_spans(out_dir: Path) -> str:
    path = out_dir / "spans.jsonl"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("trace_id"):
            return str(row["trace_id"])
    return ""


def resolve_phoenix_link(
    *,
    job: str,
    project: str,
    out_dir: Path,
    endpoint: str,
    public_base: str,
    timeout_s: float = 20.0,
) -> dict:
    """等 Phoenix 收下这趟 session，拼出可点的 Sessions 瀑布链接。"""
    from urllib.parse import quote

    trace_id = _trace_id_from_spans(out_dir)
    result = {
        "project": project,
        "session_id": job,
        "trace_id": trace_id,
        "project_gid": "",
        "session_gid": "",
        "span_gid": "",
        "url": "",
        "error": "",
    }
    if not endpoint:
        result["error"] = "没有 OTLP endpoint，span 只写了本地 jsonl"
        return result

    query = """
    query($name: String!) {
      getProjectByName(name: $name) {
        id
        name
        hasTraces
        recordCount
        sessionCount
        sessions(first: 50) {
          edges {
            node {
              id
              sessionId
              numTraces
              traces(first: 5) {
                edges { node { id traceId rootSpan { id name } } }
              }
            }
          }
        }
      }
    }
    """
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            data = _graphql(endpoint, query, {"name": project})
            proj = data.get("getProjectByName") or {}
            result["project_gid"] = proj.get("id") or ""
            for edge in ((proj.get("sessions") or {}).get("edges") or []):
                node = edge.get("node") or {}
                if node.get("sessionId") != job:
                    continue
                result["session_gid"] = node.get("id") or ""
                traces = ((node.get("traces") or {}).get("edges") or [])
                if traces:
                    tnode = traces[0].get("node") or {}
                    result["trace_id"] = tnode.get("traceId") or result["trace_id"]
                    root = tnode.get("rootSpan") or {}
                    result["span_gid"] = root.get("id") or ""
                break
            if result["project_gid"] and result["session_gid"] and result["trace_id"]:
                public = public_base.rstrip("/")
                session_q = quote(result["session_gid"], safe="")
                url = (
                    f"{public}/projects/{result['project_gid']}"
                    f"/sessions/{session_q}"
                    f"?timeRangeKey=7d&sessionView=traces"
                    f"&selectedTraceId={result['trace_id']}"
                )
                if result["span_gid"]:
                    url += f"&selectedSpanNodeId={quote(result['span_gid'], safe='')}"
                result["url"] = url
                return result
            last_error = (
                f"project={proj.get('name')} traces={proj.get('recordCount')} "
                f"sessions={proj.get('sessionCount')}"
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    result["error"] = last_error or "Phoenix 还没有这趟 session"
    return result


def main() -> int:
    args = parse_args()

    from xskill.config import XSKILL_HOME

    xskill_home = Path(XSKILL_HOME)
    if not (xskill_home / "config.yaml").exists():
        print(
            f"{xskill_home}/config.yaml 不在。先跑 export_cursor_mock.py "
            "把 mock home 建起来，并确认 HOME 指对了。",
            file=sys.stderr,
        )
        return 2

    out_dir = (
        Path(args.out) if args.out else xskill_home / "obs-runs" / args.job
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # obs 层全靠环境变量开关，且是懒读，所以在 import 之后设也生效。
    os.environ["XSKILL_OTEL"] = "1"
    os.environ["XSKILL_OTEL_JOB"] = args.job
    os.environ["XSKILL_OTEL_SESSION"] = args.job
    os.environ["XSKILL_OTEL_OUT"] = str(out_dir)
    os.environ.setdefault("XSKILL_OTEL_PROJECT", "xskill-generate")
    if args.phoenix_endpoint:
        os.environ["XSKILL_OTEL_ENDPOINT"] = args.phoenix_endpoint
    if args.capture_content:
        os.environ["XSKILL_OTEL_CAPTURE_CONTENT"] = "1"

    from xskill import obs
    from xskill.agents import agent_tools
    from xskill.agents.agno_factory import make_default_factory
    from xskill.agents.generate_agent import GenerateAgent
    from xskill.config import get_config
    from xskill.team.server.generate_jobs import collect_read_roots

    config = dict(get_config())
    llm_section = dict(config.get("llm") or {})
    # 真跑时 api_key 从环境变量来，不落 mock config.yaml。
    env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get(
        "LLM_API_KEY", ""
    )
    if env_key:
        llm_section["api_key"] = env_key
    if args.max_context:
        llm_section["max_context"] = args.max_context
    if args.compact_token_limit:
        llm_section["compact_token_limit"] = args.compact_token_limit
    config["llm"] = llm_section

    # 每趟 job 自己的 skill 目录，避免上一趟 dry-run 占位 skill 混进可读范围。
    skill_dir = out_dir / "workspace" / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    traj_root = xskill_home / "team_trajectories"
    registry_db = xskill_home / "registry.db"
    logs_dir = out_dir / "trace"
    spill_root = out_dir / "spill"
    spill_root.mkdir(parents=True, exist_ok=True)

    extra_roots = collect_read_roots(skill_dir, traj_root, db_path=registry_db)
    sessions_dirs = sorted(
        p for p in traj_root.glob("clients/*/sessions") if p.is_dir()
    )
    traj_files = [
        path for sessions in sessions_dirs
        for path in sorted(sessions.glob("traj_*.md"))
    ]

    job_id = f"{args.job}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    instruction = resolve_instruction(args)

    print(f"job            : {args.job}")
    print(f"mock home      : {xskill_home}")
    print(f"skill_dir      : {skill_dir}")
    print(f"traj_root      : {traj_root}")
    print(f"可读根          : {len(extra_roots)} 个")
    print(f"轨迹条数        : {len(traj_files)}")
    print(f"max_context    : {llm_section.get('max_context')}")
    print(f"compact 阈值    : {llm_section.get('compact_token_limit')}")
    print(f"模式            : {'dry-run（不打模型）' if args.dry_run else '真打模型'}")
    print(f"输出            : {out_dir}")

    agent_tools.reset_generate_session()
    obs.reset_collector()
    tool_context = agent_tools.create_agent_tool_context(
        skill_dir=skill_dir,
        data_dir=skill_dir,
        config=config,
        atom_skill_dir=skill_dir,
        default_traj_root=traj_root,
        spill_root=spill_root,
        extra_read_roots=tuple(extra_roots),
        generate_user_id=args.user_id,
        registry_db_path=registry_db,
        blocked_read_roots=(),
    )
    llm_cfg = {**llm_section, **(config.get("llm_skill") or {})}
    factory = make_default_factory(config, spill_root=spill_root)
    agent = GenerateAgent(
        skill_dir=skill_dir,
        agno_agent_factory=factory,
        llm_cfg=llm_cfg,
        logs_dir=logs_dir,
        extra_read_roots=tuple(extra_roots),
    )

    started = time.time()
    error = ""
    answer = ""
    token = agent_tools.bind_agent_tool_context(tool_context)
    try:
        if args.dry_run:
            from fake_model import FakeScript, fake_openai_client
            script = FakeScript(
                sessions_dir=(
                    sessions_dirs[0] if sessions_dirs else traj_root
                ),
                skill_name="generate-obs-dryrun",
                reads=args.fake_reads,
            )
            with fake_openai_client(script, llm_cfg.get("model") or "fake"):
                answer = agent.run(
                    instruction=instruction,
                    user_id=args.user_id,
                    job_id=job_id,
                )
        else:
            answer = agent.run(
                instruction=instruction,
                user_id=args.user_id,
                job_id=job_id,
            )
    except Exception as exc:  # noqa: BLE001 — 失败也要留下特征
        error = f"{type(exc).__name__}: {exc}"
        print(f"\ngenerate 抛错: {error}", file=sys.stderr)
    finally:
        committed = agent_tools.generate_committed_skills()
        agent_tools.reset_agent_tool_context(token)

    elapsed = time.time() - started
    run_meta = {
        "job": args.job,
        "job_id": job_id,
        "user_id": args.user_id,
        "instruction": instruction,
        "dry_run": args.dry_run,
        "fake_reads": args.fake_reads if args.dry_run else None,
        "mock_home": str(xskill_home),
        "traj_available": len(traj_files),
        "read_roots": [str(p) for p in extra_roots],
        "max_context": llm_section.get("max_context"),
        "compact_token_limit": llm_section.get("compact_token_limit"),
        "model": llm_cfg.get("model"),
        "base_url": llm_cfg.get("base_url"),
        "wall_seconds": round(elapsed, 3),
        "committed_skills": committed,
        "answer_chars": len(answer or ""),
        "error": error,
    }
    phoenix = resolve_phoenix_link(
        job=args.job,
        project=os.environ.get("XSKILL_OTEL_PROJECT") or "xskill-generate",
        out_dir=out_dir,
        endpoint=os.environ.get("XSKILL_OTEL_ENDPOINT", ""),
        public_base=os.environ.get(
            "XSKILL_OTEL_PUBLIC_BASE", "http://8.219.96.11:8873"
        ),
    )
    run_meta["phoenix"] = phoenix
    (out_dir / "run.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    features_file = out_dir / "features.json"
    print(f"\n耗时 {elapsed:.1f}s，提交 skill: {committed or '（无）'}")
    if features_file.exists():
        summary = json.loads(features_file.read_text(encoding="utf-8"))
        print(
            f"compact {summary['compact_count']} 次 | "
            f"模型 {summary['llm_rounds']} 轮 | "
            f"工具 {summary['tool_call_total']} 次 | "
            f"读到轨迹 {summary['read_traj_count']} 条"
        )
        print(f"工具分布: {summary['tool_calls']}")
        print(f"特征     : {features_file}")
    else:
        print(f"没生成 {features_file}", file=sys.stderr)
        return 1
    if phoenix.get("url"):
        print(f"Phoenix  : {phoenix['url']}")
    elif phoenix.get("error"):
        print(f"Phoenix  : 还没有链接（{phoenix['error']}）", file=sys.stderr)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
