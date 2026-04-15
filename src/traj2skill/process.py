"""
process.py -- 核心流程: 处理单条轨迹
======================================
从 traj2skill.py 提取的 process_traj() 和 batch 相关逻辑。
"""

import json, logging, traceback
from pathlib import Path
from datetime import datetime

from traj2skill.config import get_skill_dir, get_traj_dir, get_output_dir, get_config
from traj2skill.log import StreamLog
from traj2skill.llm_client import create_llm_client, create_embed_client
from traj2skill.git_lock import (
    ensure_repo, acquire_lock, release_lock, commit_changes,
    has_changes, run_git,
)
from traj2skill.skill_eval import run_eval, should_merge
from traj2skill.skill_tools import (
    init_context, update_frontmatter_metadata, rebuild_skill_index,
)
from traj2skill.frontmatter import parse as fm_parse, serialize as fm_serialize
from traj2skill.agent import run_agent
from traj2skill.candidates import promote_ready_candidates

logger = logging.getLogger("traj2skill")


def process_traj(traj_md_path: str, config: dict, dry_run: bool = False,
                 skill_dir: Path | None = None, data_dir: Path | None = None,
                 log_fn=None) -> dict:
    """处理一条轨迹的完整流程。

    *log_fn*: optional callable ``(msg, tag)`` — when provided, every log
    call is also forwarded to it (so SSE endpoints can stream intermediate
    events, not just the final result).
    """
    log = StreamLog(verbose=True)
    if log_fn is not None:
        class _TeeLog(StreamLog):
            def __call__(self, msg: str, tag: str = "info"):
                super().__call__(msg, tag)
                try:
                    log_fn(msg, tag)
                except Exception:
                    pass
        log = _TeeLog(verbose=True)
    traj_path = Path(traj_md_path)
    skill_dir = skill_dir or get_skill_dir()
    data_dir = data_dir or get_traj_dir()
    output_dir = get_output_dir()

    print(f"\n{'='*55}")
    print(f"  traj2skill: {traj_path.name}")
    print(f"{'='*55}")

    # -- 1. 读取轨迹 --
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

    # -- 2. 初始化上下文 --
    # 注意：agent + eval 走 role="skill"（可能是大模型），
    # skill_tools 里的 search_skills / update_frontmatter_metadata 等
    # 也用这个 llm；若只想让 metadata summary 用 mini，可再细分。
    llm = create_llm_client(config, role="skill")
    embed = create_embed_client(config)
    init_context(skill_dir, data_dir, llm, embed, config)

    # -- 3. 锁 --
    ensure_repo(str(skill_dir))
    traj_name = traj_path.stem  # traj_0042

    if dry_run:
        log("Dry-run mode, skipping lock", "git")
    else:
        log(f"获取锁: {traj_name}", "git")
        if not acquire_lock(str(skill_dir), traj_name, timeout=60):
            return {"action": "error", "error": "lock timeout"}

    try:
        # -- 4. 运行 Agent --
        log("启动 Agent", "step")
        agent_result = run_agent(traj_md, traj_meta, config, log)

        if dry_run:
            log("Dry-run done", "ok")
            log.save(output_dir / f"dryrun_{traj_name}.log.json")
            return {"action": "dry_run", "agent_result": agent_result}

        # -- 4a. 共识性硬护栏（path B 新建校验）--
        # agent 应该只用多轨迹共识 pattern 建新 skill，单轨迹独有的 pattern
        # 走 add_candidate。此处扫描新建的 SKILL.md（git status 标为 untracked /
        # added）做最小检验：frontmatter.metadata.source_trajs 若 < 2 条 →
        # 违反路径 B 硬程序 → 清理该 skill 目录，避免污染 git。
        _, status_out, _ = run_git(["status", "--porcelain", "-u"], cwd=str(skill_dir))
        consensus_threshold = int(
            config.get("candidates", {}).get("min_source_trajs", 2)
        )
        violated = []
        for line in status_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            # "?? name/SKILL.md" 或 "A  name/SKILL.md" 都视为新建
            mark = line[:2].strip()
            fpath = line[3:].strip().split(" -> ")[-1]
            if "SKILL.md" not in fpath:
                continue
            if mark not in ("??", "A", "AM"):  # 只管新增/新文件
                continue
            skill_name = fpath.split("/")[0]
            if not skill_name or skill_name.startswith("."):
                continue
            skill_md_path = skill_dir / skill_name / "SKILL.md"
            if not skill_md_path.exists():
                continue
            try:
                fm, _body = fm_parse(skill_md_path.read_text(encoding="utf-8"))
                src = fm.get("metadata", {}).get("source_trajs", []) or []
                src = [s for s in src if s]  # drop empty
                if len(src) < consensus_threshold:
                    violated.append((skill_name, len(src)))
            except Exception as e:
                log(f"skip consensus check for {skill_name}: {e}", "error")

        if violated:
            for name, nsrc in violated:
                log(
                    f"path B 硬程序违规: 新建 {name} 只有 {nsrc} 条 source_traj "
                    f"(需 ≥{consensus_threshold})，清理该 skill 目录",
                    "error",
                )
                # 清理 agent 刚创建的违规目录
                sdir = skill_dir / name
                if sdir.exists():
                    import shutil
                    try:
                        shutil.rmtree(sdir)
                    except Exception as e:
                        log(f"  cleanup failed: {e}", "error")
            # 如果清理后没别的变更，整体判 insufficient-signal
            if not has_changes(str(skill_dir)):
                release_lock(str(skill_dir))
                return {
                    "action": "rejected",
                    "reason": "insufficient_consensus",
                    "violated": [{"skill": n, "source_trajs": c} for n, c in violated],
                }

        # -- 4b. Promote any candidates that reached threshold.
        # The agent may have called add_candidate on one or more skills; scan
        # every skill dir with a .candidates.yml and flush ripe ones into
        # SKILL.md BEFORE we commit, so everything lands in the same commit.
        threshold = int(config.get("candidates", {}).get("threshold", 3))
        promoted_total = 0
        for d in sorted(skill_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not (d / ".candidates.yml").exists():
                continue
            try:
                promoted = promote_ready_candidates(d, threshold=threshold)
            except Exception as e:
                log(f"promote failed for {d.name}: {e}", "error")
                continue
            if promoted:
                promoted_total += len(promoted)
                log(f"promoted {len(promoted)} candidates for {d.name}", "decision")
        if promoted_total:
            log(f"total promoted: {promoted_total}", "decision")

        # -- 5. 检查变更 --
        if not has_changes(str(skill_dir)):
            log("Agent 决定: 不需要修改 skill", "decision")
            release_lock(str(skill_dir))
            return {"action": "skip", "reason": "no changes"}

        # -- 6. 找到被修改/创建的 skill，区分实质变更 vs 仅 abstract 更新 --
        # -u 展开未 track 目录内的文件
        _, status_out, _ = run_git(["status", "--porcelain", "-u"], cwd=str(skill_dir))
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
                # v2 uses SKILL.md; still accept legacy skill.md for in-flight migrations
                if "SKILL.md" in fpath or "skill.md" in fpath:
                    has_skill_md_change = True

        # -- 7. Commit --
        log("提交变更", "git")
        committed = commit_changes(str(skill_dir), f"skill: auto from {traj_name}")
        if not committed:
            log("无实际变更可提交", "git")
            release_lock(str(skill_dir))
            return {"action": "skip", "reason": "nothing to commit"}

        log(f"变更的 skill: {changed_skills}", "decision")

        # 仅 metadata/其他文件改动（SKILL.md body 未变），跳过 eval
        if not has_skill_md_change:
            log("仅更新 metadata，跳过 eval", "decision")
            release_lock(str(skill_dir))
            return {"action": "updated_metadata", "skills": list(changed_skills)}

        # -- 8. Eval --
        eval_results = {}
        for skill_name in changed_skills:
            skill_path = skill_dir / skill_name
            if not skill_path.is_dir():
                continue

            log(f"评估 skill: {skill_name}", "eval")

            if llm:
                result = run_eval(skill_path, llm, n_runs=3, log_fn=log, config=config)
            else:
                result = {"tier": "none", "eval_score": 7.0, "note": "no llm, auto pass"}
            eval_results[skill_name] = result
            log(f"eval_score: {result.get('eval_score', 0)}", "eval")

        # -- 9. 结果判断 --
        all_pass = True
        for skill_name, er in eval_results.items():
            if not should_merge(er, is_new=True):
                all_pass = False
                log(f"{skill_name} 未通过 eval (score={er.get('eval_score', 0)})", "eval")

        if all_pass and eval_results:
            # -- 10. 合入后：写回 metadata (summary + eval) + 重建索引 --
            for skill_name in changed_skills:
                log(f"更新 frontmatter metadata: {skill_name}", "step")
                update_frontmatter_metadata(skill_name, source_trajs=[traj_name])

                # 写入 eval 结果到 frontmatter.metadata.eval
                skill_md_path = skill_dir / skill_name / "SKILL.md"
                if skill_md_path.exists():
                    text = skill_md_path.read_text(encoding="utf-8")
                    fm, body = fm_parse(text)
                    meta = fm.setdefault("metadata", {})
                    eval_entry = dict(eval_results.get(skill_name, {}) or {})
                    eval_entry["last_eval"] = datetime.now().isoformat(timespec="seconds")
                    meta["eval"] = eval_entry
                    skill_md_path.write_text(fm_serialize(fm, body), encoding="utf-8")

                # Remove any stale .abstract that might have been created before.
                legacy_abstract = skill_dir / skill_name / ".abstract"
                if legacy_abstract.exists():
                    try:
                        legacy_abstract.unlink()
                    except Exception:
                        pass

            commit_changes(str(skill_dir), f"skill: metadata + eval for {', '.join(changed_skills)}")
            log("重建索引", "step")
            rebuild_skill_index()
            log("完成", "ok")
            release_lock(str(skill_dir))
            return {"action": "merged", "skills": list(changed_skills), "eval": eval_results}
        else:
            log("未通过 eval", "eval")
            # eval 不通过 -> revert 这次的 commit
            run_git(["revert", "--no-edit", "HEAD"], cwd=str(skill_dir))
            release_lock(str(skill_dir))
            return {"action": "rejected", "skills": list(changed_skills), "eval": eval_results}

    except Exception as e:
        log(f"异常: {e}", "error")
        traceback.print_exc()
        if not dry_run:
            release_lock(str(skill_dir))
        return {"action": "error", "error": str(e)}

    finally:
        # 确保释放锁
        if not dry_run:
            release_lock(str(skill_dir))

        # 保存执行日志
        output_dir.mkdir(exist_ok=True)
        log.save(output_dir / f"t2s_{traj_path.stem}_{datetime.now().strftime('%H%M%S')}.log.json")
