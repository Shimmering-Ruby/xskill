#!/usr/bin/env python3
"""
index.py -- 轨迹索引构建（增量版）
===================================
流程（单轨迹粒度，支持断点续跑）:
  1. 扫描 traj_dir/traj_XXXX.md
  2. 增量提取 meta：跳过已有且合格的 .md.meta，不合格的重提
  3. 增量向量化：已在 index.pkl 中的跳过，只补新增
  4. 合并写回 index.pkl

用法 (作为库):
  from xskill.index import index_dataset, meta_to_index_text, parse_meta

用法 (CLI):
  xskill index /path/to/trajectories
  xskill index --dataset swe_smith_dataset
  xskill index --all --concurrency 20
"""

import argparse, json, os, re, sys, pickle, logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import numpy as np

from xskill.llm_client import LLMClient, EmbedClient, create_embed_client, create_llm_client
from xskill.config import get_traj_dir, get_config, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("index")


# ===================================================================
# LLM Meta 提取
# ===================================================================

EXTRACT_PROMPT = """你是一名资深 AI Agent 轨迹分析师。你将收到一条 agent 执行轨迹的 markdown 记录。
轨迹可能来自任何场景：代码修复、功能开发、系统运维、文档编写、数据分析、项目规划、文件管理、信息检索等。
你的任务是从中提取高密度结构化知识，用于后续的相似轨迹检索和经验复用。

━━ 输出要求 ━━
严格使用以下 XML 标签包裹输出，标签之外不得有任何文字。

必须产出的字段（required）：
  - <intent>
  - <summary>
  - <blockers>
  - <success>
  - <tags>

可选字段（optional）：
  - <shortest_path>：仅当轨迹**明确成功**，且你对"最短必要路径"有高置信度时才填写。
    若任务失败、中断、或你无法自信地还原最小步骤序列——请输出空标签 <shortest_path></shortest_path>，**不要编造**。

注意：不要输出 <subgoals> 标签。子目标将在多轨迹聚合阶段从多条轨迹的交集中派生，不在单轨迹提取环节产出。

<intent>
用户要完成的核心任务。写清楚"对什么对象做什么操作、期望达到什么效果"。
如涉及具体技术实体（模块名、文件名、服务名、工具名），必须包含。
不超过 40 字。
示例："修复 MoneyWidget.decompress 导致禁用字段表单验证失败的问题"
示例："在 Headless-Wan2GP 中为图像编辑任务新增 qwen_edit_model 参数选择"
示例："扫描 desloppify 代码库并给出优先级排序的重构建议"
</intent>

<summary>
对轨迹全过程的详细复盘。必须涵盖以下四点，每点一行，以序号开头：
1. 根因/背景：任务的核心原因或背景是什么？如果是 bug，精确到哪个文件哪个函数的什么逻辑问题；如果是需求，说明业务动机。
2. 关键步骤：agent 实际执行了哪些关键动作？按时间顺序列出（搜索了什么、读了什么文件、改了什么、运行了什么命令）。只写有信息量的步骤，跳过重复和无效操作。
3. 产出结果：最终交付了什么？改了哪些文件、输出了什么结论、生成了什么产物？如果任务未完成，说明停在了哪一步。
4. 验证情况：agent 如何确认结果正确？跑了测试/脚本/人工确认？还是没有验证就结束了？
不超过 250 字。要求信息密度高——能让一个不了解上下文的人看完后理解发生了什么、学到了什么。
</summary>

<blockers>
agent 执行过程中遇到的卡点、弯路和失败尝试。每条格式为"[类型] 具体描述 → 后果/浪费"。
类型从以下选取：
- 定位弯路：搜索了错误的关键词/文件，或在无关代码中浪费时间
- 理解偏差：误判了问题原因或需求含义，导致方向错误
- 操作失误：命令/参数写错、编辑了错误的位置、遗漏关键步骤
- 反复修改：同一处改了多次才正确，或产出被用户否定后返工
- 遗漏上下文：忽略了文档/注释/配置中的关键提示信息
- 环境障碍：依赖缺失、权限不足、网络问题等外部因素
如果整条轨迹执行顺畅没有明显卡点，只写一条：
<blocker>[无] 执行顺畅，未遇到明显障碍</blocker>
<blocker>[类型] 描述 → 后果</blocker>
</blockers>

<shortest_path>
【可选字段】仅在以下两个条件**同时满足**时才填写内容：
  (a) 轨迹最终成功（success=true），并且
  (b) 你对"完成此任务所需的最短必要步骤序列"有高置信度。
若轨迹失败、中途放弃、或你不确定最短路径——请输出空标签：<shortest_path></shortest_path>
绝对不要为了"把字段填上"而编造步骤，失败轨迹的 shortest_path 宁可为空也不要虚构。

填写时的要求：去掉所有探索、试错、验证步骤，只保留"最短必要路径"，格式为编号列表，每步一个具体操作，不超过 120 字。
示例（高置信度时）：
1. 打开 djmoney/forms/widgets.py
2. 将 decompress 方法中第 26 行的 return 语句移到条件判断之后
3. 提交修改
</shortest_path>

<success>true 或 false（agent 是否最终完成了用户的核心任务）</success>

<tags>
3-5 个语义标签，覆盖以下维度：
- 任务类型：bug_fix / feature_dev / refactor / devops / doc_write / data_analysis / file_ops / research / planning
- 技术领域：涉及的框架、语言、工具、平台（如 django, react, kubernetes, bash）
- 关键实体：核心涉及的模块/类/函数/服务名
标签使用小写下划线命名，避免过于宽泛（不要用 code 或 programming 这类无区分度的标签）。
<tag>标签</tag>
</tags>

── 轨迹 ──
{traj_md}
"""


def extract_meta_llm(traj_md: str, llm: LLMClient) -> str:
    """调用 LLM 提取结构化 meta，返回 XML 字符串"""
    prompt = EXTRACT_PROMPT.format(traj_md=traj_md[:8000])
    return llm.chat(prompt)


# ===================================================================
# Meta 解析 & 校验
# ===================================================================

def parse_meta(xml_text: str) -> dict:
    """从 LLM 返回的 XML 中解析结构化字段"""
    def _extract(tag: str, text: str) -> str:
        m = re.search(rf'<{tag}>(.*?)</{tag}>', text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _extract_list(tag: str, text: str) -> list[str]:
        return [m.strip() for m in re.findall(rf'<{tag}>(.*?)</{tag}>', text, re.DOTALL)]

    summary = _extract("summary", xml_text)
    intent = _extract("intent", xml_text)
    # shortest_path 为可选字段：缺失或空都合法，默认空串
    shortest_path = _extract("shortest_path", xml_text)  # _extract 已在缺失时返回 ""
    success_str = _extract("success", xml_text).lower()
    success = True if "true" in success_str else (False if "false" in success_str else None)
    blockers = _extract_list("blocker", xml_text)
    # subgoals 不再从单轨迹提取；保留 key 以便下游兼容，但始终为空列表
    # （即便上游 LLM 误输出了 <subgoal>，也统一丢弃，避免污染单轨迹索引）
    subgoals: list[str] = []
    tags = _extract_list("tag", xml_text)

    return {
        "intent": intent,
        "summary": summary,
        "blockers": blockers,
        "shortest_path": shortest_path,
        "subgoals": subgoals,
        "success": success,
        "tags": tags,
        "raw_xml": xml_text,
    }


def validate_meta(meta: dict) -> bool:
    """校验 meta 质量：intent、summary 必须非空且有实质内容，tags 至少 1 个。

    注意：subgoals 与 shortest_path 不参与校验。
      - subgoals 已从单轨迹提取中移除（将在多轨迹聚合阶段派生），始终为 []
      - shortest_path 为可选字段，失败轨迹或低置信度下为 ""
    """
    intent = meta.get("intent", "")
    summary = meta.get("summary", "")
    tags = meta.get("tags", [])
    if not intent or len(intent) < 5:
        return False
    if not summary or len(summary) < 20:
        return False
    if not tags:
        return False
    return True


def meta_to_index_text(meta: dict) -> str:
    """将 meta 组合成用于向量化的文本

    字段选取原则：主信号为 intent / summary / tags；
    shortest_path 仅在非空时纳入（单轨迹提取大多为空）；
    subgoals 不再纳入（始终为空，浪费 embedding 输入）。
    """
    parts = []
    if meta.get("intent"):
        parts.append(f"intent: {meta['intent']}")
    if meta.get("summary"):
        parts.append(f"summary: {meta['summary']}")
    if meta.get("blockers"):
        parts.append("blockers: " + "; ".join(meta["blockers"]))
    sp = meta.get("shortest_path") or ""
    if sp.strip():
        parts.append(f"shortest_path: {sp}")
    if meta.get("tags"):
        parts.append("tags: " + ", ".join(meta["tags"]))
    return " | ".join(parts)


# ===================================================================
# 规则 fallback (无 LLM 时)
# ===================================================================

def extract_meta_rule(traj_md: str, traj_json: dict) -> dict:
    """无 LLM 时的规则提取 fallback"""
    query = traj_json.get("query", "")
    tool_names = traj_json.get("tool_names", [])
    success = traj_json.get("success")
    category = traj_json.get("category", "")

    # subgoals 已从单轨迹提取中移除（Stage B：将在多轨迹聚合阶段派生）
    # 规则兜底也统一返回空列表，保持与 LLM 路径一致
    summary = query[:50] if query else "unknown task"
    tags = [category] if category else []
    tags.extend(tool_names[:3])

    return {
        "summary": summary,
        "intent": query[:20] if query else "",
        "subgoals": [],
        "shortest_path": "",
        "blockers": [],
        "success": success,
        "tags": list(dict.fromkeys(tags)),
        "raw_xml": "(rule-based, no LLM)",
    }


# ===================================================================
# 单轨迹 meta 处理（含校验）
# ===================================================================

def _process_one_meta(md_file: Path, llm: LLMClient | None) -> str:
    """提取并校验单条 meta，返回 traj 文件名"""
    traj_md = md_file.read_text(encoding="utf-8")
    json_path = md_file.with_suffix(".json")
    traj_json = {}
    if json_path.exists():
        traj_json = json.loads(json_path.read_text(encoding="utf-8"))

    if llm is not None:
        try:
            xml_text = extract_meta_llm(traj_md, llm)
            meta = parse_meta(xml_text)
            if not validate_meta(meta):
                logger.warning(f"meta 校验不通过 ({md_file.name})，fallback rule")
                meta = extract_meta_rule(traj_md, traj_json)
        except Exception as e:
            logger.warning(f"LLM 失败 ({md_file.name}): {e}, fallback rule")
            meta = extract_meta_rule(traj_md, traj_json)
    else:
        meta = extract_meta_rule(traj_md, traj_json)

    meta_path = md_file.parent / f"{md_file.name}.meta"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_file.name


# ===================================================================
# 增量向量索引
# ===================================================================

def load_existing_index(dataset_dir: Path) -> dict | None:
    """加载已有的 index.pkl，不存在返回 None"""
    index_path = dataset_dir / "index.pkl"
    if index_path.exists():
        with open(index_path, "rb") as f:
            return pickle.load(f)
    return None


def build_vector_index_incremental(dataset_dir: Path, embed_client, force: bool = False) -> Path:
    """增量构建向量索引：只对新增轨迹做 embedding，已有的保留"""
    from tqdm import tqdm

    meta_files = sorted(dataset_dir.glob("traj_*.md.meta"))
    if not meta_files:
        logger.warning(f"无 .md.meta 文件: {dataset_dir}")
        return None

    # 收集所有合格的 meta
    all_entries = {}  # traj_id -> index_text
    invalid_count = 0
    for mf in meta_files:
        meta = json.loads(mf.read_text(encoding="utf-8"))
        if not validate_meta(meta):
            invalid_count += 1
            logger.warning(f"跳过不合格 meta: {mf.name}")
            continue
        traj_id = mf.name.replace(".md.meta", "")
        all_entries[traj_id] = meta_to_index_text(meta)

    if not all_entries:
        logger.warning(f"无合格 meta 可索引: {dataset_dir}")
        return None

    if invalid_count:
        logger.info(f"跳过 {invalid_count} 条不合格 meta")

    # 加载已有索引
    existing = None if force else load_existing_index(dataset_dir)
    existing_map = {}  # traj_id -> embedding vector
    if existing is not None:
        for i, tid in enumerate(existing["traj_ids"]):
            existing_map[tid] = existing["embeddings"][i]
        logger.info(f"已有索引: {len(existing_map)} 条")

    # 区分：已有 / 需新增
    reuse_ids = []
    todo_ids = []
    for tid in all_entries:
        if tid in existing_map:
            reuse_ids.append(tid)
        else:
            todo_ids.append(tid)

    logger.info(f"向量化: 复用 {len(reuse_ids)}, 新增 {len(todo_ids)}")

    # 对新增的逐条 embed（带进度条，单条容错）
    new_embeddings = {}  # traj_id -> np.ndarray
    failed_ids = []
    if todo_ids:
        for tid in tqdm(todo_ids, desc="embedding", unit="条"):
            text = all_entries[tid]
            try:
                vec = embed_client.encode(text)
                new_embeddings[tid] = vec
            except Exception as e:
                logger.error(f"embedding 失败 ({tid}): {e}")
                failed_ids.append(tid)

    if failed_ids:
        logger.warning(f"{len(failed_ids)} 条 embedding 失败，已跳过: {failed_ids[:5]}...")

    # 合并：复用 + 新增
    final_ids = []
    final_texts = []
    final_vecs = []

    for tid in sorted(all_entries.keys()):
        if tid in existing_map:
            final_ids.append(tid)
            final_texts.append(all_entries[tid])
            final_vecs.append(existing_map[tid])
        elif tid in new_embeddings:
            final_ids.append(tid)
            final_texts.append(all_entries[tid])
            final_vecs.append(new_embeddings[tid])
        # else: failed, skip

    if not final_vecs:
        logger.warning("无可用向量，跳过索引保存")
        return None

    embeddings = np.stack(final_vecs)
    # L2 归一化
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embeddings = embeddings / norms

    # 保存索引
    index_path = dataset_dir / "index.pkl"
    index_data = {
        "traj_ids": final_ids,
        "texts": final_texts,
        "embeddings": embeddings,
        "method": "api",
        "dim": embed_client.dim,
        "embed_model": embed_client.model,
        "embed_base_url": embed_client.base_url,
        "created": datetime.now().isoformat(),
        "count": len(final_ids),
    }
    with open(index_path, "wb") as f:
        pickle.dump(index_data, f)

    logger.info(f"索引已保存: {index_path} (dim={embed_client.dim}, n={len(final_ids)})")
    return index_path


# ===================================================================
# 主流程
# ===================================================================

def index_dataset(dataset_dir: Path, llm: LLMClient | None,
                  embed_client, dry_run: bool = False, reindex: bool = False,
                  concurrency: int = 10):
    """对一个数据集执行完整的增量索引流程"""
    from tqdm import tqdm

    md_files = sorted(dataset_dir.glob("traj_*.md"))
    md_files = [f for f in md_files if not f.name.endswith(".meta")]
    if not md_files:
        logger.warning(f"无轨迹文件: {dataset_dir}")
        return

    use_llm = llm is not None

    print(f"\n{'---'*17}")
    print(f"  {dataset_dir.name}: {len(md_files)} 条轨迹")
    print(f"  meta提取: {'LLM (' + llm.model + ')' if use_llm else 'rule (no LLM)'}")
    print(f"  embedding: {embed_client}")
    print(f"  并发: {concurrency}")
    print(f"{'---'*17}")

    # -- Step 1: meta 提取 --
    # reindex=True:  强制全部重抽（删除所有现有 .meta，全量重提）
    # reindex=False: 增量（合格跳过 / 不合格重提 / 无 meta 新提）
    todo = []
    skipped = 0
    reextract = 0

    if reindex:
        # 强制全部重抽：删除所有现有 .meta
        for md_file in md_files:
            meta_path = md_file.parent / f"{md_file.name}.meta"
            if meta_path.exists():
                try:
                    meta_path.unlink()
                except Exception:
                    pass
            todo.append(md_file)
        print(f"  --reindex: 删除所有 {len(md_files)} 条旧 meta，全量重新提取")
    else:
        for md_file in md_files:
            meta_path = md_file.parent / f"{md_file.name}.meta"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if validate_meta(meta):
                        skipped += 1
                        continue
                    else:
                        meta_path.unlink()
                        todo.append(md_file)
                        reextract += 1
                except (json.JSONDecodeError, Exception):
                    meta_path.unlink()
                    todo.append(md_file)
                    reextract += 1
            else:
                todo.append(md_file)

        if reextract:
            print(f"  {reextract} 条 meta 不合格，将重新提取")

    if dry_run:
        for md_file in todo[:3]:
            traj_md = md_file.read_text(encoding="utf-8")
            prompt = EXTRACT_PROMPT.format(traj_md=traj_md[:3000])
            print(f"\n  [dry-run] {md_file.name}")
            print(f"  prompt 长度: {len(prompt)} 字符")
        if len(todo) > 3:
            print(f"  ... (省略剩余 {len(todo)-3} 条)")
        print(f"\n  [dry-run] 完成，未实际调用")
        return

    extracted = 0
    failed = 0
    if todo:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process_one_meta, f, llm): f for f in todo}
            with tqdm(total=len(todo), desc="meta提取", unit="条") as pbar:
                for future in as_completed(futures):
                    try:
                        future.result()
                        extracted += 1
                    except Exception as e:
                        failed += 1
                        md_file = futures[future]
                        logger.error(f"处理失败 ({md_file.name}): {e}")
                    pbar.update(1)

    print(f"  meta 提取: {extracted} 新增, {skipped} 已存在" +
          (f", {reextract} 重提" if reextract else "") +
          (f", {failed} 失败" if failed else ""))

    # -- Step 2: 增量构建向量索引 --
    index_path = build_vector_index_incremental(dataset_dir, embed_client, force=reindex)
    if index_path:
        print(f"  向量索引: {index_path}")


def main(traj_dir: Path | None = None):
    """CLI 入口，也可作为库函数调用（传入 traj_dir）"""
    parser = argparse.ArgumentParser(description="XSkill 轨迹索引构建（增量版）")
    parser.add_argument("path", nargs="?", type=str, help="轨迹目录路径")
    parser.add_argument("--dataset", type=str, help="数据集目录名 (在 traj_dir 下)")
    parser.add_argument("--all", action="store_true", help="索引所有数据集")
    parser.add_argument("--no-llm", action="store_true", help="不调 LLM，纯规则提取 meta")
    parser.add_argument("--dry-run", action="store_true", help="只看 prompt，不调 LLM")
    parser.add_argument("--reindex", action="store_true", help="强制全量重建向量索引")
    parser.add_argument("--concurrency", type=int, default=10, help="LLM 并发数 (默认 10)")
    parser.add_argument("--llm-base-url", help="覆盖 config 中的 llm.base_url")
    parser.add_argument("--llm-model", help="覆盖 config 中的 llm.model")
    parser.add_argument("--llm-key", help="覆盖 config 中的 llm.api_key")
    parser.add_argument("--embed-base-url", help="覆盖 config 中的 embedding.base_url")
    parser.add_argument("--embed-model", help="覆盖 config 中的 embedding.model")
    parser.add_argument("--embed-key", help="覆盖 config 中的 embedding.api_key")
    args = parser.parse_args()

    config = load_config()

    # CLI 覆盖
    if args.llm_base_url:
        config.setdefault("llm", {})["base_url"] = args.llm_base_url
    if args.llm_model:
        config.setdefault("llm", {})["model"] = args.llm_model
    if args.llm_key:
        config.setdefault("llm", {})["api_key"] = args.llm_key
    if args.embed_base_url:
        config.setdefault("embedding", {})["base_url"] = args.embed_base_url
    if args.embed_model:
        config.setdefault("embedding", {})["model"] = args.embed_model
    if args.embed_key:
        config.setdefault("embedding", {})["api_key"] = args.embed_key

    print("=" * 50)
    print("  XSkill 轨迹索引构建（增量版）")
    print("=" * 50)

    # 创建客户端
    llm = None if args.no_llm else create_llm_client(config)
    embed_client = create_embed_client(config)

    print(f"  LLM:   {llm or '(disabled)'}")
    print(f"  Embed: {embed_client}")

    # 确定基础轨迹目录
    base_traj_dir = traj_dir or get_traj_dir()

    # 收集数据集
    datasets = []
    if args.path:
        # 直接指定路径
        d = Path(args.path)
        if d.is_dir():
            datasets.append(d)
        else:
            print(f"目录不存在: {d}")
            return 1
    elif args.all:
        for d in sorted(base_traj_dir.iterdir()):
            if d.is_dir() and d.name != "raw" and not d.name.endswith("_bak") and list(d.glob("traj_*.md")):
                datasets.append(d)
    elif args.dataset:
        d = base_traj_dir / args.dataset
        if d.is_dir():
            datasets.append(d)
        else:
            print(f"数据集不存在: {d}")
            return 1
    else:
        # 如果 traj_dir 本身有轨迹文件，直接索引
        if list(base_traj_dir.glob("traj_*.md")):
            datasets.append(base_traj_dir)
        else:
            parser.print_help()
            return 1

    if not datasets:
        print("未找到任何数据集")
        return 1

    for dataset_dir in datasets:
        index_dataset(dataset_dir, llm, embed_client,
                      dry_run=args.dry_run, reindex=args.reindex,
                      concurrency=args.concurrency)

    print(f"\n{'='*50}")
    print("  完成!")
    print(f"{'='*50}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
