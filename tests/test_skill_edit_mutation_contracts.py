"""Focused contracts used by mutmut for SkillEdit's durable boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from xskill.agents import agent_tools
from xskill.skill import candidates as candidate_buffer
from xskill.skill import git as skill_git


def test_graduate_baby_forwards_exact_optimizer_and_git_arguments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "skills" / "graduation-contract"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\n"
        "name: graduation-contract\n"
        "description: valid mutation contract\n"
        "---\n\n"
        "# Graduation contract\n",
        encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []

    def optimize(actual_target: Path, actual_slug: str) -> None:
        calls.append(("optimize", actual_target, actual_slug))

    def graduate(actual_target: str, actual_message: str) -> bool:
        calls.append(("git", actual_target, actual_message))
        return True

    monkeypatch.setattr(
        agent_tools,
        "_run_description_optimization",
        optimize,
    )
    monkeypatch.setattr(
        skill_git,
        "commit_baby_to_main_branch",
        graduate,
    )

    assert agent_tools.graduate_baby_to_main(
        target,
        "graduation-contract",
        "all candidate checkpoints complete",
    ) is True
    assert calls == [
        ("optimize", target, "graduation-contract"),
        ("git", str(target), "all candidate checkpoints complete"),
    ]


def test_graduate_baby_rejects_invalid_frontmatter_before_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "skills" / "invalid-graduation"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("not frontmatter", encoding="utf-8")

    def unexpected(*_args, **_kwargs) -> None:
        raise AssertionError("invalid SKILL.md must not reach a side effect")

    monkeypatch.setattr(
        agent_tools,
        "_run_description_optimization",
        unexpected,
    )
    monkeypatch.setattr(
        skill_git,
        "commit_baby_to_main_branch",
        unexpected,
    )

    assert agent_tools.graduate_baby_to_main(
        target,
        "invalid-graduation",
        "must not commit",
    ) is False


def test_remove_candidates_does_not_consume_a_git_write_slot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill_dir = tmp_path / "skills" / "candidate-lock-contract"
    skill_dir.mkdir(parents=True)
    data: dict = {"candidates": []}
    for atom_id in ("atom-01", "atom-02"):
        data, _ = candidate_buffer.add_atom_contribution(
            data,
            atom_id,
            5,
        )
    candidate_buffer.save_candidates(skill_dir, data)
    lock_calls: list[tuple[Path, bool]] = []

    @contextmanager
    def recording_lock(
        actual_skill_dir: Path,
        *,
        use_git_write_limit: bool = True,
    ) -> Iterator[None]:
        lock_calls.append((actual_skill_dir, use_git_write_limit))
        yield

    monkeypatch.setattr(
        candidate_buffer,
        "skill_repo_lock",
        recording_lock,
    )

    consumed, remaining = candidate_buffer.remove_candidates(
        skill_dir,
        {"atom-01"},
    )

    assert consumed == ["atom-01"]
    assert remaining == 1
    assert lock_calls == [(skill_dir, False)]
