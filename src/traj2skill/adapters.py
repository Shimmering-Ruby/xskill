"""
adapters.py -- Trajectory submission and format adaptation layer
=================================================================
Convert various input formats to the standard traj2skill format
(markdown + json metadata) and handle submission to the traj directory.
"""

import json
import re
from pathlib import Path
from typing import Optional

from traj2skill.config import get_traj_dir


def generate_traj_id(traj_dir: Path = None) -> str:
    """
    Auto-generate a traj ID like ``traj_0301`` based on existing files
    in *traj_dir*.  Scans for ``traj_*.md`` and picks max + 1.
    """
    traj_dir = traj_dir or get_traj_dir()
    traj_dir.mkdir(parents=True, exist_ok=True)

    existing_ids: list[int] = []
    for f in traj_dir.glob("traj_*.md"):
        m = re.match(r"traj_(\d+)", f.stem)
        if m:
            existing_ids.append(int(m.group(1)))

    next_id = max(existing_ids) + 1 if existing_ids else 1
    return f"traj_{next_id:04d}"


def adapt_trajectory(
    content: str,
    format: str,
    metadata: Optional[dict] = None,
) -> tuple[str, dict]:
    """
    Convert various input formats to the standard traj2skill representation.

    Supported *format* values:

    - ``markdown`` -- passthrough; content is already ``traj_*.md`` format.
    - ``json`` -- JSON object with fields like ``messages``, ``tool_calls``, etc.
      Converted to a markdown trajectory.
    - ``raw`` -- plain text; wrapped in a basic trajectory markdown template.

    Returns ``(md_content, json_metadata)``.
    """
    metadata = metadata or {}

    if format == "markdown":
        return content, metadata

    if format == "json":
        return _adapt_json(content, metadata)

    if format == "raw":
        return _adapt_raw(content, metadata)

    raise ValueError(f"unsupported trajectory format: {format!r}")


# ------------------------------------------------------------------
# Internal converters
# ------------------------------------------------------------------

def _adapt_json(content: str, metadata: dict) -> tuple[str, dict]:
    """Convert a JSON trajectory to markdown + metadata."""
    data = json.loads(content)

    # Merge top-level keys (except messages/tool_calls) into metadata
    meta = dict(metadata)
    for key in ("model", "instance_id", "repo", "task", "result", "exit_status"):
        if key in data and key not in meta:
            meta[key] = data[key]

    # Build markdown from messages / tool_calls
    lines: list[str] = []
    lines.append(f"# Trajectory")
    if meta.get("instance_id"):
        lines.append(f"\n**instance_id**: {meta['instance_id']}")
    if meta.get("model"):
        lines.append(f"**model**: {meta['model']}")
    lines.append("")

    messages = data.get("messages", [])
    tool_calls = data.get("tool_calls", [])

    for msg in messages:
        role = msg.get("role", "unknown")
        text = msg.get("content", "")
        lines.append(f"## {role.capitalize()}")
        lines.append("")
        if isinstance(text, str):
            lines.append(text)
        elif isinstance(text, list):
            # multi-part content
            for part in text:
                if isinstance(part, dict):
                    lines.append(part.get("text", str(part)))
                else:
                    lines.append(str(part))
        lines.append("")

    if tool_calls:
        lines.append("## Tool Calls")
        lines.append("")
        for tc in tool_calls:
            name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
            args = tc.get("arguments", tc.get("function", {}).get("arguments", ""))
            lines.append(f"### {name}")
            lines.append("```")
            lines.append(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False))
            lines.append("```")
            if tc.get("output"):
                lines.append(f"\n**output**:\n```\n{tc['output']}\n```")
            lines.append("")

    md_content = "\n".join(lines)
    return md_content, meta


def _adapt_raw(content: str, metadata: dict) -> tuple[str, dict]:
    """Wrap plain text in a basic trajectory markdown template."""
    lines = [
        "# Trajectory",
        "",
        "## Raw Content",
        "",
        content,
        "",
    ]
    md_content = "\n".join(lines)
    return md_content, dict(metadata)


# ------------------------------------------------------------------
# Submission
# ------------------------------------------------------------------

def submit_trajectory(
    content: str,
    format: str = "markdown",
    metadata: Optional[dict] = None,
    traj_id: Optional[str] = None,
    traj_dir: Optional[Path] = None,
) -> dict:
    """
    Complete submission flow:

    1. Resolve *traj_dir* (from param or ``get_traj_dir()``).
    2. Generate *traj_id* if not provided.
    3. Adapt the input format to standard markdown + JSON metadata.
    4. Write ``traj_{id}.md`` and optionally ``traj_{id}.json``.
    5. Return ``{"traj_id": ..., "path": ..., "status": "stored"}``.
    """
    traj_dir = Path(traj_dir) if traj_dir else get_traj_dir()
    traj_dir.mkdir(parents=True, exist_ok=True)

    if not traj_id:
        traj_id = generate_traj_id(traj_dir)

    md_content, json_metadata = adapt_trajectory(content, format, metadata)

    # Write markdown
    md_path = traj_dir / f"{traj_id}.md"
    md_path.write_text(md_content, encoding="utf-8")

    # Write JSON metadata if non-empty
    if json_metadata:
        json_path = traj_dir / f"{traj_id}.json"
        json_path.write_text(
            json.dumps(json_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "traj_id": traj_id,
        "path": str(md_path),
        "status": "stored",
    }
