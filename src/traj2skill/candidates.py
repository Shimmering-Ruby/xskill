"""
candidates.py -- Candidate buffer for the Skill curation pipeline
===================================================================
Manages ``.candidates.yml`` under each skill directory. Candidates are patterns
(step / warning / decision_branch) proposed by the agent after seeing a
trajectory, but NOT yet written into SKILL.md. They accumulate across
trajectories; when at least ``threshold`` distinct trajectories support the
same pattern, it gets *promoted* into SKILL.md body.

Design notes
------------
- Fuzzy match: two patterns are considered "the same candidate" when
  lowercase-stripped first 60 chars match, OR one is a lowercased substring
  of the other. This is intentionally conservative — the agent's own
  ``list_candidates`` tool gives it the final de-dup judgement.
- We refuse to touch SKILL.md frontmatter; promotion only inserts body
  content, preserving the Stage A schema exactly.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import yaml

from traj2skill.frontmatter import parse as fm_parse, serialize as fm_serialize

logger = logging.getLogger("candidates")

CANDIDATES_FILENAME = ".candidates.yml"
FUZZY_PREFIX = 60


# ═══════════════════════════════════════════════════════════════════
# I/O
# ═══════════════════════════════════════════════════════════════════

def _candidates_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / CANDIDATES_FILENAME


def load_candidates(skill_dir: Path) -> dict:
    """Read .candidates.yml or return a fresh empty structure."""
    p = _candidates_path(skill_dir)
    if not p.exists():
        return {"candidates": []}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"failed to parse {p}: {e}; starting empty")
        return {"candidates": []}
    if not isinstance(data, dict) or "candidates" not in data:
        return {"candidates": []}
    if data.get("candidates") is None:
        data["candidates"] = []
    return data


def save_candidates(skill_dir: Path, data: dict) -> None:
    p = _candidates_path(skill_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
# Fuzzy matching + merge
# ═══════════════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _fuzzy_equal(a: str, b: str) -> bool:
    a_n = _norm(a)
    b_n = _norm(b)
    if not a_n or not b_n:
        return False
    if a_n == b_n:
        return True
    # first-N prefix match
    if a_n[:FUZZY_PREFIX] == b_n[:FUZZY_PREFIX]:
        return True
    # substring (one contained in the other) — only when the shorter is
    # meaningfully long, to avoid collapsing unrelated short phrases.
    shorter, longer = (a_n, b_n) if len(a_n) <= len(b_n) else (b_n, a_n)
    if len(shorter) >= 20 and shorter in longer:
        return True
    return False


def add_or_merge(
    data: dict,
    pattern: str,
    pattern_type: str,
    traj_id: str,
    attach_to: str | None = None,
) -> tuple[dict, bool]:
    """Either merge ``traj_id`` into an existing fuzzy-matching candidate, or
    append a new one.

    Returns (data, was_new).
    """
    today = date.today().isoformat()
    candidates = data.setdefault("candidates", [])

    for cand in candidates:
        if _fuzzy_equal(cand.get("pattern", ""), pattern):
            supporters = cand.setdefault("supporting_trajs", [])
            if traj_id not in supporters:
                supporters.append(traj_id)
            cand["last_seen"] = today
            # backfill missing fields on legacy entries
            cand.setdefault("first_seen", today)
            cand.setdefault("type", pattern_type)
            if attach_to and not cand.get("attach_to"):
                cand["attach_to"] = attach_to
            cand.setdefault("promoted", False)
            cand.setdefault("promoted_at", None)
            return data, False

    entry = {
        "pattern": pattern,
        "type": pattern_type,
        "supporting_trajs": [traj_id],
        "first_seen": today,
        "last_seen": today,
        "promoted": False,
        "promoted_at": None,
    }
    if attach_to:
        entry["attach_to"] = attach_to
    candidates.append(entry)
    return data, True


def ready_for_promotion(data: dict, threshold: int = 3) -> list[dict]:
    out = []
    for c in data.get("candidates", []):
        if c.get("promoted"):
            continue
        if len(c.get("supporting_trajs", []) or []) >= threshold:
            out.append(c)
    return out


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def stale_candidates(data: dict, days: int = 60, threshold: int = 3) -> list[dict]:
    today = date.today()
    out = []
    for c in data.get("candidates", []):
        if c.get("promoted"):
            continue
        if len(c.get("supporting_trajs", []) or []) >= threshold:
            continue
        first = _parse_iso_date(c.get("first_seen"))
        if first is None:
            continue
        if (today - first).days >= days:
            out.append(c)
    return out


def mark_promoted(data: dict, pattern: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for c in data.get("candidates", []):
        if _fuzzy_equal(c.get("pattern", ""), pattern):
            c["promoted"] = True
            c["promoted_at"] = now
            return


# ═══════════════════════════════════════════════════════════════════
# Promotion: candidate → SKILL.md body insertion
# ═══════════════════════════════════════════════════════════════════

def _read_skill_md(skill_dir: Path) -> tuple[dict, str, Path]:
    upper = skill_dir / "SKILL.md"
    lower = skill_dir / "skill.md"
    if upper.exists():
        fm, body = fm_parse(upper.read_text(encoding="utf-8"))
        return fm, body, upper
    if lower.exists():
        fm, body = fm_parse(lower.read_text(encoding="utf-8"))
        return fm, body, lower
    return {}, "", upper


def _find_section_bounds(body: str, section_name: str | None) -> tuple[int, int]:
    """Return (start_line_index_after_header, end_line_index_exclusive) for the
    section whose ``##`` header *contains* ``section_name`` (case-insensitive).
    If ``section_name`` is None/empty, targets the last ``##`` section.
    If no section found, returns (len(lines), len(lines)) → appends at end.
    """
    lines = body.split("\n")
    headers: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = re.match(r"^##\s+(.*?)\s*$", ln)
        if m:
            headers.append((i, m.group(1).strip()))

    if not headers:
        return len(lines), len(lines)

    target_idx: int | None = None
    if section_name:
        needle = section_name.strip().lower()
        for k, (i, name) in enumerate(headers):
            if needle and (needle in name.lower() or name.lower() in needle):
                target_idx = k
                break
    if target_idx is None:
        target_idx = len(headers) - 1  # last section as fallback

    start = headers[target_idx][0] + 1
    end = headers[target_idx + 1][0] if target_idx + 1 < len(headers) else len(lines)
    return start, end


def _next_step_number(body: str) -> int:
    """Scan for the max `N.` list-item leading number in the body; return N+1."""
    max_n = 0
    for m in re.finditer(r"^(\d+)\.\s", body, flags=re.MULTILINE):
        n = int(m.group(1))
        if n > max_n:
            max_n = n
    return max_n + 1


def _evidence_tag(supporting_trajs: list[str]) -> str:
    total = len(supporting_trajs)
    sample = ", ".join(supporting_trajs[:3])
    return f"({total} trajectories: {sample})"


def _insert_warning(body: str, attach_to: str | None, pattern: str,
                    supporting_trajs: list[str]) -> str:
    lines = body.split("\n")
    start, end = _find_section_bounds(body, attach_to)
    tag = _evidence_tag(supporting_trajs)
    block = [
        "",
        f"   > ⚠️ {pattern} {tag}",
        "",
    ]
    # Insert right after the header (start), before any existing content.
    new_lines = lines[:start] + block + lines[start:end] + lines[end:]
    return "\n".join(new_lines)


def _insert_step(body: str, attach_to: str | None, pattern: str,
                 supporting_trajs: list[str]) -> str:
    lines = body.split("\n")
    start, end = _find_section_bounds(body, attach_to)
    n = _next_step_number(body)
    tag = _evidence_tag(supporting_trajs)
    block = [
        "",
        f"{n}. {pattern}  {tag}",
        "",
    ]
    # append at end of the chosen section
    # trim trailing blank lines inside the section
    tail = end
    while tail - 1 >= start and lines[tail - 1].strip() == "":
        tail -= 1
    new_lines = lines[:tail] + block + lines[tail:end] + lines[end:]
    return "\n".join(new_lines)


def _insert_decision_branch(body: str, attach_to: str | None, pattern: str,
                             supporting_trajs: list[str]) -> str:
    lines = body.split("\n")
    start, end = _find_section_bounds(body, attach_to)
    tag = _evidence_tag(supporting_trajs)
    block = [
        "",
        f"- {pattern} {tag}",
        "",
    ]
    tail = end
    while tail - 1 >= start and lines[tail - 1].strip() == "":
        tail -= 1
    new_lines = lines[:tail] + block + lines[tail:end] + lines[end:]
    return "\n".join(new_lines)


def _apply_candidate(body: str, cand: dict) -> str:
    ptype = (cand.get("type") or "step").strip().lower()
    pattern = cand.get("pattern", "").strip()
    attach_to = cand.get("attach_to")
    supporters = cand.get("supporting_trajs", []) or []
    if ptype == "warning":
        return _insert_warning(body, attach_to, pattern, supporters)
    if ptype == "decision_branch":
        return _insert_decision_branch(body, attach_to, pattern, supporters)
    return _insert_step(body, attach_to, pattern, supporters)


def promote_ready_candidates(skill_dir: Path, threshold: int = 3) -> list[dict]:
    """Promote all ready candidates for this skill into SKILL.md.

    Returns the list of candidate dicts that were just promoted.
    """
    skill_dir = Path(skill_dir)
    data = load_candidates(skill_dir)
    ready = ready_for_promotion(data, threshold=threshold)
    if not ready:
        return []

    fm, body, path = _read_skill_md(skill_dir)
    if not fm and not body:
        logger.warning(f"no SKILL.md found at {skill_dir}; skipping promotion")
        return []

    promoted: list[dict] = []
    for cand in ready:
        try:
            body = _apply_candidate(body, cand)
            mark_promoted(data, cand.get("pattern", ""))
            promoted.append(cand)
        except Exception as e:
            logger.warning(f"failed to promote candidate '{cand.get('pattern','')[:60]}': {e}")

    if promoted:
        upper = skill_dir / "SKILL.md"
        upper.write_text(fm_serialize(fm, body), encoding="utf-8")
        save_candidates(skill_dir, data)
        logger.info(f"promoted {len(promoted)} candidate(s) in {skill_dir}")

    return promoted


# ═══════════════════════════════════════════════════════════════════
# Stale archival
# ═══════════════════════════════════════════════════════════════════

def archive_stale(skill_dir: Path, days: int = 60, threshold: int = 3) -> list[dict]:
    """Move stale candidates (older than ``days`` with < ``threshold`` supporters
    and not promoted) into ``references/stale_candidates.md`` and drop them
    from ``.candidates.yml``. Returns the archived entries.
    """
    skill_dir = Path(skill_dir)
    data = load_candidates(skill_dir)
    stale = stale_candidates(data, days=days, threshold=threshold)
    if not stale:
        return []

    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    stale_file = refs_dir / "stale_candidates.md"

    lines: list[str] = []
    if stale_file.exists():
        lines.append(stale_file.read_text(encoding="utf-8").rstrip())
        lines.append("")
    else:
        lines.append("# Stale Candidates")
        lines.append("")
        lines.append("Patterns that never gathered enough trajectory support.")
        lines.append("")

    today = date.today().isoformat()
    lines.append(f"## Archived {today}")
    lines.append("")
    for c in stale:
        supporters = c.get("supporting_trajs", []) or []
        lines.append(
            f"- [{c.get('type', 'step')}] {c.get('pattern', '')} "
            f"(first_seen={c.get('first_seen', '?')}, "
            f"supporters={len(supporters)}: {', '.join(supporters)})"
        )
    lines.append("")

    stale_file.write_text("\n".join(lines), encoding="utf-8")

    # Drop from candidates list
    stale_patterns = [c.get("pattern", "") for c in stale]
    data["candidates"] = [
        c for c in data.get("candidates", [])
        if not any(_fuzzy_equal(c.get("pattern", ""), sp) for sp in stale_patterns)
    ]
    save_candidates(skill_dir, data)
    return stale
