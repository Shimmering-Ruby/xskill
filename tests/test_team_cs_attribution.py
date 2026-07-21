from __future__ import annotations

import subprocess
from pathlib import Path

from xskill.pipeline.runner import DirectoryWatcher
from xskill.pipeline.atom import AtomTask, AtomTaskStore
from xskill.canary import load_ux_scores


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(skill_dir: Path, name: str):
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    return d


def test_server_mode_scores_each_used_skill(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "fix-foo")

    # 构造一条已 split 完的 traj：1 个 atom，used_skills=["fix-foo"]
    sessions = tmp_path / "clients" / "cid-1" / "sessions"
    sessions.mkdir(parents=True)
    md = sessions / "traj_cc_x_001.md"
    md.write_text("# body", encoding="utf-8")
    store = AtomTaskStore(root=sessions)
    store.save(AtomTask(
        atom_id="atom_traj_cc_x_001_0001", traj_id="traj_cc_x_001",
        offset_start=0, offset_end=6, intent="i", summary="s",
        tags=[], used_skills=["fix-foo"], ux_score=8,
        pre_atom_id=None, post_atom_id=None, context_prefix="", raw_segment="# body",
    ))

    w = DirectoryWatcher(llm=object(), skill_dir=skill_dir, store=store,
                         config={"canary": {"probability": 0.2}}, server_mode=True)
    # 模拟 list_watch_dirs 返回该 client 桶，label=client_id
    monkeypatch.setattr("xskill.pipeline.runner.list_watch_dirs",
                        lambda **kw: [{"id": 1, "path": str(sessions), "label": "cid-1"}])
    w._score_atoms_for_traj_server(1, "traj_cc_x_001.md")

    rows = load_ux_scores(skill_dir / "fix-foo")
    assert len(rows) == 1 and rows[0]["side"] == "main" and rows[0]["score"] == 8.0
