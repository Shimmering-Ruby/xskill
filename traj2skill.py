#!/usr/bin/env python3
"""
traj2skill.py — 从轨迹自动蒸馏 Skill
═══════════════════════════════════════

用法:
  # 处理单条轨迹
  python traj2skill.py process --traj data/swe_smith_dataset/traj_0042.md

  # 批量处理
  python traj2skill.py batch --dataset swe_smith_dataset --max 10

  # 只看 agent 会做什么, 不实际写入
  python traj2skill.py process --traj data/swe_smith_dataset/traj_0042.md --dry-run

  # 初始化 skill 仓库
  python traj2skill.py init

  # 重建 skill 索引
  python traj2skill.py reindex

  # 查看状态
  python traj2skill.py status

  # 手动触发 eval
  python traj2skill.py eval --skill fix_orm_query
"""

import argparse, json, re, sys, os, logging, time
from pathlib import Path
from datetime import datetime

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from modules.llm_client import create_llm_client, create_embed_client
from modules.git_lock import (
    ensure_repo, acquire_lock, release_lock, commit_changes,
    merge_to_main, is_on_main, has_changes, make_branch_name, current_branch,
)
from modules.skill_eval import run_eval, should_merge
from modules.skill_tools import (
    init_context, search_similar_trajs, search_skills, read_file,
    create_skill, write_file, update_abstract, rebuild_skill_index,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("traj2skill")

# 压掉噪音日志
for _noisy in ("httpx", "httpcore", "openai", "modules.llm_client", "agno"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

SKILL_DIR = ROOT / "skill"
DATA_DIR = ROOT / "data"


def load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f)
    return {}


# ═══════════════════════════════════════════════════════════════════
# 流式输出
# ═══════════════════════════════════════════════════════════════════

class StreamLog:
    """带前缀的流式日志，方便 grep 和观测"""
    def __init__(self, verbose=True):
        self.verbose = verbose
        self.events = []

    def __call__(self, msg: str, tag: str = "info"):
        entry = {"t": datetime.now().isoformat(), "tag": tag, "msg": msg}
        self.events.append(entry)
        if self.verbose:
            icon = {"step": "▶", "tool": "🔧", "decision": "💡", "git": "📦",
                    "eval": "📊", "error": "❌", "ok": "✅"}.get(tag, "  ")
            print(f"  {icon} [{tag}] {msg}", flush=True)

    def save(self, path: Path):
        path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# Agent System Prompt
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一个 Skill 管理 Agent。你的任务是分析新进来的 agent 执行轨迹，
判断是否需要创建新的 skill 或更新已有 skill。

## 你的能力
- search_similar_trajs(query, top_k, filter): 检索相似历史轨迹
- search_skills(query, top_k): 检索已有 skill
- read_file(path): 读取任意文件
- create_skill(skill_name): 创建新 skill 目录
- write_file(path, content): 写入/修改文件 (仅 ./skill/ 目录下)

注意：摘要生成和向量化由系统在 eval 通过合入后自动完成，你不需要管。

## 决策流程

1. 先仔细阅读新轨迹
2. 用 search_skills 检索是否已有覆盖此场景的 skill
3. 用 search_similar_trajs 找相似的成功和失败轨迹

然后判断:

### 情况 A: 已有高度相似的 skill
  → 读取该 skill, 判断新轨迹是否提供了新信息
  → 有新 pitfall 或优化 → update skill.md
  → 没有 → 不做任何操作

### 情况 B: 无高度相似 skill, 但从轨迹和检索结果中能看出一类可复用的任务模式
  → 创建新 skill
  → 从多条相似轨迹的交集提取 subgoal
  → 从失败轨迹提取 pitfalls

### 情况 C: 信号不足, 无法提炼出有价值的 skill
  → 不做任何操作

判断相似度时，不要依赖具体数值阈值，而是读取 skill 内容后根据 trigger 和 steps 的覆盖范围做语义判断。

## Skill 编写规范

skill.md 必须包含:
  ## trigger
  1-2 句描述适用场景

  ## steps
  按 subgoal 分段, 每段包含具体工具调用步骤

  ## pitfalls
  来自失败轨迹的错误模式和修复方式

约束:
- skill 名称用英文下划线 (如 fix_orm_n_plus_one)
- subgoal 2~8 个
- 步骤必须具体到工具名和操作, 不能笼统
- 写完 skill.md 即可，摘要和索引由系统自动处理

## Trigger 粒度自检
写完 trigger 后自问: 这个 trigger 是否只适用于当前这一条轨迹？如果是, 太细, 放宽。
是否任何 bug 修复都能命中？如果是, 太粗, 收紧。
好的 trigger 应该能覆盖"同一类"问题, 但不会误命中无关场景。"""


# ═══════════════════════════════════════════════════════════════════
# Agent 执行 (agno)
# ═══════════════════════════════════════════════════════════════════

def run_agent_agno(new_traj_md: str, new_traj_meta: dict, config: dict, log: StreamLog) -> dict:
    """用 agno Agent 执行"""
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    llm_cfg = config.get("llm", {})
    model = OpenAIChat(
        id=llm_cfg.get("model", "gpt-4o"),
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key") or os.environ.get("LLM_API_KEY", ""),
        role_map={
            "system": "system",
            "user": "user",
            "assistant": "assistant",
            "tool": "tool",
            "model": "assistant",
        },
    )

    user_prompt = f"""## 新轨迹内容
{new_traj_md[:6000]}

## 新轨迹 Meta
{json.dumps(new_traj_meta, ensure_ascii=False, indent=2)[:2000]}

请分析这条轨迹，按决策流程执行。"""

    agent = Agent(
        model=model,
        tools=[
            search_similar_trajs,
            search_skills,
            read_file,
            create_skill,
            write_file,
        ],
        instructions=[SYSTEM_PROMPT],
        system_message_role="system",
        markdown=True,
    )

    stream = agent.run(user_prompt, stream=True, stream_events=True)

    content = ""
    in_reasoning = False
    for chunk in stream:
        event = getattr(chunk, 'event', None)

        # 思考 token
        reasoning = getattr(chunk, 'reasoning_content', None)
        if event == 'RunContent' and reasoning:
            if not in_reasoning:
                print("  💭 ", end="", flush=True)
                in_reasoning = True
            print(reasoning, end="", flush=True)

        # 回复 token
        text = getattr(chunk, 'content', None)
        if event == 'RunContent' and text:
            if in_reasoning:
                print(flush=True)
                in_reasoning = False
            print(text, end="", flush=True)
            content += text

        # 工具调用开始
        if event == 'ToolCallStarted':
            if in_reasoning:
                print(flush=True)
                in_reasoning = False
            tool = getattr(chunk, 'tool', None)
            if tool:
                name = getattr(tool, 'tool_name', '?')
                args = getattr(tool, 'tool_args', {})
                short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items()) if args else ""
                log(f"{name}({short_args})", "tool")

        # 工具调用完成
        if event == 'ToolCallCompleted':
            tool = getattr(chunk, 'tool', None)
            result_text = getattr(chunk, 'content', '') or ''
            if tool:
                name = getattr(tool, 'tool_name', '?')
                # 取结果摘要
                tool_result = getattr(tool, 'tool_result', None)
                if tool_result:
                    result_str = str(tool_result)
                    if result_str.startswith('[') or result_str.startswith('{'):
                        try:
                            data = json.loads(result_str)
                            if isinstance(data, list):
                                preview = f"[{len(data)} 条结果]"
                            elif isinstance(data, dict) and 'results' in data:
                                preview = f"[{len(data['results'])} 条结果]"
                            else:
                                preview = result_str[:80]
                        except Exception:
                            preview = result_str[:80]
                    else:
                        preview = result_str.split('\n')[0][:80]
                    log(f"{name} → {preview}", "tool")

    if in_reasoning:
        print(flush=True)
    if content:
        print(flush=True)

    log("Agent 完成", "ok")

    return {"content": content, "framework": "agno"}


def run_agent(new_traj_md: str, new_traj_meta: dict, config: dict, log: StreamLog) -> dict:
    """用 agno Agent 执行"""
    return run_agent_agno(new_traj_md, new_traj_meta, config, log)


# ═══════════════════════════════════════════════════════════════════
# 核心流程: 处理单条轨迹
# ═══════════════════════════════════════════════════════════════════

def process_traj(traj_md_path: str, config: dict, dry_run: bool = False) -> dict:
    """处理一条轨迹的完整流程"""
    log = StreamLog(verbose=True)
    traj_path = Path(traj_md_path)

    print(f"\n{'═'*55}")
    print(f"  traj2skill: {traj_path.name}")
    print(f"{'═'*55}")

    # ── 1. 读取轨迹 ──
    log(f"读取轨迹: {traj_path}", "step")
    if not traj_path.exists():
        log(f"文件不存在: {traj_path}", "error")
        return {"action": "error", "error": "file not found"}

    traj_md = traj_path.read_text(encoding="utf-8")

    # 读 meta (json 或 .md.meta)
    traj_meta = {}
    meta_candidates = [
        traj_path.with_suffix(".json"),
        traj_path.parent / f"{traj_path.name}.meta",
    ]
    for mc in meta_candidates:
        if mc.exists():
            traj_meta = json.loads(mc.read_text(encoding="utf-8"))
            break
    log(f"Meta: success={traj_meta.get('success')}, tools={traj_meta.get('tool_names', [])}", "step")

    # ── 2. 初始化上下文 ──
    llm = create_llm_client(config)
    embed = create_embed_client(config)
    init_context(SKILL_DIR, DATA_DIR, llm, embed, config)

    # ── 3. 锁 ──
    ensure_repo(str(SKILL_DIR))
    traj_name = traj_path.stem  # traj_0042

    if dry_run:
        log("🏜  Dry-run 模式, 跳过锁", "git")
    else:
        log(f"获取锁: {traj_name}", "git")
        if not acquire_lock(str(SKILL_DIR), traj_name, timeout=60):
            return {"action": "error", "error": "lock timeout"}

    try:
        # ── 4. 运行 Agent ──
        log("启动 Agent", "step")
        agent_result = run_agent(traj_md, traj_meta, config, log)

        if dry_run:
            log("🏜  Dry-run 完成", "ok")
            log.save(ROOT / "output" / f"dryrun_{traj_name}.log.json")
            return {"action": "dry_run", "agent_result": agent_result}

        # ── 5. 检查变更 ──
        if not has_changes(str(SKILL_DIR)):
            log("Agent 决定: 不需要修改 skill", "decision")
            release_lock(str(SKILL_DIR))
            return {"action": "skip", "reason": "no changes"}

        # ── 6. 找到被修改/创建的 skill，区分实质变更 vs 仅 abstract 更新 ──
        from modules.git_lock import run_git
        # -u 展开未 track 目录内的文件
        _, status_out, _ = run_git(["status", "--porcelain", "-u"], cwd=str(SKILL_DIR))
        changed_skills = set()
        has_skill_md_change = False
        for line in status_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            fpath = line[3:].strip().split(" -> ")[-1]
            path_parts = fpath.split("/")
            if path_parts[0] and not path_parts[0].startswith("."):
                changed_skills.add(path_parts[0])
                if "skill.md" in fpath:
                    has_skill_md_change = True

        # ── 7. Commit ──
        log("提交变更", "git")
        committed = commit_changes(str(SKILL_DIR), f"skill: auto from {traj_name}")
        if not committed:
            log("无实际变更可提交", "git")
            release_lock(str(SKILL_DIR))
            return {"action": "skip", "reason": "nothing to commit"}

        log(f"变更的 skill: {changed_skills}", "decision")

        # 如果只是更新了 .abstract（没有 skill.md 新建/修改），跳过 eval
        if not has_skill_md_change:
            log("仅更新 abstract，跳过 eval", "decision")
            release_lock(str(SKILL_DIR))
            return {"action": "updated_abstract", "skills": list(changed_skills)}

        # ── 8. Eval ──
        eval_results = {}
        for skill_name in changed_skills:
            skill_path = SKILL_DIR / skill_name
            if not skill_path.is_dir():
                continue

            log(f"评估 skill: {skill_name}", "eval")

            if llm:
                result = run_eval(skill_path, llm, n_runs=3, log_fn=log)
            else:
                result = {"tier": "none", "eval_score": 7.0, "note": "no llm, auto pass"}
            eval_results[skill_name] = result
            log(f"eval_score: {result.get('eval_score', 0)}", "eval")

        # ── 9. 结果判断 ──
        all_pass = True
        for skill_name, er in eval_results.items():
            if not should_merge(er, is_new=True):
                all_pass = False
                log(f"❌ {skill_name} 未通过 eval (score={er.get('eval_score', 0)})", "eval")

        if all_pass and eval_results:
            # ── 10. 合入后：生成摘要 + 重建索引 ──
            for skill_name in changed_skills:
                log(f"生成摘要: {skill_name}", "step")
                abstract_result = update_abstract(skill_name, source_trajs=[traj_name])
                # 写入 eval 结果
                abstract_path = SKILL_DIR / skill_name / ".abstract"
                if abstract_path.exists():
                    abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
                    abstract["eval_result"] = eval_results.get(skill_name, {})
                    abstract_path.write_text(json.dumps(abstract, ensure_ascii=False, indent=2), encoding="utf-8")
            commit_changes(str(SKILL_DIR), f"skill: abstract + eval for {', '.join(changed_skills)}")
            log("重建索引", "step")
            rebuild_skill_index()
            log("✅ 完成", "ok")
            release_lock(str(SKILL_DIR))
            return {"action": "merged", "skills": list(changed_skills), "eval": eval_results}
        else:
            log("未通过 eval", "eval")
            # eval 不通过 → revert 这次的 commit
            run_git(["revert", "--no-edit", "HEAD"], cwd=str(SKILL_DIR))
            release_lock(str(SKILL_DIR))
            return {"action": "rejected", "skills": list(changed_skills), "eval": eval_results}

    except Exception as e:
        log(f"异常: {e}", "error")
        import traceback
        traceback.print_exc()
        if not dry_run:
            release_lock(str(SKILL_DIR))
        return {"action": "error", "error": str(e)}

    finally:
        # 确保释放锁
        if not dry_run:
            release_lock(str(SKILL_DIR))

        # 保存执行日志
        out_dir = ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        log.save(out_dir / f"t2s_{traj_path.stem}_{datetime.now().strftime('%H%M%S')}.log.json")


# ═══════════════════════════════════════════════════════════════════
# CLI 命令
# ═══════════════════════════════════════════════════════════════════

def cmd_process(args, config):
    result = process_traj(args.traj, config, dry_run=args.dry_run)
    print(f"\n  结果: {json.dumps(result, ensure_ascii=False, default=str)[:500]}")
    return 0 if result.get("action") != "error" else 1


def cmd_batch(args, config):
    dataset_dir = DATA_DIR / args.dataset
    if not dataset_dir.is_dir():
        print(f"❌ 数据集不存在: {dataset_dir}")
        return 1

    md_files = sorted(dataset_dir.glob("traj_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(".meta")]
    if args.max:
        md_files = md_files[:args.max]

    print(f"批量处理: {len(md_files)} 条轨迹 from {args.dataset}")

    results = {"merged": 0, "rejected": 0, "skipped": 0, "errors": 0}
    for i, md_file in enumerate(md_files):
        print(f"\n{'━'*55}")
        print(f"  [{i+1}/{len(md_files)}] {md_file.name}")
        print(f"{'━'*55}")
        result = process_traj(str(md_file), config, dry_run=args.dry_run)
        action = result.get("action", "error")
        results[action] = results.get(action, 0) + 1

    print(f"\n{'═'*55}")
    print(f"  批量处理完成")
    print(f"  合入: {results.get('merged', 0)}  拒绝: {results.get('rejected', 0)}  "
          f"跳过: {results.get('skipped', 0) + results.get('skip', 0)}  "
          f"错误: {results.get('errors', 0) + results.get('error', 0)}")
    print(f"{'═'*55}")
    return 0


def cmd_init(args, config):
    ensure_repo(str(SKILL_DIR))
    print(f"✅ skill 仓库已初始化: {SKILL_DIR}")
    return 0


def cmd_reindex(args, config):
    llm = create_llm_client(config)
    embed = create_embed_client(config)
    init_context(SKILL_DIR, DATA_DIR, llm, embed, config)
    rebuild_skill_index()
    print("✅ skill 索引已重建")
    return 0


def cmd_status(args, config):
    print(f"\n  skill 目录: {SKILL_DIR}")
    if not SKILL_DIR.exists():
        print("  状态: 未初始化 (运行 traj2skill.py init)")
        return 0

    branch = current_branch(str(SKILL_DIR))
    print(f"  git 分支:   {branch} {'🔒 locked' if branch != 'main' else '✅ free'}")

    skills = [d for d in SKILL_DIR.iterdir()
              if d.is_dir() and not d.name.startswith(".")]
    print(f"  skill 数量: {len(skills)}")
    for d in sorted(skills):
        ap = d / ".abstract"
        if ap.exists():
            abstract = json.loads(ap.read_text(encoding="utf-8"))
            score = abstract.get("eval_result", {}).get("eval_score", "?")
            ver = abstract.get("version", "?")
            print(f"    📁 {d.name}  v{ver}  score={score}  "
                  f"tags={abstract.get('tags', [])}")
        else:
            print(f"    📁 {d.name}  (no .abstract)")

    index_path = SKILL_DIR / ".skill_index.pkl"
    print(f"  向量索引:   {'✅ exists' if index_path.exists() else '❌ missing'}")
    return 0


def cmd_eval(args, config):
    skill_path = SKILL_DIR / args.skill
    if not skill_path.is_dir():
        print(f"❌ skill 不存在: {skill_path}")
        return 1

    llm = create_llm_client(config)
    if not llm:
        print("❌ 需要 LLM 配置来做 eval")
        return 1

    log = StreamLog()
    result = run_eval(skill_path, llm, n_runs=args.n_runs, log_fn=log)
    print(f"\n  结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    return 0


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="traj2skill — 从轨迹自动蒸馏 Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # process
    p_proc = sub.add_parser("process", help="处理单条轨迹")
    p_proc.add_argument("--traj", required=True, help="轨迹 .md 文件路径")
    p_proc.add_argument("--dry-run", action="store_true", help="只看 agent 决策，不实际修改")

    # batch
    p_batch = sub.add_parser("batch", help="批量处理数据集")
    p_batch.add_argument("--dataset", required=True, help="数据集目录名")
    p_batch.add_argument("--max", type=int, default=None, help="最多处理条数")
    p_batch.add_argument("--dry-run", action="store_true")

    # init
    sub.add_parser("init", help="初始化 skill git 仓库")

    # reindex
    sub.add_parser("reindex", help="重建 skill 向量索引")

    # status
    sub.add_parser("status", help="查看 skill 仓库状态")

    # eval
    p_eval = sub.add_parser("eval", help="手动触发 skill eval")
    p_eval.add_argument("--skill", required=True, help="skill 名称")
    p_eval.add_argument("--n-runs", type=int, default=3, help="LLM 打分次数")

    # 全局参数
    parser.add_argument("--llm-base-url", help="覆盖 LLM base_url")
    parser.add_argument("--llm-model", help="覆盖 LLM model")
    parser.add_argument("--llm-key", help="覆盖 LLM api_key")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    config = load_config()

    # CLI 覆盖
    if args.llm_base_url:
        config.setdefault("llm", {})["base_url"] = args.llm_base_url
    if args.llm_model:
        config.setdefault("llm", {})["model"] = args.llm_model
    if args.llm_key:
        config.setdefault("llm", {})["api_key"] = args.llm_key

    cmd_map = {
        "process": cmd_process,
        "batch": cmd_batch,
        "init": cmd_init,
        "reindex": cmd_reindex,
        "status": cmd_status,
        "eval": cmd_eval,
    }

    return cmd_map[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main() or 0)
