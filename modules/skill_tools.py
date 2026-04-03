"""
skill_tools.py — Skill Agent 的工具函数
══════════════════════════════════════════
用 agno @tool 装饰器注册，给 skill 生成 agent 使用。
"""

import json, os, re, pickle, logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("skill_tools")

# 全局上下文 — 在 traj2skill.py 中初始化
_ctx = {
    "skill_dir": None,     # Path: ./skill
    "data_dir": None,      # Path: ./data
    "llm_client": None,    # LLMClient
    "embed_client": None,  # EmbedClient
    "config": {},
}


def init_context(skill_dir, data_dir, llm_client, embed_client, config):
    _ctx["skill_dir"] = Path(skill_dir)
    _ctx["data_dir"] = Path(data_dir)
    _ctx["llm_client"] = llm_client
    _ctx["embed_client"] = embed_client
    _ctx["config"] = config


# ═══════════════════════════════════════════════════════════════════
# 检索类工具
# ═══════════════════════════════════════════════════════════════════

def search_similar_trajs(query: str, top_k: int = 5, filter: str = "all") -> str:
    """
    检索相似的历史轨迹。

    Args:
        query: 自然语言查询，描述你要找的轨迹类型
        top_k: 返回结果数量，默认 5
        filter: 过滤条件 "all" | "success" | "failure"

    Returns:
        JSON 字符串，包含匹配的轨迹列表，每条有 traj_id, similarity, meta 摘要, md 文件路径
    """
    from search import search as do_search
    data_dir = _ctx["data_dir"]
    config = _ctx["config"]

    results = []
    # 在所有数据集上搜索
    for d in sorted(data_dir.iterdir()):
        if not d.is_dir() or d.name == "raw":
            continue
        index_path = d / "index.pkl"
        if not index_path.exists():
            continue
        try:
            hits = do_search(d, query, top_k=top_k, min_similarity=0.1,
                            success_filter=filter, config=config)
            for h in hits:
                h["dataset"] = d.name
                h.pop("traj_json", None)  # 太大了不传
            results.extend(hits)
        except Exception as e:
            logger.warning(f"搜索 {d.name} 失败: {e}")

    # 全局排序取 top_k
    results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    results = results[:top_k]

    return json.dumps(results, ensure_ascii=False, indent=2, default=str)


def search_skills(query: str, top_k: int = 5) -> str:
    """
    检索已有的 skill。通过 ./skill/*/.abstract 的向量索引检索。

    Args:
        query: 自然语言查询，描述你要找的 skill 类型
        top_k: 返回数量

    Returns:
        JSON 字符串，包含匹配的 skill 列表，每条有 skill_name, similarity, abstract 摘要
    """
    skill_dir = _ctx["skill_dir"]
    index_path = skill_dir / ".skill_index.pkl"

    if not index_path.exists():
        return json.dumps({"results": [], "message": "skill 索引为空，尚无已有 skill"})

    with open(index_path, "rb") as f:
        index_data = pickle.load(f)

    embed_client = _ctx["embed_client"]
    embeddings = index_data["embeddings"]
    skill_names = index_data["skill_names"]
    # 编码查询
    query_emb = embed_client.encode(query)
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    similarities = embeddings @ query_emb
    ranked = sorted(enumerate(similarities), key=lambda x: x[1], reverse=True)

    results = []
    for idx, sim in ranked[:top_k]:
        name = skill_names[idx]
        abstract_path = skill_dir / name / ".abstract"
        abstract = {}
        if abstract_path.exists():
            abstract = json.loads(abstract_path.read_text(encoding="utf-8"))

        results.append({
            "skill_name": name,
            "similarity": round(float(sim), 4),
            "summary": abstract.get("summary", ""),
            "trigger": abstract.get("trigger", ""),
            "tags": abstract.get("tags", []),
            "version": abstract.get("version", 0),
        })

    return json.dumps(results, ensure_ascii=False, indent=2)


def read_file(path: str) -> str:
    """
    读取文件内容。可以读取轨迹 md、skill md、任意项目文件。

    Args:
        path: 文件路径 (相对于项目根目录或绝对路径)

    Returns:
        文件内容字符串
    """
    p = Path(path)
    # 安全检查: 只允许读取项目目录下的文件
    root = _ctx["skill_dir"].parent
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return f"错误: 不允许读取项目目录外的文件 ({path})"

    if not p.exists():
        return f"错误: 文件不存在 ({path})"

    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > 10000:
            return content[:10000] + f"\n\n... (截断, 原文 {len(content)} 字符)"
        return content
    except Exception as e:
        return f"错误: 读取失败 ({e})"


# ═══════════════════════════════════════════════════════════════════
# 写入类工具
# ═══════════════════════════════════════════════════════════════════

def create_skill(skill_name: str) -> str:
    """
    创建新的 skill 目录。

    Args:
        skill_name: skill 名称 (英文, 下划线分隔, 如 "fix_orm_n_plus_one")

    Returns:
        创建结果和路径
    """
    skill_dir = _ctx["skill_dir"]
    target = skill_dir / skill_name

    if target.exists():
        return f"skill 目录已存在: {target}，请使用 write_file 直接修改"

    target.mkdir(parents=True)
    logger.info(f"📁 创建 skill 目录: {target}")
    return f"已创建: {target}\n请接下来用 write_file 写入 skill.md"


def write_file(path: str, content: str) -> str:
    """
    写入或修改文件。只允许写入 ./skill/ 目录下的文件。

    Args:
        path: 文件路径 (相对于项目根目录)
        content: 文件内容

    Returns:
        写入结果
    """
    p = Path(path)
    skill_dir = _ctx["skill_dir"]

    # 安全检查: 只允许写 skill 目录
    try:
        p.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return f"错误: 只允许写入 ./skill/ 目录下的文件 (尝试写入: {path})"

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    logger.info(f"✏️  写入: {p} ({len(content)} bytes)")
    return f"已写入: {p} ({len(content)} 字符)"


ABSTRACT_PROMPT = """分析以下 skill 目录的内容，生成一个结构化的摘要。

## skill 目录中的文件
{file_listing}

## skill.md 内容
{skill_md}

请严格按以下 JSON 格式返回，不要输出其他内容:
```json
{{
  "summary": "一句话概括 (30字以内)",
  "trigger": "适用场景描述 (1~2句)",
  "subgoals": ["子目标1", "子目标2", ...],
  "tags": ["tag1", "tag2", "tag3"],
  "tools_used": ["tool1", "tool2"]
}}
```"""


def update_abstract(skill_name: str, source_trajs: list[str] = None) -> str:
    """
    重新生成 skill 的 .abstract 文件 (调用 LLM 总结)。

    Args:
        skill_name: skill 名称
        source_trajs: 关联的轨迹 ID 列表 (如 ["traj_0023", "traj_0045"])

    Returns:
        生成的 abstract 内容
    """
    skill_dir = _ctx["skill_dir"]
    llm = _ctx["llm_client"]
    target = skill_dir / skill_name

    if not target.exists():
        return f"错误: skill 目录不存在 ({target})"

    # 列出文件
    files = []
    for f in sorted(target.rglob("*")):
        if f.is_file() and f.name != ".abstract":
            files.append(str(f.relative_to(target)))

    # 读 skill.md
    skill_md_path = target / "skill.md"
    skill_md = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else "(skill.md 不存在)"

    # 读旧 abstract 拿 version
    abstract_path = target / ".abstract"
    old_version = 0
    old_eval = {}
    if abstract_path.exists():
        old = json.loads(abstract_path.read_text(encoding="utf-8"))
        old_version = old.get("version", 0)
        old_eval = old.get("eval_result", {})

    # 调 LLM
    prompt = ABSTRACT_PROMPT.format(
        file_listing="\n".join(f"- {f}" for f in files),
        skill_md=skill_md[:4000],
    )

    max_retries = 3
    abstract_data = None
    for attempt in range(max_retries):
        try:
            resp = llm.chat(prompt)
            # 提取 JSON
            json_match = re.search(r'\{.*\}', resp, re.DOTALL)
            if json_match:
                abstract_data = json.loads(json_match.group())
                break
        except Exception as e:
            logger.warning(f".abstract 生成 attempt {attempt+1} 失败: {e}")

    if not abstract_data:
        return "错误: LLM 生成 .abstract 失败 (已重试 3 次)"

    # 补充字段
    abstract_data["skill_name"] = skill_name
    abstract_data["files"] = files
    abstract_data["version"] = old_version + 1
    abstract_data["source_trajs"] = source_trajs or []
    abstract_data["eval_result"] = old_eval  # 保留旧 eval, 新 eval 在 commit 后跑
    from datetime import datetime
    abstract_data["last_updated"] = datetime.now().isoformat()

    abstract_path.write_text(json.dumps(abstract_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"📋 .abstract 已更新: {abstract_path} (v{abstract_data['version']})")

    return json.dumps(abstract_data, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
# Skill 索引重建
# ═══════════════════════════════════════════════════════════════════

def rebuild_skill_index():
    """重建 ./skill/.skill_index.pkl"""
    skill_dir = _ctx["skill_dir"]
    embed_client = _ctx["embed_client"]

    abstracts = []
    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        ap = d / ".abstract"
        if ap.exists():
            data = json.loads(ap.read_text(encoding="utf-8"))
            text = " | ".join(filter(None, [
                f"summary: {data.get('summary', '')}",
                f"trigger: {data.get('trigger', '')}",
                f"tags: {', '.join(data.get('tags', []))}",
                f"subgoals: {' → '.join(data.get('subgoals', []))}",
            ]))
            abstracts.append((d.name, text, data))

    if not abstracts:
        logger.info("无 skill 可索引")
        return

    names, texts, _ = zip(*abstracts)

    embeddings = embed_client.encode_batch(list(texts))
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embeddings = embeddings / norms

    index_data = {
        "skill_names": list(names),
        "texts": list(texts),
        "embeddings": embeddings,
        "method": "api",
    }

    index_path = skill_dir / ".skill_index.pkl"
    with open(index_path, "wb") as f:
        pickle.dump(index_data, f)

    logger.info(f"🔄 skill 索引重建: {len(names)} 条 → {index_path}")
