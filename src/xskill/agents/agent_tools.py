"""Agno tools exposed to xskill agents.

This module is the boundary for agent-visible tools. Keep tool implementation
logic in ``skill_tools``; wrappers here only declare Agno tool metadata and
delegate to the implementation functions.
"""
from __future__ import annotations

from agno.tools import tool

from xskill.agents import skill_tools as ST


@tool(name="atom_task_read")
def atom_task_read(atom_id: str) -> str:
    """Read one AtomTask JSON by atom_id."""
    return ST.atom_task_read(atom_id)


@tool(name="atom_task_search")
def atom_task_search(query: str, top_k: int = 5) -> str:
    """Search AtomTask records by vector and keyword match."""
    return ST.atom_task_search(query, top_k)


@tool(name="read_traj")
def read_traj(traj_id: str, offset_start: int, offset_end: int) -> str:
    """Read a line-range slice from one source trajectory markdown file."""
    return ST.read_traj(traj_id, offset_start, offset_end)


@tool(name="skill_read")
def skill_read(skill_name: str) -> str:
    """Read a skill's SKILL.md content."""
    return ST.skill_read(skill_name)


@tool(name="read_skill_tasks")
def read_skill_tasks(skill_name: str) -> str:
    """Read a skill candidates buffer."""
    return ST.read_skill_tasks(skill_name)


@tool(name="new_skill_folder")
def new_skill_folder(skill_name: str, description: str) -> str:
    """Create a baby skill folder and initialize its git repository."""
    return ST.new_skill_folder(skill_name, description)


@tool(name="add_task_to_skill")
def add_task_to_skill(skill_name: str, atom_id: str, weightscore: int) -> str:
    """Add one AtomTask contribution to a skill candidates buffer."""
    return ST.add_task_to_skill(skill_name, atom_id, weightscore)


@tool(name="rename_skill")
def rename_skill(old_name: str, new_name: str) -> str:
    """Rename a baby skill folder."""
    return ST.rename_skill(old_name, new_name)


@tool(name="move_task_to")
def move_task_to(skill_from: str, skill_to: str, atom_id: str) -> str:
    """Move one AtomTask contribution between skill candidates buffers."""
    return ST.move_task_to(skill_from, skill_to, atom_id)


@tool(name="score_task")
def score_task(atom_id: str, score: int) -> str:
    """Set one AtomTask UX score."""
    return ST.score_task(atom_id, score)


@tool(name="list_files")
def list_files(path: str) -> str:
    """List files under the skill root."""
    return ST.list_files(path)


@tool(name="write_file")
def write_file(path: str, content: str) -> str:
    """Write a file under the skill root."""
    return ST.write_file(path, content)


@tool(name="commit_baby_to_main")
def commit_baby_to_main(skill_name: str, message: str) -> str:
    """Commit a baby skill and graduate it to main."""
    return ST.commit_baby_to_main(skill_name, message)


@tool(name="commit_to_staging")
def commit_to_staging(skill_name: str, message: str) -> str:
    """Commit a main skill update to staging."""
    return ST.commit_to_staging(skill_name, message)


@tool(name="absorb_user_edit_to_main")
def absorb_user_edit_to_main(skill_name: str, message: str) -> str:
    """Commit user edits to the skill main branch."""
    return ST.absorb_user_edit_to_main(skill_name, message)

