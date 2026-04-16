#!/usr/bin/env python3
"""
search.py -- 轨迹检索
=======================
从已建好的索引中检索相似轨迹。

用法 (作为库):
  from traj2skill.search import search, search_for_pipeline

用法 (CLI):
  t2s search --dataset swe_smith_dataset --query "修复 Django ORM 的 N+1 查询问题"
  t2s search --dataset swe_smith_dataset --traj data/swe_smith_dataset/traj_0042.md
"""

import argparse, json, os, pickle, sys, logging
from pathlib import Path

import numpy as np

from traj2skill.llm_client import EmbedClient, create_embed_client
from traj2skill.index import meta_to_index_text
from traj2skill.config import get_traj_dir, get_config, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("search")


def load_index(dataset_dir: Path) -> dict:
    """加载向量索引"""
    index_path = dataset_dir / "index.pkl"
    if not index_path.exists():
        raise FileNotFoundError(f"索引不存在: {index_path}\n请先运行: t2s index --dataset {dataset_dir.name}")
    with open(index_path, "rb") as f:
        return pickle.load(f)


def load_meta(dataset_dir: Path, traj_id: str) -> dict:
    """加载某条轨迹的 meta"""
    meta_path = dataset_dir / f"{traj_id}.md.meta"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}


def encode_query(query_text: str, index_data: dict, dataset_dir: Path, config: dict = None) -> "np.ndarray":
    """将查询文本编码为向量，自动匹配索引的 method"""
    # 用 API embedding -- 从 config 重建 client 或用索引中记录的信息
    if config:
        embed = create_embed_client(config)
    else:
        embed = EmbedClient(
            base_url=index_data.get("embed_base_url", ""),
            model=index_data.get("embed_model", ""),
            api_key=os.environ.get("EMBED_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        )
    vec = embed.encode(query_text)
    # 归一化
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def search(
    dataset_dir: Path,
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    success_filter: str = "all",
    config: dict = None,
) -> list[dict]:
    """
    检索相似轨迹

    Returns:
        [{"traj_id": str, "similarity": float, "meta": dict, "json_path": Path}, ...]
    """
    index_data = load_index(dataset_dir)
    embeddings = index_data["embeddings"]
    traj_ids = index_data["traj_ids"]

    # 编码查询
    query_emb = encode_query(query_text, index_data, dataset_dir, config)

    # 计算相似度
    similarities = embeddings @ query_emb

    # 排序
    ranked = sorted(enumerate(similarities), key=lambda x: x[1], reverse=True)

    results = []
    for idx, sim in ranked:
        if len(results) >= top_k:
            break
        if sim < min_similarity:
            continue

        traj_id = traj_ids[idx]
        meta = load_meta(dataset_dir, traj_id)

        # success 过滤
        if success_filter == "success" and meta.get("success") is not True:
            continue
        elif success_filter == "failure" and meta.get("success") is not False:
            continue

        json_path = dataset_dir / f"{traj_id}.json"
        traj_json = {}
        if json_path.exists():
            traj_json = json.loads(json_path.read_text(encoding="utf-8"))

        results.append({
            "traj_id": traj_id,
            "similarity": round(float(sim), 4),
            "meta": meta,
            "traj_json": traj_json,
            "md_path": str(dataset_dir / f"{traj_id}.md"),
        })

    return results


def search_all(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.0,
    success_filter: str = "all",
    config: dict = None,
) -> list[dict]:
    """跨所有注册目录搜索，合并结果按相似度排序。

    遍历 registry 中每个含 index.pkl 的目录，分别调 search()，
    合并后按 similarity 降序截取 top_k。
    """
    from traj2skill.registry import all_index_paths

    merged: list[dict] = []
    paths = all_index_paths()
    if not paths:
        logger.warning("search_all: no registered directories with index.pkl")
        return []

    for dir_path in paths:
        try:
            results = search(
                dir_path,
                query_text,
                top_k=top_k,
                min_similarity=min_similarity,
                success_filter=success_filter,
                config=config,
            )
            for r in results:
                r["dataset_dir"] = str(dir_path)
            merged.extend(results)
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("search_all: skip %s: %s", dir_path, e)

    merged.sort(key=lambda x: x["similarity"], reverse=True)
    return merged[:top_k]


def search_for_pipeline(
    dataset_dir: Path,
    query_traj_md: str,
    query_traj_meta: dict,
    top_k_success: int = 3,
    top_k_failure: int = 2,
    min_similarity: float = 0.3,
    config: dict = None,
) -> dict:
    """
    pipeline 用的检索接口：同时返回相似成功和失败轨迹

    Args:
        query_traj_md: 新轨迹的 markdown
        query_traj_meta: 新轨迹的 meta (已经过 LLM 提取)

    Returns:
        {"success": [...], "failure": [...]}
    """
    index_data = load_index(dataset_dir)
    embeddings = index_data["embeddings"]
    traj_ids = index_data["traj_ids"]

    # 用 meta 文本做查询
    query_text = meta_to_index_text(query_traj_meta)
    query_emb = encode_query(query_text, index_data, dataset_dir, config)

    similarities = embeddings @ query_emb
    ranked = sorted(enumerate(similarities), key=lambda x: x[1], reverse=True)

    success_results = []
    failure_results = []

    for idx, sim in ranked:
        if sim < min_similarity:
            continue
        if len(success_results) >= top_k_success and len(failure_results) >= top_k_failure:
            break

        traj_id = traj_ids[idx]
        meta = load_meta(dataset_dir, traj_id)
        entry = {
            "traj_id": traj_id,
            "similarity": round(float(sim), 4),
            "meta": meta,
            "md_path": str(dataset_dir / f"{traj_id}.md"),
        }

        if meta.get("success") is True and len(success_results) < top_k_success:
            success_results.append(entry)
        elif meta.get("success") is False and len(failure_results) < top_k_failure:
            failure_results.append(entry)

    return {"success": success_results, "failure": failure_results}


# ===================================================================
# CLI
# ===================================================================

def print_results(results: list[dict]):
    """打印检索结果"""
    if not results:
        print("  (无匹配结果)")
        return

    for i, r in enumerate(results, 1):
        meta = r["meta"]
        sim = r["similarity"]
        traj_id = r["traj_id"]

        success_str = "[OK]" if meta.get("success") is True else "[FAIL]" if meta.get("success") is False else "[?]"
        summary = meta.get("summary", "")
        intent = meta.get("intent", "")
        tags = meta.get("tags", [])
        subgoals = meta.get("subgoals", [])

        print(f"\n  [{i}] {traj_id}  sim={sim:.3f}  {success_str}")
        if summary:
            print(f"      summary: {summary}")
        if intent:
            print(f"      intent: {intent}")
        if subgoals:
            print(f"      subgoals: {' -> '.join(subgoals[:4])}")
        if tags:
            print(f"      tags: {', '.join(tags)}")
        print(f"      file: {r['md_path']}")


def main(traj_dir: Path | None = None):
    """CLI 入口"""
    parser = argparse.ArgumentParser(description="T2S 轨迹检索")
    parser.add_argument("path", nargs="?", type=str, help="轨迹目录路径")
    parser.add_argument("--dataset", type=str, help="数据集目录名")
    parser.add_argument("--query", type=str, help="自然语言查询")
    parser.add_argument("--traj", type=str, help="用一条轨迹 .md 作为查询")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-sim", type=float, default=0.0)
    parser.add_argument("--filter", choices=["all", "success", "failure"], default="all")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    config = load_config()
    base_traj_dir = traj_dir or get_traj_dir()

    # 确定数据集目录
    if args.path:
        dataset_dir = Path(args.path)
    elif args.dataset:
        dataset_dir = base_traj_dir / args.dataset
    else:
        dataset_dir = base_traj_dir

    if not dataset_dir.is_dir():
        print(f"数据集不存在: {dataset_dir}")
        return 1

    # 确定查询文本
    if args.query:
        query_text = args.query
    elif args.traj:
        traj_path = Path(args.traj)
        if not traj_path.exists():
            print(f"轨迹文件不存在: {traj_path}")
            return 1
        # 如果有 .meta 用 meta 文本，否则用 md 原文
        meta_path = traj_path.parent / f"{traj_path.name}.meta"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            query_text = meta_to_index_text(meta)
        else:
            query_text = traj_path.read_text(encoding="utf-8")[:2000]
    else:
        parser.print_help()
        return 1

    print(f"Search: \"{query_text[:80]}{'...' if len(query_text)>80 else ''}\"")
    print(f"   dataset: {dataset_dir.name} | top_k={args.top_k} | filter={args.filter}")

    results = search(
        dataset_dir,
        query_text,
        top_k=args.top_k,
        min_similarity=args.min_sim,
        success_filter=args.filter,
        config=config,
    )

    if args.json:
        # JSON 输出 (去掉 traj_json 避免过大)
        clean = [{k: v for k, v in r.items() if k != "traj_json"} for r in results]
        print(json.dumps(clean, ensure_ascii=False, indent=2))
    else:
        print_results(results)
        print(f"\n  共 {len(results)} 条结果")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
