"""
skill_manager.py -- Skill version management (v2 format)
==========================================================
Git-based CRUD for skills. All reads/writes target SKILL.md with YAML
frontmatter; legacy (skill.md + .abstract) directories are read lazily for
listing, but any mutation auto-migrates the directory to v2.
"""

import json, shutil, logging
from pathlib import Path

from traj2skill.git_lock import run_git, commit_changes
from traj2skill.frontmatter import parse as fm_parse, serialize as fm_serialize

logger = logging.getLogger("skill_manager")


def _skill_md_path(skill_path: Path) -> Path:
    """Prefer SKILL.md; fall back to legacy skill.md if that's all there is."""
    upper = skill_path / "SKILL.md"
    lower = skill_path / "skill.md"
    if upper.exists():
        return upper
    if lower.exists():
        return lower
    return upper  # non-existent upper — caller handles


def _load_skill(skill_path: Path) -> tuple[dict, str, Path]:
    """Return (frontmatter, body, path_used). For legacy dirs with only a
    plain skill.md + .abstract, synthesize a frontmatter dict from the
    .abstract contents so list_skills/show_skill continue to work without
    rewriting files on read."""
    p = _skill_md_path(skill_path)
    if not p.exists():
        return {}, "", p

    text = p.read_text(encoding="utf-8")
    fm, body = fm_parse(text)

    if fm:
        return fm, body, p

    # Legacy path: body is the whole text; synthesize from .abstract
    abstract_path = skill_path / ".abstract"
    synth = {"name": skill_path.name, "metadata": {}}
    if abstract_path.exists():
        try:
            abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
            synth["description"] = abstract.get("trigger", "") or abstract.get("summary", "")
            meta = synth["metadata"]
            meta["version"] = abstract.get("version", 0)
            meta["tags"] = abstract.get("tags", [])
            meta["source_trajs"] = abstract.get("source_trajs", [])
            meta["frozen"] = abstract.get("frozen", False)
            meta["summary"] = abstract.get("summary", "")
            if abstract.get("eval_result"):
                meta["eval"] = abstract["eval_result"]
        except Exception:
            pass
    return synth, text, p


def list_skills(skill_dir: Path) -> list[dict]:
    """List all skills with v2 metadata. Legacy skills are surfaced via the
    synthesized frontmatter in _load_skill."""
    results = []
    if not skill_dir.exists():
        return results

    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue

        fm, _body, _p = _load_skill(d)
        meta = fm.get("metadata", {}) or {}
        eval_block = meta.get("eval", {}) or {}
        entry = {
            "name": d.name,
            "version": int(meta.get("version", 0) or 0),
            "eval_score": eval_block.get("eval_score") or eval_block.get("score"),
            "tags": meta.get("tags", []) or [],
            "frozen": bool(meta.get("frozen", False)),
        }
        results.append(entry)

    return results


def show_skill(skill_dir: Path, name: str) -> dict:
    """Return skill details.

    Fields:
        name           — skill dir name
        description    — from frontmatter.description
        metadata       — frontmatter.metadata dict
        skill_md_body  — the markdown body AFTER the frontmatter
        skill_md_raw   — full raw SKILL.md (including frontmatter) for preview
        files          — relative file paths inside the skill dir
    """
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return {"error": f"skill not found: {name}"}

    fm, body, p = _load_skill(skill_path)

    raw = p.read_text(encoding="utf-8") if p.exists() else ""

    files = [
        str(f.relative_to(skill_path))
        for f in sorted(skill_path.rglob("*"))
        if f.is_file()
    ]

    return {
        "name": name,
        "description": (fm.get("description") or "").strip(),
        "metadata": fm.get("metadata", {}) or {},
        "skill_md_body": body,
        "skill_md_raw": raw,
        "files": files,
    }


def skill_log(skill_dir: Path, name: str) -> str:
    """Return git log for a skill directory."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return f"skill not found: {name}"

    code, out, err = run_git(
        ["log", "--oneline", "--follow", "-20", "--", f"{name}/"],
        cwd=str(skill_dir),
    )
    if code != 0:
        return f"git log failed: {err}"
    return out or "(no history)"


def skill_diff(skill_dir: Path, name: str, v1: str | None = None, v2: str | None = None) -> str:
    """Git diff for a skill. Default: HEAD~1 vs HEAD."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return f"skill not found: {name}"

    if v1 and v2:
        code, out, err = run_git(["diff", v1, v2, "--", f"{name}/"], cwd=str(skill_dir))
    else:
        code, out, err = run_git(["diff", "HEAD~1", "HEAD", "--", f"{name}/"], cwd=str(skill_dir))

    if code != 0:
        return f"git diff failed: {err}"
    return out or "(no diff)"


def rollback_skill(skill_dir: Path, name: str, version: str | None = None) -> bool:
    """Roll a skill back to a specific commit or HEAD~1."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        logger.error(f"skill not found: {name}")
        return False

    target = version or "HEAD~1"
    code, _, err = run_git(["checkout", target, "--", f"{name}/"], cwd=str(skill_dir))

    if code != 0:
        logger.error(f"rollback failed: {err}")
        return False

    committed = commit_changes(str(skill_dir), f"rollback {name} to {target}")
    return committed


def freeze_skill(skill_dir: Path, name: str) -> bool:
    """Freeze: set metadata.frozen = true in SKILL.md frontmatter."""
    return _set_frozen(skill_dir, name, True)


def unfreeze_skill(skill_dir: Path, name: str) -> bool:
    """Unfreeze: set metadata.frozen = false."""
    return _set_frozen(skill_dir, name, False)


def _set_frozen(skill_dir: Path, name: str, frozen: bool) -> bool:
    """Flip frontmatter.metadata.frozen and persist. Auto-migrates legacy dirs."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        logger.error(f"skill not found: {name}")
        return False

    fm, body, p = _load_skill(skill_path)
    if not fm:
        # empty dir — create a minimal stub so freeze/unfreeze isn't a no-op
        fm = {"name": name, "metadata": {"frozen": frozen}}
        body = body or f"# {name}\n"
    else:
        fm.setdefault("metadata", {})["frozen"] = frozen

    # Always write to SKILL.md (migrate from legacy on the fly)
    upper = skill_path / "SKILL.md"
    upper.write_text(fm_serialize(fm, body), encoding="utf-8")

    # remove legacy .abstract and skill.md if we just migrated
    legacy_abstract = skill_path / ".abstract"
    if legacy_abstract.exists():
        legacy_abstract.unlink()
    legacy_md = skill_path / "skill.md"
    if legacy_md.exists() and legacy_md != upper:
        legacy_md.unlink()

    action = "freeze" if frozen else "unfreeze"
    commit_changes(str(skill_dir), f"{action} {name}")
    logger.info(f"{action}: {name}")
    return True


def delete_skill(skill_dir: Path, name: str) -> bool:
    """Delete a skill directory and commit."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        logger.error(f"skill not found: {name}")
        return False

    shutil.rmtree(skill_path)
    committed = commit_changes(str(skill_dir), f"delete skill: {name}")
    if committed:
        logger.info(f"deleted: {name}")
    return committed


def export_skill(skill_dir: Path, name: str, output_path: Path) -> Path:
    """Copy the skill directory to output_path/<name>."""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        raise FileNotFoundError(f"skill not found: {name}")

    target = output_path / name if output_path.is_dir() else output_path
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_path, target)
    logger.info(f"exported: {name} -> {target}")
    return target


def import_skill(skill_dir: Path, source_path: Path) -> str:
    """Copy a skill directory into ./skill/ and commit."""
    source = Path(source_path)
    if not source.is_dir():
        raise FileNotFoundError(f"source not found: {source}")

    name = source.name
    target = skill_dir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    commit_changes(str(skill_dir), f"import skill: {name}")
    logger.info(f"imported: {name}")
    return name
