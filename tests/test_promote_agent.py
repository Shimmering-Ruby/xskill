"""
test_promote_agent.py -- promote_ready_candidates() agentic flow
=================================================================

Covers the two primary code paths refactored in the agent-driven promote:
  - happy: skill-edit agent runs cleanly; we verify candidates flip to
    promoted=True and the agent's writes survive.
  - fallback: skill-edit agent raises; rule-based _apply_candidate() must
    take over and still produce a SKILL.md update + promoted markers.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from xskill import candidates as cand_mod


def _seed_skill(skill_dir: Path, body: str = "# title\n\n## stage\n") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: fix-x\n"
        "description: test\n"
        "---\n\n" + body,
        encoding="utf-8",
    )
    cand_data = {
        "candidates": [
            {
                "pattern": "run python manage.py showmigrations to locate conflict",
                "type": "step",
                "attach_to": "stage",
                "supporting_trajs": ["traj_0001", "traj_0002", "traj_0003"],
                "first_seen": "2026-04-01",
                "last_seen": "2026-04-10",
                "promoted": False,
                "promoted_at": None,
            },
        ]
    }
    (skill_dir / ".candidates.yml").write_text(
        yaml.safe_dump(cand_data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_promote_happy_path_uses_agent_writes(tmp_path, monkeypatch):
    """Agent succeeds → its writes are preserved, candidate marked promoted."""
    skill_dir = tmp_path / "fix-x"
    _seed_skill(skill_dir)

    agent_calls: list[Path] = []

    def fake_agent(skill_dir, candidates, config):
        agent_calls.append(skill_dir)
        # Simulate the agent rewriting SKILL.md its own way + creating a script
        (skill_dir / "SKILL.md").write_text(
            "---\nname: fix-x\ndescription: test\n---\n\n"
            "# title\n\n## stage\n\n1. agent-authored step\n",
            encoding="utf-8",
        )
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "scripts" / "helper.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    monkeypatch.setattr(cand_mod, "_run_skill_edit_agent", fake_agent)

    promoted = cand_mod.promote_ready_candidates(
        skill_dir, threshold=3, config={"llm": {"base_url": "x", "model": "y"}}
    )

    assert len(promoted) == 1, "exactly one candidate hit threshold"
    assert agent_calls == [skill_dir], "skill-edit agent should run once"

    # Candidate flipped
    saved = yaml.safe_load((skill_dir / ".candidates.yml").read_text(encoding="utf-8"))
    assert saved["candidates"][0]["promoted"] is True
    assert saved["candidates"][0]["promoted_at"] is not None

    # Agent's writes survived (NOT clobbered by rule-based fallback)
    txt = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "agent-authored step" in txt
    assert (skill_dir / "scripts" / "helper.sh").exists()


def test_promote_fallback_when_agent_raises(tmp_path, monkeypatch):
    """Agent raises → rule-based _apply_candidate inserts step into SKILL.md."""
    skill_dir = tmp_path / "fix-x"
    _seed_skill(skill_dir)

    def boom(skill_dir, candidates, config):
        raise RuntimeError("simulated llm outage")

    monkeypatch.setattr(cand_mod, "_run_skill_edit_agent", boom)

    promoted = cand_mod.promote_ready_candidates(
        skill_dir, threshold=3, config={"llm": {"base_url": "x", "model": "y"}}
    )

    assert len(promoted) == 1, "fallback must still mark the candidate promoted"

    # Rule-based insertion ran — the candidate's pattern text is in SKILL.md
    txt = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "showmigrations" in txt, "rule-based fallback should have inserted the step"
    # And the evidence tag from _evidence_tag is there
    assert "traj_0001" in txt

    saved = yaml.safe_load((skill_dir / ".candidates.yml").read_text(encoding="utf-8"))
    assert saved["candidates"][0]["promoted"] is True


def test_promote_skips_when_no_ready(tmp_path, monkeypatch):
    """Candidate below threshold → no agent invocation, no marks flipped."""
    skill_dir = tmp_path / "fix-x"
    _seed_skill(skill_dir)
    # Drop supporters below threshold
    data = yaml.safe_load((skill_dir / ".candidates.yml").read_text(encoding="utf-8"))
    data["candidates"][0]["supporting_trajs"] = ["traj_0001"]
    (skill_dir / ".candidates.yml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    called = {"n": 0}

    def fake_agent(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(cand_mod, "_run_skill_edit_agent", fake_agent)

    promoted = cand_mod.promote_ready_candidates(skill_dir, threshold=3, config={})
    assert promoted == []
    assert called["n"] == 0
