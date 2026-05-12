"""
skill_tools.py — Tools exposed to the Skill curation Agent
══════════════════════════════════════════════════════════════
Agno-compatible tool functions. All skill files follow the v2 schema
(SKILL.md with YAML frontmatter); no more separate .abstract file.
"""

import json, os, pickle, logging
from datetime import date, datetime
from pathlib import Path

import numpy as np

from xskill.frontmatter import parse as fm_parse, serialize as fm_serialize

logger = logging.getLogger("skill_tools")

# Global context — initialized by process.py / server.py / cli.py
_ctx = {
    "skill_dir": None,     # Path: ./skill
    "data_dir": None,      # Path: ./data
    "llm_client": None,    # LLMClient
    "embed_client": None,  # EmbedClient
    "config": {},
}

# v2 context for AtomTask-era tools (TaskClusterAgent / SkillEditAgent use these).
# Kept separate from _ctx so legacy callers (旧 SkillAgent) don't accidentally
# read stale fields during Task 3/4 transition.
_ctx_v2 = {
    "skill_dir": None,     # Path: <skill root>
    "store": None,         # AtomTaskStore
    "embed_client": None,  # EmbedClient
    "traj_root": None,     # Path: <traj root> e.g. ~/.xskill/cc_sessions
}


def init_context_v2(*, skill_dir, store, embed_client, traj_root):
    _ctx_v2["skill_dir"] = Path(skill_dir)
    _ctx_v2["store"] = store
    _ctx_v2["embed_client"] = embed_client
    _ctx_v2["traj_root"] = Path(traj_root)


def init_context(skill_dir, data_dir, llm_client, embed_client, config):
    _ctx["skill_dir"] = Path(skill_dir)
    _ctx["data_dir"] = Path(data_dir)
    _ctx["llm_client"] = llm_client
    _ctx["embed_client"] = embed_client
    _ctx["config"] = config


def _slugify(name: str) -> str:
    """Normalize a skill name to the slug form used in frontmatter.name."""
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def _sanitize_frontmatter_dates(fm: dict) -> dict:
    """不让 LLM 写的日期字段污染 frontmatter。
    - created: 必须是合法 ISO date 且 ≤ 今天；否则替换成今天（保留历史 created 优先）
    - last_updated: 一律覆盖成当前时间
    返回被修改过的 fm（同对象）。
    """
    meta = fm.setdefault("metadata", {})
    today = date.today()
    created = str(meta.get("created", "")).strip()
    valid_created = False
    try:
        parsed = date.fromisoformat(created[:10]) if created else None
        if parsed and parsed <= today:
            valid_created = True
    except (ValueError, TypeError):
        pass
    if not valid_created:
        meta["created"] = today.isoformat()
    meta["last_updated"] = datetime.now().isoformat(timespec="seconds")
    return fm


def _read_skill_md(skill_path: Path) -> tuple[dict, str, Path]:
    """Return (frontmatter_dict, body, path_of_SKILL.md). Supports legacy
    lowercase `skill.md` as a fallback read path (writes always go to
    SKILL.md)."""
    upper = skill_path / "SKILL.md"
    lower = skill_path / "skill.md"
    if upper.exists():
        fm, body = fm_parse(upper.read_text(encoding="utf-8"))
        return fm, body, upper
    if lower.exists():
        fm, body = fm_parse(lower.read_text(encoding="utf-8"))
        return fm, body, lower
    return {}, "", upper


# ═══════════════════════════════════════════════════════════════════
# Read tools
# ═══════════════════════════════════════════════════════════════════

def search_similar_trajs(query: str, top_k: int = 5, filter: str = "all") -> str:
    """
    Search historical trajectories for semantic matches.

    Args:
        query: natural-language description of the trajectory type you want
        top_k: number of results (default 5)
        filter: "all" | "success" | "failure"

    Returns:
        JSON string: list of {traj_id, similarity, meta (summary), md_path, dataset}
    """
    from xskill.search import search as do_search
    data_dir = _ctx["data_dir"]
    config = _ctx["config"]

    results = []
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
                h.pop("traj_json", None)
            results.extend(hits)
        except Exception as e:
            logger.warning(f"search failed on {d.name}: {e}")

    results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    results = results[:top_k]
    return json.dumps(results, ensure_ascii=False, indent=2, default=str)


def search_skills(query: str, top_k: int = 5) -> str:
    """
    Search existing skills via the frontmatter-based vector index.

    Returns:
        JSON list, each item: {skill_name, similarity, description, tags, version}
    """
    skill_dir = _ctx["skill_dir"]
    index_path = skill_dir / ".skill_index.pkl"

    if not index_path.exists():
        return json.dumps({"results": [], "message": "skill index empty"})

    with open(index_path, "rb") as f:
        index_data = pickle.load(f)

    embed_client = _ctx["embed_client"]
    embeddings = index_data["embeddings"]
    skill_names = index_data["skill_names"]
    query_emb = embed_client.encode(query)
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    similarities = embeddings @ query_emb
    ranked = sorted(enumerate(similarities), key=lambda x: x[1], reverse=True)

    results = []
    for idx, sim in ranked[:top_k]:
        name = skill_names[idx]
        skill_path = skill_dir / name
        fm, _body, _ = _read_skill_md(skill_path)
        meta = fm.get("metadata", {}) or {}
        results.append({
            "skill_name": name,
            "similarity": round(float(sim), 4),
            "description": (fm.get("description") or "").strip(),
            "tags": meta.get("tags", []),
            "version": meta.get("version", 0),
        })

    return json.dumps(results, ensure_ascii=False, indent=2)


def read_file(path: str) -> str:
    """Read an arbitrary file under the project root."""
    p = Path(path)
    root = _ctx["skill_dir"].parent
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return f"error: outside project root ({path})"

    if not p.exists():
        return f"error: file not found ({path})"

    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > 10000:
            return content[:10000] + f"\n\n... (truncated, full length {len(content)} chars)"
        return content
    except Exception as e:
        return f"error: read failed ({e})"


# ═══════════════════════════════════════════════════════════════════
# Write tools
# ═══════════════════════════════════════════════════════════════════

SKILL_MD_STUB = """---
name: {slug}
description: |
  (placeholder — the agent will fill this with a 2-5 sentence router-ready
  description including likely user phrasings and required tools.)
compatibility: |
  (placeholder — required environment, versions, and any NO-GO conditions.)
metadata:
  version: 1
  created: "{today}"
  last_updated: "{today}"
  source_trajs: []
  frozen: false
  use_count: 0
---

# {title}

(Write body here. Use `## <stage-name>` phase-based headers. Inline warnings as
`> ⚠️` blockquotes directly under the step that needs them, citing trajectory
evidence.)
"""


def create_skill(skill_name: str) -> str:
    """
    Scaffold a new skill directory in the v2 layout.

    Creates:
        ./skill/<name>/SKILL.md          (stub frontmatter + placeholder body)
        ./skill/<name>/scripts/.gitkeep
        ./skill/<name>/references/.gitkeep

    Args:
        skill_name: slug (lowercase dashes, e.g. "fix-orm-n-plus-one")

    Returns:
        Status message; the agent should overwrite SKILL.md via write_file.
    """
    skill_dir = _ctx["skill_dir"]
    slug = _slugify(skill_name)
    target = skill_dir / slug

    if target.exists():
        return f"skill directory already exists: {target}. Use write_file to overwrite SKILL.md."

    target.mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "references").mkdir()
    (target / "scripts" / ".gitkeep").write_text("", encoding="utf-8")
    (target / "references" / ".gitkeep").write_text("", encoding="utf-8")

    today = date.today().isoformat()
    title = slug.replace("-", " ").capitalize()
    skill_md = SKILL_MD_STUB.format(slug=slug, today=today, title=title)
    (target / "SKILL.md").write_text(skill_md, encoding="utf-8")

    logger.info(f"📁 created skill scaffold: {target}")
    return (f"created: {target}\n"
            f"files: SKILL.md (stub), scripts/.gitkeep, references/.gitkeep\n"
            f"Next: overwrite {target}/SKILL.md with your full v2 content via write_file.")


# ═══════════════════════════════════════════════════════════════════
# Candidate buffer tools (agent-facing)
# ═══════════════════════════════════════════════════════════════════

def add_candidate(skill_name: str, pattern: str, pattern_type: str,
                  traj_id: str, attach_to: str = "") -> str:
    """
    Add a proposed pattern to the skill's .candidates.yml buffer. If a
    fuzzy-matching pattern already exists, merges the traj_id into its
    supporters list (de-duplicated). Otherwise creates a new candidate.

    Args:
        skill_name: slug of the target skill (must already exist)
        pattern: the pattern text (concrete, evidence-style)
        pattern_type: one of "step" | "warning" | "decision_branch"
        traj_id: the trajectory id (e.g. "traj_0023") contributing this signal
        attach_to: SKILL.md stage-header section to attach to (for warnings
                   and branches). Empty means "end of body".

    Returns:
        Human-readable status including the current supporter count.
    """
    from xskill import candidates as C

    skill_dir = _ctx["skill_dir"]
    slug = _slugify(skill_name)
    target = skill_dir / slug
    if not target.exists():
        # try non-slug name as fallback
        target = skill_dir / skill_name
    if not target.exists():
        return f"error: skill directory not found ({skill_name})"

    ptype = (pattern_type or "step").strip().lower()
    if ptype not in ("step", "warning", "decision_branch"):
        return (f"error: pattern_type must be one of step|warning|decision_branch "
                f"(got '{pattern_type}')")

    data = C.load_candidates(target)
    data, was_new = C.add_or_merge(
        data, pattern, ptype, traj_id,
        attach_to=attach_to or None,
    )
    C.save_candidates(target, data)

    # Look up the current supporter count for this pattern to report back.
    count = 0
    promoted = False
    for c in data.get("candidates", []):
        if C._fuzzy_equal(c.get("pattern", ""), pattern):
            count = len(c.get("supporting_trajs", []) or [])
            promoted = bool(c.get("promoted"))
            break

    verb = "new candidate" if was_new else "merged into existing candidate"
    tail = " [already PROMOTED]" if promoted else ""
    return (f"{verb} for skill '{slug}': '{pattern[:80]}' "
            f"type={ptype} supporters={count}{tail}")


def list_candidates(skill_name: str) -> str:
    """
    List candidates in the skill's .candidates.yml buffer.

    Returns a compact human-readable listing; the agent should read this
    before calling ``add_candidate`` to avoid duplicate proposals.
    """
    from xskill import candidates as C

    skill_dir = _ctx["skill_dir"]
    slug = _slugify(skill_name)
    target = skill_dir / slug
    if not target.exists():
        target = skill_dir / skill_name
    if not target.exists():
        return f"error: skill directory not found ({skill_name})"

    data = C.load_candidates(target)
    cands = data.get("candidates", []) or []
    if not cands:
        return f"(no candidates in buffer for '{slug}')"

    lines = [f"candidates for '{slug}' ({len(cands)} total):"]
    for c in cands:
        tag = "[PROMOTED]" if c.get("promoted") else "[PENDING] "
        n = len(c.get("supporting_trajs", []) or [])
        lines.append(
            f"  {tag} ({n}) [{c.get('type','step')}] "
            f"{(c.get('pattern','') or '')[:100]}"
        )
    return "\n".join(lines)


def write_file(path: str, content: str) -> str:
    """Write or overwrite a file under ./skill/ only.

    v2 行为：只做路径安全 + frontmatter 日期消毒。旧 v1 ``source_trajs ≥ 3``
    gate 和 ``N/M 条轨迹`` warning 消毒已删——v2 用 ``source_atoms`` 引用 atom
    而非 traj，且质量保障靠 candidates buffer 累计 weightscore ≥ 10 的硬门槛，
    不需要 SKILL.md 写入端再卡一道。
    """
    p = Path(path)
    if _ctx_v2["skill_dir"] is not None:
        skill_dir = _ctx_v2["skill_dir"]
    else:
        skill_dir = _ctx["skill_dir"]

    try:
        p.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return f"error: writes restricted to ./skill/ (tried: {path})"

    p.parent.mkdir(parents=True, exist_ok=True)
    # 写 SKILL.md：消毒 frontmatter 日期（防止 LLM 写未来日期 / 不合法 ISO）
    if p.name == "SKILL.md":
        try:
            fm, body = fm_parse(content)
            _sanitize_frontmatter_dates(fm)
            content = fm_serialize(fm, body)
        except Exception as e:
            logger.warning(f"SKILL.md frontmatter 日期消毒失败，原样写入: {e}")
    p.write_text(content, encoding="utf-8")
    logger.info(f"✏️  wrote: {p} ({len(content)} bytes)")
    return f"wrote: {p} ({len(content)} chars)"


# ═══════════════════════════════════════════════════════════════════
# Frontmatter metadata update (post-eval bookkeeping)
# ═══════════════════════════════════════════════════════════════════

SUMMARY_PROMPT = """Summarize the following SKILL.md in exactly 2 sentences
(max 50 words total). Focus on what problem it solves and the core decision
point. No preamble. Output the 2 sentences only.

---
{skill_md}
---"""


def update_frontmatter_metadata(skill_name: str, source_trajs: list[str] | None = None) -> str:
    """
    Update frontmatter.metadata on a skill's SKILL.md:
      - bump version if source_trajs changed
      - union-append new source_trajs
      - set last_updated = now
      - refresh metadata.summary via LLM (for better vector embeddings)
      - delete any legacy .abstract file lying around

    Body is preserved byte-exact.

    Returns:
        JSON blob of the new metadata, or an error message.
    """
    skill_dir = _ctx["skill_dir"]
    llm = _ctx["llm_client"]
    slug = _slugify(skill_name)
    target = skill_dir / skill_name
    if not target.exists():
        # try slug variant
        target = skill_dir / slug
    if not target.exists():
        return f"error: skill directory not found ({skill_name})"

    fm, body, path = _read_skill_md(target)
    meta = fm.setdefault("metadata", {})

    # source_trajs union
    existing_trajs = list(meta.get("source_trajs") or [])
    new_trajs = list(source_trajs or [])
    changed_trajs = False
    for t in new_trajs:
        if t not in existing_trajs:
            existing_trajs.append(t)
            changed_trajs = True
    meta["source_trajs"] = existing_trajs

    # version bump only if source_trajs actually changed
    if changed_trajs:
        meta["version"] = int(meta.get("version", 0)) + 1

    _sanitize_frontmatter_dates(fm)  # 兜底：覆盖未来日期 / 不合法 created

    # LLM-generated 2-sentence summary (for embeddings)
    if llm:
        skill_text = (fm.get("description", "") + "\n\n" + body)[:4000]
        try:
            summary = llm.chat(SUMMARY_PROMPT.format(skill_md=skill_text)).strip()
            # keep short
            if summary:
                meta["summary"] = summary[:400]
        except Exception as e:
            logger.warning(f"summary generation failed for {skill_name}: {e}")

    # write back
    new_text = fm_serialize(fm, body)
    # Always land in SKILL.md (uppercase). If read came from legacy skill.md,
    # migrate-on-touch.
    upper = target / "SKILL.md"
    upper.write_text(new_text, encoding="utf-8")
    if path.name == "skill.md" and path.exists():
        try:
            path.unlink()
            logger.info(f"removed legacy {path}")
        except Exception:
            pass

    # delete legacy .abstract if present
    old_abstract = target / ".abstract"
    if old_abstract.exists():
        try:
            old_abstract.unlink()
            logger.info(f"removed legacy .abstract for {skill_name}")
        except Exception:
            pass

    logger.info(f"📋 frontmatter updated: {upper} (v{meta.get('version')})")
    return json.dumps(meta, ensure_ascii=False, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# Skill index rebuild
# ═══════════════════════════════════════════════════════════════════

def rebuild_skill_index():
    """Rebuild ./skill/.skill_index.pkl from frontmatter description+summary+tags."""
    skill_dir = _ctx["skill_dir"]
    embed_client = _ctx["embed_client"]

    entries = []
    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        fm, _body, _path = _read_skill_md(d)
        if not fm:
            continue
        meta = fm.get("metadata", {}) or {}
        description = (fm.get("description") or "").strip()
        summary = (meta.get("summary") or "").strip()
        tags = meta.get("tags", []) or []
        text = f"{description} | tags: {', '.join(tags)} | {summary}".strip()
        entries.append((d.name, text))

    if not entries:
        logger.info("no skills to index")
        return

    names, texts = zip(*entries)
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

    logger.info(f"🔄 skill index rebuilt: {len(names)} entries → {index_path}")


# ═══════════════════════════════════════════════════════════════════
# AtomTask-era tools (v2) — consumed by TaskClusterAgent / SkillEditAgent
# ═══════════════════════════════════════════════════════════════════

def atom_task_read(atom_id: str) -> str:
    """读一个 AtomTask 的完整 JSON。

    用于 cluster / edit agent 在决定归类前查看 atom 的 intent / summary /
    raw_segment / used_skills。
    """
    store = _ctx_v2["store"]
    if store is None:
        return "error: v2 context not initialized"
    try:
        return store.load(atom_id).to_json()
    except FileNotFoundError as e:
        return f"error: {e}"


def atom_task_search(query: str, top_k: int = 5) -> str:
    """混合检索（向量 ⊕ BM25 关键字）AtomTask；返回 JSON 命中列表。

    每条结果含 ``atom_id`` 和 ``sources``（命中通道）；不做 rerank。
    """
    from xskill.hybrid_search import HybridSearch
    store = _ctx_v2["store"]
    embed = _ctx_v2["embed_client"]
    if store is None or embed is None:
        return "error: v2 context not initialized"
    hs = HybridSearch(store, embed)
    hits = hs.search(query, top_k=top_k)
    return json.dumps(hits, ensure_ascii=False, indent=2)


def read_traj(traj_id: str, offset_start: int, offset_end: int) -> str:
    """按字符 offset 读 traj.md 片段。

    用法：agent 看了 atom 摘要后想确认细节时，传 atom 的 offset_start/end
    回来取原文。校验区间合法（``offset_end > offset_start`` 且区间在文件
    长度内），违反直接返回 error。
    """
    traj_root = _ctx_v2["traj_root"]
    if traj_root is None:
        return "error: v2 context not initialized"
    p = traj_root / f"{traj_id}.md"
    if not p.is_file():
        return f"error: traj file not found: {p}"
    if offset_end <= offset_start:
        return f"error: offset_end ({offset_end}) must be > offset_start ({offset_start})"
    text = p.read_text(encoding="utf-8")
    if offset_start < 0 or offset_end > len(text):
        return (
            f"error: offsets [{offset_start}..{offset_end}] outside file "
            f"length {len(text)}"
        )
    return text[offset_start:offset_end]


def new_skill_folder(skill_name: str) -> str:
    """创建一个空 skill 目录骨架（scripts/ + references/）。

    与 v1 ``create_skill`` 的差异：不预生成 SKILL.md 占位骨架——v2 流程下
    SkillEditAgent 在 candidates 攒到阈值后再自主写完整内容；中间态目录
    只有 ``.candidates.yml``。
    """
    skill_dir = _ctx_v2["skill_dir"]
    if skill_dir is None:
        return "error: v2 context not initialized"
    slug = _slugify(skill_name)
    target = skill_dir / slug
    if target.exists():
        return f"already exists: {target}"
    target.mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "references").mkdir()
    (target / "scripts" / ".gitkeep").write_text("", encoding="utf-8")
    (target / "references" / ".gitkeep").write_text("", encoding="utf-8")
    return f"created: {target}"


def skill_read(skill_name: str) -> str:
    """读 skill 的 SKILL.md；没有则返回 placeholder 提示。"""
    skill_dir = _ctx_v2["skill_dir"]
    if skill_dir is None:
        return "error: v2 context not initialized"
    slug = _slugify(skill_name)
    p = skill_dir / slug / "SKILL.md"
    if not p.is_file():
        return f"(skill {slug} has no SKILL.md yet — only candidates buffer)"
    return p.read_text(encoding="utf-8")


def add_task_to_skill(skill_name: str, atom_id: str, weightscore: int) -> str:
    """把一个 atom 作为贡献追加到该 skill 的 .candidates.yml。

    ``weightscore`` 必须 1-10。返回末尾追加 ``weightscore_total=<N>`` 让
    agent 看到累计进度——达到 ``ATOM_PROMOTION_THRESHOLD`` (=10) 就会被
    SkillEditAgent 拾起来生成/更新 SKILL.md。
    """
    from xskill import candidates as C
    skill_dir = _ctx_v2["skill_dir"]
    if skill_dir is None:
        return "error: v2 context not initialized"
    slug = _slugify(skill_name)
    target = skill_dir / slug
    if not target.is_dir():
        return f"error: skill {slug} not found; call new_skill_folder first"
    try:
        ws = int(weightscore)
    except (TypeError, ValueError):
        return f"error: weightscore must be int 1..10 (got {weightscore!r})"
    if not (1 <= ws <= 10):
        return f"error: weightscore must be 1..10 (got {ws})"
    data = C.load_candidates(target)
    data, was_new = C.add_atom_contribution(data, atom_id, ws)
    C.save_candidates(target, data)
    total = next(
        c["weightscore_total"] for c in data["candidates"] if c["atom_id"] == atom_id
    )
    verb = "new" if was_new else "merged"
    return f"{verb}: skill={slug} atom={atom_id} weightscore_total={total}"


def score_task(atom_id: str, score: int) -> str:
    """覆盖 AtomTask 的 ux_score（手动修正 / 灰度链路使用）。"""
    store = _ctx_v2["store"]
    if store is None:
        return "error: v2 context not initialized"
    try:
        sc = int(score)
    except (TypeError, ValueError):
        return f"error: score must be int 1..10 (got {score!r})"
    if not (1 <= sc <= 10):
        return f"error: score must be 1..10 (got {sc})"
    try:
        a = store.load(atom_id)
    except FileNotFoundError as e:
        return f"error: {e}"
    a.ux_score = sc
    store.save(a)
    return f"scored: {atom_id} → {sc}"


def add_task(
    atom_id: str, *, traj_id: str, offset_start: int, offset_end: int,
    intent: str, summary: str, tags: list, used_skills: list,
    ux_score: int | None = None,
) -> str:
    """手动创建一个 AtomTask（offline 脚本 / agent 合成 atom 用）。

    生产路径走 TaskAgent；这个工具是给需要补轨的 agent / 脚本兜底。
    """
    from xskill.atom_task import AtomTask
    store = _ctx_v2["store"]
    if store is None:
        return "error: v2 context not initialized"
    atom = AtomTask(
        atom_id=atom_id, traj_id=traj_id,
        offset_start=int(offset_start), offset_end=int(offset_end),
        intent=intent, summary=summary,
        tags=list(tags or []), used_skills=list(used_skills or []),
        ux_score=ux_score,
        pre_atom_id=None, post_atom_id=None,
        context_prefix="", raw_segment="",
    )
    store.save(atom)
    return f"added: {atom_id}"
