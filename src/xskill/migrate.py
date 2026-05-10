"""
migrate.py -- Legacy skill/ -> v2 SKILL.md migrator
====================================================
Converts old `skill.md` + `.abstract` pairs into the v2 open-standard layout
(SKILL.md with YAML frontmatter, warnings inlined as `> ⚠️` blockquotes).
"""

from __future__ import annotations

import json, re, logging
from datetime import date
from pathlib import Path

from xskill.frontmatter import serialize as fm_serialize
from xskill.git_lock import commit_changes

logger = logging.getLogger("migrate")


def is_legacy_skill(skill_path: Path) -> bool:
    """True if directory has old-format files and no SKILL.md yet."""
    return (
        skill_path.is_dir()
        and (skill_path / "skill.md").exists()
        and not (skill_path / "SKILL.md").exists()
    )


def _rename_legacy_eval_scores(eval_result: dict) -> dict:
    """Rename v1 score keys (trigger_precision, pitfall_quality) to v2 names
    (description_precision, warning_coverage) inside an eval_result dict.

    Works on both the top-level ``scores`` dict and each entry of ``all_runs``.
    Returns a new dict; the input is not mutated.
    """
    if not isinstance(eval_result, dict):
        return eval_result

    # Local import avoids a circular dependency with skill_eval at module load.
    try:
        from xskill.skill_eval import LEGACY_ALIASES
    except Exception:
        LEGACY_ALIASES = {
            "trigger_precision": "description_precision",
            "pitfall_quality": "warning_coverage",
        }

    def _rename(d: dict) -> dict:
        out = {}
        for k, v in d.items():
            nk = LEGACY_ALIASES.get(k, k)
            # new name wins if both present
            if nk in out and k in LEGACY_ALIASES:
                continue
            out[nk] = v
        return out

    new = dict(eval_result)
    if isinstance(new.get("scores"), dict):
        new["scores"] = _rename(new["scores"])
    if isinstance(new.get("all_runs"), list):
        new["all_runs"] = [
            _rename(r) if isinstance(r, dict) else r for r in new["all_runs"]
        ]
    return new


def _split_sections(body: str) -> dict[str, str]:
    """Split markdown by top-level `## <title>` headers. Returns {title: body}."""
    sections: dict[str, str] = {}
    current = "_preamble"
    current_lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            sections[current] = "\n".join(current_lines).strip()
            current = m.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current] = "\n".join(current_lines).strip()
    return sections


def _expand_description(trigger_text: str, summary: str, llm) -> str:
    """Optionally expand a terse trigger into a router-ready description via LLM.
    Falls back to a concatenation if no LLM is available."""
    base = (trigger_text or summary or "").strip()
    if not llm or not base:
        return base

    prompt = (
        "Rewrite the following skill trigger/summary as a 2-5 sentence "
        "'description' for an agent-skill router. Include likely user phrasings "
        "in quotes and name required tools or permissions. Output plain text "
        "only, no preamble.\n\n"
        f"Trigger: {trigger_text}\nSummary: {summary}"
    )
    try:
        out = llm.chat(prompt).strip()
        return out or base
    except Exception as e:
        logger.warning(f"LLM description expansion failed: {e}")
        return base


def _inline_pitfalls(steps_body: str, pitfalls_body: str) -> str:
    """Fold each pitfall bullet into a `> ⚠️` blockquote.

    Policy (documented fallback): we do NOT try to heuristically map each
    pitfall to a specific step — attribution requires semantic context we
    don't have here. Instead, ALL pitfalls are appended as a single block
    at the end of the steps section, each rendered as a `> ⚠️` blockquote.
    The agent will re-distribute warnings inline next time it updates the
    skill; humans can also re-thread them manually.
    """
    if not pitfalls_body.strip():
        return steps_body.rstrip()

    # Split pitfalls into items: accept "- ", "* ", or numbered list markers,
    # otherwise treat each non-empty paragraph as an item.
    items: list[str] = []
    buf: list[str] = []
    for line in pitfalls_body.splitlines():
        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            if buf:
                items.append(" ".join(s.strip() for s in buf).strip())
                buf = []
            items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).rstrip())
        elif line.strip() == "":
            if buf:
                items.append(" ".join(s.strip() for s in buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        items.append(" ".join(s.strip() for s in buf).strip())

    warnings = "\n\n".join(f"> ⚠️ {item}" for item in items if item)
    return steps_body.rstrip() + "\n\n## Known pitfalls (migrated)\n\n" + warnings + "\n"


def migrate_skill(skill_path: Path, llm=None) -> dict:
    """Migrate one legacy skill directory in-place.

    Returns:
        {"ok": bool, "name": str, "skill_md_path": str, "note": str}
    """
    name = skill_path.name
    if not is_legacy_skill(skill_path):
        return {"ok": False, "name": name, "note": "not a legacy skill"}

    skill_md = skill_path / "skill.md"
    abstract_path = skill_path / ".abstract"

    old_md = skill_md.read_text(encoding="utf-8")
    abstract = {}
    if abstract_path.exists():
        try:
            abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
        except Exception:
            abstract = {}

    sections = _split_sections(old_md)
    preamble = sections.get("_preamble", "").strip()
    trigger_body = sections.get("trigger", "").strip()
    steps_body = sections.get("steps", "").strip()
    pitfalls_body = sections.get("pitfalls", "").strip()

    trigger_text = abstract.get("trigger") or trigger_body
    summary = abstract.get("summary", "")

    description = _expand_description(trigger_text, summary, llm)
    compatibility = abstract.get("compatibility") or (
        "Migrated from v1 format; original skill did not declare compatibility. "
        "Review before production use."
    )

    today = date.today().isoformat()
    metadata = {
        "version": int(abstract.get("version", 1) or 1),
        "created": abstract.get("created", today),
        "last_updated": today,
        "source_trajs": abstract.get("source_trajs", []) or [],
        "frozen": bool(abstract.get("frozen", False)),
        "use_count": int(abstract.get("use_count", 0) or 0),
    }
    if summary:
        metadata["summary"] = summary
    if abstract.get("tags"):
        metadata["tags"] = abstract["tags"]
    if abstract.get("eval_result"):
        metadata["eval"] = _rename_legacy_eval_scores(abstract["eval_result"])

    fm = {
        "name": name.replace("_", "-").lower(),
        "description": description,
        "compatibility": compatibility,
        "metadata": metadata,
    }

    # Body composition: preamble (if any) + steps + inlined pitfalls
    new_body_parts = []
    title = name.replace("_", " ").replace("-", " ").capitalize()
    new_body_parts.append(f"# {title}")
    if preamble:
        new_body_parts.append(preamble)
    if steps_body:
        # preserve the old section title for readability
        new_body_parts.append("## Steps\n\n" + steps_body)
    elif pitfalls_body:
        new_body_parts.append("## Steps\n\n(no steps recorded in legacy skill.md)")

    body = "\n\n".join(new_body_parts) + "\n"
    body = _inline_pitfalls(body, pitfalls_body)

    new_text = fm_serialize(fm, body)
    (skill_path / "SKILL.md").write_text(new_text, encoding="utf-8")
    try:
        skill_md.unlink()
    except Exception:
        pass
    try:
        if abstract_path.exists():
            abstract_path.unlink()
    except Exception:
        pass

    return {"ok": True, "name": name, "skill_md_path": str(skill_path / "SKILL.md")}


def migrate_all(skill_dir: Path, llm=None, commit: bool = True) -> list[dict]:
    """Migrate every legacy skill dir under skill_dir."""
    results: list[dict] = []
    if not skill_dir.exists():
        return results

    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if is_legacy_skill(d):
            res = migrate_skill(d, llm=llm)
            results.append(res)
            if res.get("ok") and commit:
                commit_changes(str(skill_dir), f"migrate: v1→v2 format for {res['name']}")

    return results
