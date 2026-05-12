#!/usr/bin/env python3
"""
search.py -- AtomTask 检索（v2）
=================================

旧 traj-level 检索（index.pkl 顶层 + meta_to_index_text）已删；本模块只面向
AtomTask。``search`` / ``search_all`` 保留同名但返回 ``atom_id`` 命中。

CLI 用法：
  xskill search --dataset cc_sessions --query "django migration"

依赖：``xskill.atom_task.AtomTaskStore`` + ``xskill.hybrid_search.HybridSearch``。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from xskill.atom_task import AtomTaskStore
from xskill.config import get_traj_dir, load_config
from xskill.hybrid_search import HybridSearch
from xskill.llm_client import create_embed_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("search")


def search(
    dataset_dir: Path,
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    success_filter: str = "all",
    config: dict | None = None,
) -> list[dict]:
    """AtomTask 检索（HybridSearch union+dedup）。

    返回每条 ``{atom_id, sources, vector_similarity?, bm25_score?, md_path,
    traj_id, atom (load 的字段)}``。

    ``min_similarity`` 仅对向量分数生效；BM25 命中没有归一化分，不过滤。
    ``success_filter`` 在 atom 层无意义（atom 没有 success 字段），保留参数
    位避免上层 import 时 TypeError。
    """
    embed = create_embed_client(config or load_config())
    store = AtomTaskStore(root=Path(dataset_dir))
    hits = HybridSearch(store, embed).search(query_text, top_k=top_k)
    out: list[dict] = []
    for h in hits:
        if min_similarity > 0 and "vector_similarity" in h:
            if h["vector_similarity"] < min_similarity:
                continue
        try:
            atom = store.load(h["atom_id"])
        except FileNotFoundError:
            continue
        h["traj_id"] = atom.traj_id
        h["md_path"] = str(Path(dataset_dir) / f"{atom.traj_id}.md")
        h["intent"] = atom.intent
        h["summary"] = atom.summary
        out.append(h)
    return out


def search_all(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    success_filter: str = "all",
    config: dict | None = None,
) -> list[dict]:
    """跨所有注册目录搜索 atom，合并按向量相似度排序。

    遍历 ``Registry`` 中含 ``index.pkl`` 的每个 dir，分别调 ``search``，合并
    后按 ``vector_similarity`` 倒序截 ``top_k``。BM25-only 命中（无 vector
    分数）排在最后。
    """
    from xskill.registry import all_index_paths

    merged: list[dict] = []
    for p in all_index_paths():
        try:
            results = search(
                p, query_text,
                top_k=top_k, min_similarity=min_similarity,
                success_filter=success_filter, config=config,
            )
            for r in results:
                r["dataset_dir"] = str(p)
            merged.extend(results)
        except Exception as e:
            logger.warning("search_all: skip %s: %s", p, e)

    def _vec_score(d: dict) -> float:
        return d.get("vector_similarity", -1.0)

    merged.sort(key=_vec_score, reverse=True)
    return merged[:top_k]


# ===================================================================
# CLI
# ===================================================================

def print_results(results: list[dict]):
    if not results:
        print("  (无匹配结果)")
        return
    for i, r in enumerate(results, 1):
        sim = r.get("vector_similarity")
        bm25 = r.get("bm25_score")
        sources = ",".join(r.get("sources", []))
        sim_str = f"sim={sim:.3f}" if sim is not None else f"bm25={bm25:.3f}"
        print(f"\n  [{i}] {r['atom_id']}  {sim_str}  sources={sources}")
        if r.get("intent"):
            print(f"      intent:  {r['intent']}")
        if r.get("summary"):
            print(f"      summary: {r['summary']}")
        if r.get("md_path"):
            print(f"      traj:    {r['md_path']}")


def main(traj_dir: Path | None = None):
    parser = argparse.ArgumentParser(description="XSkill AtomTask 检索")
    parser.add_argument("path", nargs="?", type=str, help="轨迹目录路径")
    parser.add_argument("--dataset", type=str, help="数据集目录名")
    parser.add_argument("--query", type=str, help="自然语言查询")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-sim", type=float, default=0.0)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if not args.query:
        parser.error("--query 必填")
    config = load_config()
    base = traj_dir or get_traj_dir()
    if args.path:
        dataset_dir = Path(args.path)
    elif args.dataset:
        dataset_dir = base / args.dataset
    else:
        dataset_dir = base
    if not dataset_dir.is_dir():
        print(f"数据集不存在: {dataset_dir}")
        return 1

    print(f"Search: \"{args.query[:80]}\"  dataset: {dataset_dir.name}"
          f"  top_k={args.top_k}")
    results = search(dataset_dir, args.query, top_k=args.top_k,
                     min_similarity=args.min_sim, config=config)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_results(results)
        print(f"\n  共 {len(results)} 条结果")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
