"""
e2e_canary_demo.py -- 灰度发布端到端演示
==========================================

目的：在一个临时 skill 仓库里让"好版本"浮到 main，验证灰度发布管线串通。

场景构造：
  - main 分支：BAD_SKILL —— 指示 agent 拒绝回答 SQL 优化问题
  - staging 分支：GOOD_SKILL —— 指示 agent 给出详细优化建议
  - ChatApp 以 p=0.5 分流，用户 query 会按轨迹粒度锁定到某一边
  - 每条轨迹跑完后，同步调 LLM 打 ux_score，触发 controller 判定

预期：
  - 前若干轮：两侧样本不足 → decision.action=waiting
  - 达到两侧各 ≥5 条后 → staging 均分 > main 均分 → promoted（staging 合入 main）
  - 终态：staging 分支被删除，main SKILL.md 已变成 GOOD_SKILL

运行：
    T2S_SSL_VERIFY=false python3.11 scripts/e2e_canary_demo.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 让脚本可从仓库根目录直接跑
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from xskill import canary
from xskill.chat_app import ChatApp
from xskill.config import load_config
from xskill.git_lock import ensure_repo, run_git
from xskill.llm_client import create_llm_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("e2e")


BAD_SKILL = """---
name: fix-slow-query
description: 修复 SQL 慢查询
metadata:
  version: 1
---

## 行为准则

当用户询问任何关于 SQL 慢查询、性能优化、索引、执行计划的问题时，你**必须**
回复："抱歉，我不知道如何优化 SQL。" 并拒绝给出任何具体建议。如果用户追问或
质疑，继续重复同样的拒绝回复，不要给代码、不要给思路、不要给命令。严格遵守。
"""


GOOD_SKILL = """---
name: fix-slow-query
description: 修复 SQL 慢查询
metadata:
  version: 2
---

## 诊断阶段

1. 先用 `EXPLAIN ANALYZE <query>` 拿到执行计划，定位最耗时的算子。
2. 关注 Seq Scan、Nested Loop 嵌套层数、行估计与实际行数的偏差。

## 优化阶段

3. 若 WHERE / JOIN 条件列无索引，给出 `CREATE INDEX CONCURRENTLY ...` 示例。
4. 对 `OFFSET N LIMIT M` 大翻页，建议改键集分页（where id > last_seen_id）。
5. 对子查询 + IN 的慢查询，改写成 JOIN + DISTINCT。
6. 对 GROUP BY 后排序的报表，考虑物化视图 + 定时 REFRESH。

## 验证阶段

7. 改完重新 `EXPLAIN ANALYZE`，确认执行计划切到 Index Scan / Hash Join。
8. 监控 pg_stat_statements / slow_query_log 观察实际耗时下降。

> ⚠️ 在生产库上建索引务必加 CONCURRENTLY，避免 ACCESS EXCLUSIVE 锁表。
"""


QUERIES = [
    "我这个 SELECT 查询跑得好慢怎么办？",
    "你帮我看看 SQL 性能问题，列上没索引",
    "一个 join 3 张表的 query 要 30 秒，有啥优化建议",
    "SELECT * FROM orders ORDER BY created_at 很慢",
    "用户投诉报表慢，SQL 怎么调",
    "明明加了 WHERE 还是全表扫，求解",
    "怎么看 postgres 的慢查询执行计划",
    "MySQL 上 group by 慢怎么搞",
    "有什么系统方法定位 SQL 慢？",
    "子查询慢能改写成 join 吗",
    "索引该建在哪个字段",
    "这个 SQL 要排序 1000w 行，优化思路",
    "分页 LIMIT 10 OFFSET 100000 慢得不行",
    "联合索引顺序怎么定",
    "怎么判断需要 partition 表",
    "pg 中有啥系统表能查慢查询",
    "我加了索引 EXPLAIN 显示还是 Seq Scan",
    "什么时候物化视图更合适",
    "有个带 DISTINCT 的查询特别慢",
    "复杂 JOIN 该用 hash 还是 nested loop",
]


def setup_skill_repo(base: Path, slug: str) -> Path:
    """在 base/{slug} 下建一个 git 仓库，main=BAD，staging=GOOD。"""
    sd = base / slug
    sd.mkdir(parents=True, exist_ok=True)
    ensure_repo(str(sd))

    # 先写 BAD_SKILL 到 main
    (sd / "SKILL.md").write_text(BAD_SKILL, encoding="utf-8")
    run_git(["add", "-A"], cwd=str(sd))
    run_git(["commit", "-m", "main: bad baseline skill"], cwd=str(sd))

    initial_sha = canary.main_sha(sd)

    # 再覆盖为 GOOD_SKILL + commit（这个 commit 会被挪到 staging）
    (sd / "SKILL.md").write_text(GOOD_SKILL, encoding="utf-8")
    run_git(["add", "-A"], cwd=str(sd))
    run_git(["commit", "-m", "candidate: good skill"], cwd=str(sd))

    routed = canary.route_main_history_to_staging(sd, initial_sha)
    assert routed, "route_main_history_to_staging failed"
    assert canary.has_staging(sd), "staging branch missing"

    main_body = canary.read_skill_on_branch(sd, "main") or ""
    staging_body = canary.read_skill_on_branch(sd, "staging") or ""
    assert "拒绝" in main_body or "我不知道" in main_body, "main 不是 BAD_SKILL"
    assert "EXPLAIN ANALYZE" in staging_body, "staging 不是 GOOD_SKILL"
    log.info(f"setup OK: main=BAD, staging=GOOD at {sd}")
    return sd


def main():
    os.environ.setdefault("T2S_SSL_VERIFY", "false")
    config = load_config(REPO / "config.yaml")
    llm = create_llm_client(config, role="skill")
    if llm is None:
        print("[FATAL] LLM 未配置", file=sys.stderr)
        sys.exit(1)

    tmp = Path(tempfile.mkdtemp(prefix="xskill_canary_e2e_"))
    log.info(f"tempdir: {tmp}")

    skill_dir = tmp / "skill"
    skill_dir.mkdir()
    traj_dir = tmp / "trajectory"
    traj_dir.mkdir()

    slug = "fix-slow-query"
    sd = setup_skill_repo(skill_dir, slug)

    canary_cfg = canary.CanaryConfig(
        probability=0.5,
        min_samples=5,
        max_days_hold=14,
    )

    app = ChatApp(
        skill_dir=skill_dir,
        traj_dir=traj_dir,
        llm=llm,
        skill_slug=slug,
        canary_config=canary_cfg,
        async_score=False,  # 同步：便于顺序观察
    )

    terminal = None
    for i, q in enumerate(QUERIES, 1):
        log.info(f"--- round {i}: {q[:40]}...")
        res = app.handle(q)
        ux = res["ux_score"]
        score = ux.get("score")
        decision = ux.get("decision", {})
        print(
            f"[round {i:02d}] side={res['side']:7s} sha={res['commit_sha']} "
            f"score={score} decision={decision.get('action')} "
            f"main_n={decision.get('main_samples','-')} "
            f"staging_n={decision.get('staging_samples','-')} "
            f"avg: main={decision.get('main_avg','-')} staging={decision.get('staging_avg','-')}"
        )
        if decision.get("action") in ("promoted", "rejected", "timeout_discarded"):
            terminal = decision
            log.info(f"terminal decision at round {i}: {decision}")
            break

    # -- 终态检查 --
    print("\n============ 终态 ============")
    print(f"has_staging: {canary.has_staging(sd)}")
    main_body_final = canary.read_skill_on_branch(sd, "main") or ""
    is_good = "EXPLAIN ANALYZE" in main_body_final
    print(f"main 现在是 GOOD_SKILL? {is_good}")

    all_scores = canary.load_ux_scores(sd)
    ms = [s["score"] for s in all_scores if s["side"] == "main"]
    ss = [s["score"] for s in all_scores if s["side"] == "staging"]
    print(f"ux_scores: main_n={len(ms)} avg={sum(ms)/len(ms) if ms else 0:.2f}; "
          f"staging_n={len(ss)} avg={sum(ss)/len(ss) if ss else 0:.2f}")

    print("\n详细打分（按 scored_at 顺序）：")
    for s in all_scores:
        print(f"  {s['scored_at']} {s['side']:7s} score={s['score']} "
              f"traj={s['traj_id']} reasons={s['reasons'][:50]!r}")

    print("\ntempdir 保留供人工检查:", tmp)

    if terminal is None:
        print("\n[RESULT] 未达成终态（可能 LLM 反应不稳定或样本分布偏斜）", file=sys.stderr)
        sys.exit(2)
    if terminal.get("action") != "promoted":
        print(f"\n[RESULT] 终态是 {terminal.get('action')}，不是 promoted", file=sys.stderr)
        sys.exit(3)
    if not is_good:
        print("\n[RESULT] staging 合入后 main 未切换到 GOOD_SKILL", file=sys.stderr)
        sys.exit(4)

    print("\n[PASS] 灰度合入成功：好的 skill 浮现到 main")


if __name__ == "__main__":
    main()
