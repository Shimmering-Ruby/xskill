#!/usr/bin/env python3
"""
cli.py -- 统一 CLI 入口 (t2s)
================================
合并所有子命令: index, search, search-skill, process, batch,
init, status, reindex, eval, skill, show, validate
"""

import argparse, json, sys, logging
from pathlib import Path

from traj2skill.config import (
    set_overrides, load_config, get_traj_dir, get_skill_dir, get_output_dir,
)

logger = logging.getLogger("t2s")


# ===================================================================
# 子命令实现
# ===================================================================

def cmd_index(args, config):
    from traj2skill.llm_client import create_llm_client, create_embed_client
    from traj2skill.index import index_dataset

    llm = None if args.no_llm else create_llm_client(config)
    embed_client = create_embed_client(config)

    traj_dir = get_traj_dir()

    print("=" * 50)
    print("  T2S index (incremental)")
    print("=" * 50)
    print(f"  LLM:   {llm or '(disabled)'}")
    print(f"  Embed: {embed_client}")

    # 收集数据集
    datasets = []
    if args.path:
        d = Path(args.path)
        if d.is_dir():
            datasets.append(d)
        else:
            print(f"directory not found: {d}")
            return 1
    elif args.all:
        for d in sorted(traj_dir.iterdir()):
            if d.is_dir() and d.name != "raw" and not d.name.endswith("_bak") and list(d.glob("traj_*.md")):
                datasets.append(d)
    elif args.dataset:
        d = traj_dir / args.dataset
        if d.is_dir():
            datasets.append(d)
        else:
            print(f"dataset not found: {d}")
            return 1
    else:
        # 如果 traj_dir 本身有轨迹文件
        if list(traj_dir.glob("traj_*.md")):
            datasets.append(traj_dir)
        else:
            print("No path, --dataset, or --all specified. Use --help.")
            return 1

    if not datasets:
        print("No datasets found")
        return 1

    for dataset_dir in datasets:
        index_dataset(dataset_dir, llm, embed_client,
                      dry_run=args.dry_run, reindex=args.reindex,
                      concurrency=args.concurrency)

    print(f"\n{'='*50}")
    print("  Done!")
    print(f"{'='*50}")
    return 0


def cmd_search(args, config):
    from traj2skill.search import search, print_results
    from traj2skill.index import meta_to_index_text

    traj_dir = get_traj_dir()

    # 确定数据集目录
    if args.path:
        dataset_dir = Path(args.path)
    elif args.dataset:
        dataset_dir = traj_dir / args.dataset
    else:
        dataset_dir = traj_dir

    if not dataset_dir.is_dir():
        print(f"dataset not found: {dataset_dir}")
        return 1

    # 确定查询文本
    if args.query:
        query_text = args.query
    elif args.traj:
        traj_path = Path(args.traj)
        if not traj_path.exists():
            print(f"traj file not found: {traj_path}")
            return 1
        meta_path = traj_path.parent / f"{traj_path.name}.meta"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            query_text = meta_to_index_text(meta)
        else:
            query_text = traj_path.read_text(encoding="utf-8")[:2000]
    else:
        print("--query or --traj required")
        return 1

    print(f"Search: \"{query_text[:80]}{'...' if len(query_text)>80 else ''}\"")
    print(f"   dataset: {dataset_dir.name} | top_k={args.top_k} | filter={args.filter}")

    results = search(
        dataset_dir, query_text,
        top_k=args.top_k,
        min_similarity=getattr(args, 'min_sim', 0.0),
        success_filter=args.filter,
        config=config,
    )

    if args.json:
        clean = [{k: v for k, v in r.items() if k != "traj_json"} for r in results]
        print(json.dumps(clean, ensure_ascii=False, indent=2))
    else:
        print_results(results)
        print(f"\n  {len(results)} results")

    return 0


def cmd_search_skill(args, config):
    from traj2skill.llm_client import create_embed_client
    from traj2skill.skill_tools import init_context, search_skills

    skill_dir = get_skill_dir()
    traj_dir = get_traj_dir()
    embed = create_embed_client(config)
    llm = None
    init_context(skill_dir, traj_dir, llm, embed, config)

    result = search_skills(args.query, top_k=args.top_k)
    print(result)
    return 0


def cmd_process(args, config):
    from traj2skill.process import process_traj

    traj_path = args.path or args.traj
    if not traj_path:
        print("traj path required (positional or --traj)")
        return 1

    result = process_traj(traj_path, config, dry_run=args.dry_run)
    print(f"\n  Result: {json.dumps(result, ensure_ascii=False, default=str)[:500]}")
    return 0 if result.get("action") != "error" else 1


def cmd_batch(args, config):
    from traj2skill.process import process_traj

    traj_dir = get_traj_dir()

    if args.path:
        dataset_dir = Path(args.path)
    elif args.dataset:
        dataset_dir = traj_dir / args.dataset
    else:
        dataset_dir = traj_dir

    if not dataset_dir.is_dir():
        print(f"dataset not found: {dataset_dir}")
        return 1

    md_files = sorted(dataset_dir.glob("traj_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(".meta")]
    if args.max:
        md_files = md_files[:args.max]

    print(f"Batch: {len(md_files)} trajectories from {dataset_dir.name}")

    results = {"merged": 0, "rejected": 0, "skipped": 0, "errors": 0}
    for i, md_file in enumerate(md_files):
        print(f"\n{'---'*18}")
        print(f"  [{i+1}/{len(md_files)}] {md_file.name}")
        print(f"{'---'*18}")
        result = process_traj(str(md_file), config, dry_run=args.dry_run)
        action = result.get("action", "error")
        results[action] = results.get(action, 0) + 1

    print(f"\n{'='*55}")
    print(f"  Batch done")
    print(f"  merged: {results.get('merged', 0)}  rejected: {results.get('rejected', 0)}  "
          f"skipped: {results.get('skipped', 0) + results.get('skip', 0)}  "
          f"errors: {results.get('errors', 0) + results.get('error', 0)}")
    print(f"{'='*55}")
    return 0


def cmd_init(args, config):
    from traj2skill.git_lock import ensure_repo

    skill_dir = get_skill_dir()
    ensure_repo(str(skill_dir))
    print(f"skill repo initialized: {skill_dir}")
    return 0


def cmd_reindex(args, config):
    from traj2skill.llm_client import create_llm_client, create_embed_client
    from traj2skill.skill_tools import init_context, rebuild_skill_index

    skill_dir = get_skill_dir()
    traj_dir = get_traj_dir()
    llm = create_llm_client(config)
    embed = create_embed_client(config)
    init_context(skill_dir, traj_dir, llm, embed, config)
    rebuild_skill_index()
    print("skill index rebuilt")
    return 0


def cmd_status(args, config):
    from traj2skill.git_lock import current_branch

    skill_dir = get_skill_dir()
    print(f"\n  skill dir: {skill_dir}")
    if not skill_dir.exists():
        print("  status: not initialized (run: t2s init)")
        return 0

    branch = current_branch(str(skill_dir))
    print(f"  git branch: {branch} {'(locked)' if branch != 'main' else '(free)'}")

    skills = [d for d in skill_dir.iterdir()
              if d.is_dir() and not d.name.startswith(".")]
    print(f"  skill count: {len(skills)}")
    for d in sorted(skills):
        ap = d / ".abstract"
        if ap.exists():
            try:
                abstract = json.loads(ap.read_text(encoding="utf-8"))
                score = abstract.get("eval_result", {}).get("eval_score", "?")
                ver = abstract.get("version", "?")
                frozen = " [FROZEN]" if abstract.get("frozen") else ""
                print(f"    {d.name}  v{ver}  score={score}  "
                      f"tags={abstract.get('tags', [])}{frozen}")
            except (json.JSONDecodeError, Exception):
                print(f"    {d.name}  (bad .abstract)")
        else:
            print(f"    {d.name}  (no .abstract)")

    index_path = skill_dir / ".skill_index.pkl"
    print(f"  vector index: {'exists' if index_path.exists() else 'missing'}")
    return 0


def cmd_eval(args, config):
    from traj2skill.llm_client import create_llm_client
    from traj2skill.skill_eval import run_eval
    from traj2skill.log import StreamLog

    skill_dir = get_skill_dir()

    if getattr(args, 'list', False):
        # list eval history for all skills
        for d in sorted(skill_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            ap = d / ".abstract"
            if ap.exists():
                try:
                    abstract = json.loads(ap.read_text(encoding="utf-8"))
                    er = abstract.get("eval_result", {})
                    score = er.get("eval_score", "?")
                    tier = er.get("tier", "?")
                    print(f"  {d.name}  tier={tier}  score={score}")
                except (json.JSONDecodeError, Exception):
                    print(f"  {d.name}  (no eval)")
            else:
                print(f"  {d.name}  (no abstract)")
        return 0

    if not args.skill:
        print("--skill required (or --list)")
        return 1

    skill_path = skill_dir / args.skill
    if not skill_path.is_dir():
        print(f"skill not found: {skill_path}")
        return 1

    llm = create_llm_client(config)
    if not llm:
        print("LLM config required for eval")
        return 1

    log = StreamLog()
    result = run_eval(
        skill_path, llm, n_runs=args.n_runs, log_fn=log, config=config,
        force_sandbox=getattr(args, 'sandbox', False),
        force_no_sandbox=getattr(args, 'no_sandbox', False),
    )
    print(f"\n  Result: {json.dumps(result, ensure_ascii=False, indent=2)}")
    return 0


def cmd_skill(args, config):
    from traj2skill.skill_manager import (
        list_skills, show_skill, skill_log, skill_diff,
        rollback_skill, freeze_skill, unfreeze_skill,
        delete_skill, export_skill, import_skill,
    )

    skill_dir = get_skill_dir()
    action = args.skill_action

    if action == "list":
        skills = list_skills(skill_dir)
        if not skills:
            print("  (no skills)")
            return 0
        for s in skills:
            frozen = " [FROZEN]" if s["frozen"] else ""
            score = s["eval_score"] if s["eval_score"] is not None else "?"
            print(f"  {s['name']}  v{s['version']}  score={score}  tags={s['tags']}{frozen}")
        return 0

    elif action == "show":
        if not args.name:
            print("skill name required")
            return 1
        info = show_skill(skill_dir, args.name)
        if "error" in info:
            print(f"  {info['error']}")
            return 1
        print(f"\n=== {info['name']} ===")
        print(f"\n--- skill.md ---\n{info['skill_md']}")
        if info.get("abstract"):
            print(f"\n--- abstract ---\n{json.dumps(info['abstract'], ensure_ascii=False, indent=2)}")
        if info.get("eval"):
            print(f"\n--- eval ---\n{json.dumps(info['eval'], ensure_ascii=False, indent=2)}")
        return 0

    elif action == "log":
        if not args.name:
            print("skill name required")
            return 1
        out = skill_log(skill_dir, args.name)
        print(out)
        return 0

    elif action == "diff":
        if not args.name:
            print("skill name required")
            return 1
        v1 = args.v1 if hasattr(args, 'v1') else None
        v2 = args.v2 if hasattr(args, 'v2') else None
        out = skill_diff(skill_dir, args.name, v1, v2)
        print(out)
        return 0

    elif action == "rollback":
        if not args.name:
            print("skill name required")
            return 1
        version = getattr(args, 'version', None)
        ok = rollback_skill(skill_dir, args.name, version)
        print(f"  rollback {'ok' if ok else 'failed'}")
        return 0 if ok else 1

    elif action == "freeze":
        if not args.name:
            print("skill name required")
            return 1
        ok = freeze_skill(skill_dir, args.name)
        print(f"  freeze {'ok' if ok else 'failed'}")
        return 0 if ok else 1

    elif action == "unfreeze":
        if not args.name:
            print("skill name required")
            return 1
        ok = unfreeze_skill(skill_dir, args.name)
        print(f"  unfreeze {'ok' if ok else 'failed'}")
        return 0 if ok else 1

    elif action == "delete":
        if not args.name:
            print("skill name required")
            return 1
        ok = delete_skill(skill_dir, args.name)
        print(f"  delete {'ok' if ok else 'failed'}")
        return 0 if ok else 1

    elif action == "export":
        if not args.name:
            print("skill name required")
            return 1
        output = Path(args.output) if hasattr(args, 'output') and args.output else Path.cwd()
        try:
            result = export_skill(skill_dir, args.name, output)
            print(f"  exported to: {result}")
        except FileNotFoundError as e:
            print(f"  {e}")
            return 1
        return 0

    elif action == "import":
        source = args.source if hasattr(args, 'source') and args.source else args.name
        if not source:
            print("source path required")
            return 1
        try:
            name = import_skill(skill_dir, Path(source))
            print(f"  imported: {name}")
        except FileNotFoundError as e:
            print(f"  {e}")
            return 1
        return 0

    else:
        print(f"unknown skill action: {action}")
        return 1


def cmd_show(args, config):
    skill_dir = get_skill_dir()

    if args.skill:
        from traj2skill.skill_manager import show_skill
        info = show_skill(skill_dir, args.skill)
        if "error" in info:
            print(f"  {info['error']}")
            return 1
        print(f"\n=== {info['name']} ===")
        print(f"\n{info['skill_md']}")
        if info.get("abstract"):
            print(f"\n--- abstract ---")
            for k, v in info["abstract"].items():
                if k != "eval_result":
                    print(f"  {k}: {v}")
        return 0

    if args.traj:
        traj_path = Path(args.traj)
        if not traj_path.exists():
            print(f"file not found: {traj_path}")
            return 1
        meta_path = traj_path.parent / f"{traj_path.name}.meta"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for k, v in meta.items():
                if k != "raw_xml":
                    print(f"  {k}: {v}")
        else:
            print(f"  (no .meta for {traj_path.name})")
        return 0

    print("--skill or --traj required")
    return 1


def cmd_validate(args, config):
    """Validate trajectory directory structure"""
    path = Path(args.path) if args.path else get_traj_dir()
    if not path.is_dir():
        print(f"directory not found: {path}")
        return 1

    md_files = sorted(path.glob("traj_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(".meta")]

    if not md_files:
        print(f"  no traj_*.md files found in {path}")
        return 1

    ok = 0
    warnings = 0
    for md_file in md_files:
        json_file = md_file.with_suffix(".json")
        meta_file = md_file.parent / f"{md_file.name}.meta"
        issues = []
        if not json_file.exists():
            issues.append("no .json")
        if not meta_file.exists():
            issues.append("no .meta")
        else:
            try:
                from traj2skill.index import validate_meta
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if not validate_meta(meta):
                    issues.append("meta invalid")
            except Exception:
                issues.append("meta parse error")

        if issues:
            print(f"  [WARN] {md_file.name}: {', '.join(issues)}")
            warnings += 1
        else:
            ok += 1

    print(f"\n  {ok} ok, {warnings} warnings, {len(md_files)} total")
    return 0


# ===================================================================
# argparse 构建
# ===================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="t2s",
        description="traj2skill -- distill reusable Skills from AI Agent trajectories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 全局 flags
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--quiet", action="store_true", help="quiet mode")
    parser.add_argument("--config", type=str, help="config file path")
    parser.add_argument("--skill-dir", type=str, help="override skill directory")
    parser.add_argument("--traj-dir", type=str, help="override trajectory directory")
    parser.add_argument("--llm-base-url", type=str, help="override LLM base_url")
    parser.add_argument("--llm-model", type=str, help="override LLM model")
    parser.add_argument("--llm-key", type=str, help="override LLM api_key")

    sub = parser.add_subparsers(dest="command")

    # --- index ---
    p_index = sub.add_parser("index", help="Build trajectory index (incremental)")
    p_index.add_argument("path", nargs="?", type=str, help="trajectory directory path")
    p_index.add_argument("--dataset", type=str, help="dataset name under traj_dir")
    p_index.add_argument("--all", action="store_true", help="index all datasets")
    p_index.add_argument("--concurrency", "-c", type=int, default=10, help="LLM concurrency")
    p_index.add_argument("--no-llm", action="store_true", help="rule-only meta extraction")
    p_index.add_argument("--dry-run", action="store_true", help="show prompts, don't call LLM")
    p_index.add_argument("--reindex", action="store_true", help="force full rebuild")

    # --- search ---
    p_search = sub.add_parser("search", help="Search similar trajectories")
    p_search.add_argument("path", nargs="?", type=str, help="trajectory directory path")
    p_search.add_argument("--dataset", type=str, help="dataset name")
    p_search.add_argument("--query", "-q", type=str, help="natural language query")
    p_search.add_argument("--traj", type=str, help="use a traj .md as query")
    p_search.add_argument("--top-k", "-k", type=int, default=5)
    p_search.add_argument("--filter", choices=["all", "success", "failure"], default="all")
    p_search.add_argument("--json", action="store_true", help="JSON output")

    # --- search-skill ---
    p_ssearch = sub.add_parser("search-skill", help="Search existing skills")
    p_ssearch.add_argument("--query", "-q", type=str, required=True, help="query")
    p_ssearch.add_argument("--top-k", "-k", type=int, default=5)

    # --- process ---
    p_proc = sub.add_parser("process", help="Process single trajectory")
    p_proc.add_argument("path", nargs="?", type=str, help="trajectory .md file path")
    p_proc.add_argument("--traj", type=str, help="trajectory .md file path (compat)")
    p_proc.add_argument("--dry-run", action="store_true")

    # --- batch ---
    p_batch = sub.add_parser("batch", help="Batch process trajectories")
    p_batch.add_argument("path", nargs="?", type=str, help="trajectory directory path")
    p_batch.add_argument("--dataset", type=str, help="dataset name")
    p_batch.add_argument("--max", type=int, default=None, help="max trajectories")
    p_batch.add_argument("--dry-run", action="store_true")

    # --- init ---
    sub.add_parser("init", help="Initialize skill git repo")

    # --- status ---
    sub.add_parser("status", help="Show skill repo status")

    # --- reindex ---
    sub.add_parser("reindex", help="Rebuild skill vector index")

    # --- eval ---
    p_eval = sub.add_parser("eval", help="Evaluate a skill")
    p_eval.add_argument("--skill", type=str, help="skill name")
    p_eval.add_argument("--n-runs", type=int, default=3, help="LLM scoring runs")
    p_eval.add_argument("--sandbox", action="store_true", help="force sandbox eval")
    p_eval.add_argument("--no-sandbox", action="store_true", help="skip sandbox")
    p_eval.add_argument("--list", action="store_true", help="list eval history")

    # --- skill ---
    p_skill = sub.add_parser("skill", help="Skill version management")
    p_skill.add_argument("skill_action", choices=[
        "list", "show", "log", "diff", "rollback",
        "freeze", "unfreeze", "delete", "export", "import",
    ], help="action")
    p_skill.add_argument("name", nargs="?", type=str, help="skill name")
    p_skill.add_argument("v1", nargs="?", type=str, help="version 1 (for diff)")
    p_skill.add_argument("v2", nargs="?", type=str, help="version 2 (for diff)")
    p_skill.add_argument("--version", type=str, help="target version (for rollback)")
    p_skill.add_argument("-o", "--output", type=str, help="output path (for export)")
    p_skill.add_argument("--source", type=str, help="source path (for import)")

    # --- show ---
    p_show = sub.add_parser("show", help="Show skill or trajectory details")
    p_show.add_argument("--skill", type=str, help="skill name")
    p_show.add_argument("--traj", type=str, help="trajectory .md file path")

    # --- validate ---
    p_validate = sub.add_parser("validate", help="Validate trajectory directory")
    p_validate.add_argument("path", nargs="?", type=str, help="trajectory directory path")

    return parser


# ===================================================================
# main
# ===================================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # 全局覆盖
    set_overrides(
        debug=args.debug,
        quiet=args.quiet,
        config=getattr(args, 'config', None),
        skill_dir=getattr(args, 'skill_dir', None),
        traj_dir=getattr(args, 'traj_dir', None),
    )

    # 日志级别
    if args.debug:
        logging.basicConfig(level=logging.DEBUG,
                            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
                            datefmt="%H:%M:%S")
    elif args.quiet:
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s",
                            datefmt="%H:%M:%S")

    # 压掉噪音日志
    for _noisy in ("httpx", "httpcore", "openai", "traj2skill.llm_client", "agno"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    # 加载配置
    config = load_config()

    # CLI 覆盖 LLM 配置
    if getattr(args, 'llm_base_url', None):
        config.setdefault("llm", {})["base_url"] = args.llm_base_url
    if getattr(args, 'llm_model', None):
        config.setdefault("llm", {})["model"] = args.llm_model
    if getattr(args, 'llm_key', None):
        config.setdefault("llm", {})["api_key"] = args.llm_key

    cmd_map = {
        "index": cmd_index,
        "search": cmd_search,
        "search-skill": cmd_search_skill,
        "process": cmd_process,
        "batch": cmd_batch,
        "init": cmd_init,
        "status": cmd_status,
        "reindex": cmd_reindex,
        "eval": cmd_eval,
        "skill": cmd_skill,
        "show": cmd_show,
        "validate": cmd_validate,
    }

    handler = cmd_map.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args, config)


if __name__ == "__main__":
    sys.exit(main() or 0)
