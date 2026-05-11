"""
ecosystems.py -- Install distilled skills into AI-agent ecosystems
==================================================================

xskill produces skills in a self-managed directory (``~/.xskill/skill/<name>/``)
that is internally a git repo with canary / staging branches. External
ecosystems (Claude Code, OpenCode, Codex, openclaw) discover skills from their
own well-known directories.

This module bridges the two: it copies the stable (``main`` branch) SKILL.md
into the ecosystem's discovery path so the host agent can load it.

Only the stable side is installed. Canary / staging trials are an internal
A/B mechanism; once a staging variant wins (``canary.merge_staging_to_main``)
it lands on ``main`` and the next ``install_*`` call ships it to the host.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable


def install_to_claude_code(
    skill_path: Path | str,
    target_root: Path | str | None = None,
) -> Path:
    """Install one skill into ``<target_root>/.claude/skills/<name>/``.

    ``skill_path`` is a xskill skill directory (must contain ``SKILL.md``).
    ``target_root`` defaults to ``Path.home()``. Returns the destination
    ``SKILL.md`` path.

    The destination directory is created if absent. An existing ``SKILL.md``
    is overwritten (Claude Code's discovery is content-driven; stale frontmatter
    must not survive an update).
    """
    skill_path = Path(skill_path)
    if not skill_path.is_dir():
        raise NotADirectoryError(f"skill_path is not a directory: {skill_path}")

    src_md = skill_path / "SKILL.md"
    if not src_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_path}")

    name = skill_path.name
    root = Path(target_root) if target_root else Path.home()
    dest_dir = root / ".claude" / "skills" / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_md = dest_dir / "SKILL.md"
    shutil.copyfile(src_md, dest_md)
    return dest_md


def install_all_to_claude_code(
    skill_dir: Path | str,
    target_root: Path | str | None = None,
    names: Iterable[str] | None = None,
) -> list[Path]:
    """Install every skill under ``skill_dir`` (each subdir = one skill) to
    Claude Code's discovery root. If ``names`` is given, restrict to those.
    Returns the list of destination ``SKILL.md`` paths actually written.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise NotADirectoryError(f"skill_dir is not a directory: {skill_dir}")

    name_filter = set(names) if names is not None else None
    installed: list[Path] = []
    for entry in sorted(skill_dir.iterdir()):
        if not entry.is_dir():
            continue
        if name_filter is not None and entry.name not in name_filter:
            continue
        if not (entry / "SKILL.md").exists():
            continue
        installed.append(install_to_claude_code(entry, target_root=target_root))
    return installed
