"""dashboard/gitgraph.py —— skill 进化图（图①）：commit DAG + 裁决标注。

dulwich 直读 skill 子仓：main / staging / refs/rejected/* 三类 ref 的可达
commit 合并成一张图，裁决（canary_decision）按 sha 挂到节点；存量无 sha 的
历史裁决进 ``unlocated_decisions``，显式标"无法定位"（D9），不做时间戳模糊匹配。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from xskill.pipeline.registry import pooled_connection

_MAX_COMMITS = 200


def skill_commit_graph(skill_dir: Path, name: str,
                       db_path: Optional[Path] = None) -> dict:
    """返回 ``{nodes, decisions_unlocated, heads}``。

    node: ``{sha, parents[], subject, ts, lanes[], is_head_main, is_head_staging,
    decision(=该 sha 作为 staging_sha 的裁决 action|None), rejected_ref(bool)}``
    lanes ⊆ {main, staging, rejected}——一个 commit 可能同时可达于多条 ref
    （晋升合入后的 staging commit 也在 main 历史里）。
    """
    from dulwich.repo import Repo
    sub = Path(skill_dir) / name
    if not (sub / ".git").is_dir():
        raise KeyError(f"skill repo not found: {name}")
    repo = Repo(str(sub))
    try:
        refs = repo.refs.as_dict()
        tips: dict[str, list[bytes]] = {"main": [], "staging": [], "rejected": []}
        for ref, sha in refs.items():
            rname = ref.decode("utf-8")
            if rname == "refs/heads/main":
                tips["main"].append(sha)
            elif rname == "refs/heads/staging":
                tips["staging"].append(sha)
            elif rname.startswith("refs/rejected/"):
                tips["rejected"].append(sha)

        def reachable(start: list[bytes]) -> set[bytes]:
            seen: set[bytes] = set()
            stack = list(start)
            while stack and len(seen) < _MAX_COMMITS * 4:
                sha = stack.pop()
                if sha in seen:
                    continue
                seen.add(sha)
                try:
                    stack.extend(repo.object_store[sha].parents)
                except KeyError:
                    continue
            return seen

        lane_sets = {lane: reachable(t) for lane, t in tips.items()}
        all_shas = set().union(*lane_sets.values())
        nodes = []
        for sha in all_shas:
            try:
                c = repo.object_store[sha]
            except KeyError:
                continue
            lanes = [lane for lane, s in lane_sets.items() if sha in s]
            nodes.append({
                "sha": sha.decode("ascii"),
                "parents": [p.decode("ascii") for p in c.parents],
                "subject": c.message.decode("utf-8", "replace")
                            .splitlines()[0][:120] if c.message else "",
                "ts": int(c.commit_time),
                "lanes": lanes,
            })
        nodes.sort(key=lambda n: -n["ts"])
        nodes = nodes[:_MAX_COMMITS]
        head_main = (tips["main"][0].decode("ascii") if tips["main"] else None)
        head_staging = (tips["staging"][0].decode("ascii")
                        if tips["staging"] else None)
    finally:
        repo.close()

    decisions_by_sha: dict[str, dict] = {}
    unlocated: list[dict] = []
    with pooled_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts, action, main_avg, staging_avg, staging_sha, main_sha"
            " FROM canary_decision WHERE skill=? ORDER BY ts", (name,)
        ).fetchall()
    node_shas = {n["sha"] for n in nodes}
    for r in rows:
        d = {"ts": r["ts"], "action": r["action"],
             "main_avg": r["main_avg"], "staging_avg": r["staging_avg"],
             "staging_sha": r["staging_sha"] or "",
             "main_sha": r["main_sha"] or ""}
        if d["staging_sha"] and d["staging_sha"] in node_shas:
            decisions_by_sha[d["staging_sha"]] = d
        else:
            unlocated.append(d)  # 存量无 sha / 节点越界：显式列出，不猜
    for n in nodes:
        dec = decisions_by_sha.get(n["sha"])
        n["decision"] = dec["action"] if dec else None
        n["decision_detail"] = dec
        n["is_head_main"] = n["sha"] == head_main
        n["is_head_staging"] = n["sha"] == head_staging
    return {
        "skill": name,
        "nodes": nodes,
        "heads": {"main": head_main, "staging": head_staging},
        "decisions_unlocated": unlocated,
    }
