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

from traj2skill.frontmatter import parse as fm_parse, serialize as fm_serialize

logger = logging.getLogger("skill_tools")

# Global context — initialized by process.py / server.py / cli.py
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


import re as _re
_WARNING_FRACTION_RE = _re.compile(r"(\d+)\s*/\s*(\d+)\s*条(失败)?轨迹")


def _sanitize_warning_fractions(body: str, source_trajs_count: int) -> tuple[str, int]:
    """修掉 warning 里编造的 N/M 分子分母。

    LLM 经常写 "3/7 条轨迹表明..."，但 M 根本超过实际 source_trajs 数量 ——
    这是纯幻觉。规则：如果 M > source_trajs_count，或 N > M，就把整个 "N/M 条轨迹"
    替换成 "源轨迹"，让 warning 只剩"见 traj_XXXX"这种可核对的引用。

    返回 (新 body, 替换次数)。
    """
    count = 0

    def _replace(m: _re.Match) -> str:
        nonlocal count
        n, total = int(m.group(1)), int(m.group(2))
        fail_word = m.group(3) or ""
        # M 必须 ≤ 实际 source_trajs 总数；N 必须 ≤ M 且 ≥ 1
        if total > source_trajs_count or n > total or n < 1 or total < 1:
            count += 1
            return f"源{fail_word}轨迹中"
        return m.group(0)

    return _WARNING_FRACTION_RE.sub(_replace, body), count


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
    from traj2skill.search import search as do_search
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
    from traj2skill import candidates as C

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
    from traj2skill import candidates as C

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
    """Write or overwrite a file under ./skill/ only."""
    p = Path(path)
    skill_dir = _ctx["skill_dir"]

    try:
        p.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return f"error: writes restricted to ./skill/ (tried: {path})"

    p.parent.mkdir(parents=True, exist_ok=True)
    # 如果写的是 SKILL.md，强制消毒 frontmatter 日期 + warning 里编造的 N/M
    if p.name == "SKILL.md":
        try:
            fm, body = fm_parse(content)
            _sanitize_frontmatter_dates(fm)
            source_count = len((fm.get("metadata", {}) or {}).get("source_trajs") or [])
            body, frac_replaced = _sanitize_warning_fractions(body, source_count)
            if frac_replaced:
                logger.info(f"warning 里编造的 N/M 已替换 {frac_replaced} 处（source_trajs={source_count}）")
            content = fm_serialize(fm, body)
        except Exception as e:
            logger.warning(f"skill.md frontmatter 消毒失败，原样写入: {e}")
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


# Back-compat shim for any external caller that still imports update_abstract.
# Emits a deprecation warning once. Remove once callers are migrated.
def update_abstract(skill_name: str, source_trajs=None) -> str:
    logger.warning("update_abstract() is deprecated; use update_frontmatter_metadata()")
    return update_frontmatter_metadata(skill_name, source_trajs)


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
