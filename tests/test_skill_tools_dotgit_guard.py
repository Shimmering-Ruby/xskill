"""write_file 必须拒绝任何 .git/ 路径——agent LLM 撞到 git 错误时会试图
'自己修 git'，往 .git/HEAD / .git/refs / .git/config 写垃圾,把可恢复的
index race 变成永久损坏（实跑遇到 3 个 skill 仓损坏）。
"""
from __future__ import annotations

import pytest

from xskill.agents import agent_tools


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "foo").mkdir()
    snap = agent_tools.agent_tool_config.snapshot()
    agent_tools.init_skill_authoring_tool_context(root, root, {})
    agent_tools.init_atom_task_tool_context(
        skill_dir=root,
        atom_store=None,
        default_traj_root=root,
    )
    yield root
    agent_tools.agent_tool_config.restore(snap)


def test_reject_dotgit_head(skill_root):
    target = skill_root / "foo" / ".git" / "HEAD"
    target.parent.mkdir(parents=True)
    out = agent_tools.write_file.entrypoint(str(target), "ref: refs/heads/baby\n")
    assert "error" in out.lower()
    assert ".git" in out
    assert not target.is_file(), "write_file 居然写了 .git/HEAD"


def test_reject_dotgit_refs(skill_root):
    target = skill_root / "foo" / ".git" / "refs" / "heads" / "baby"
    target.parent.mkdir(parents=True)
    out = agent_tools.write_file.entrypoint(str(target), "deadbeef" * 5)
    assert "error" in out.lower()
    assert not target.is_file()


def test_reject_dotgit_config(skill_root):
    target = skill_root / "foo" / ".git" / "config"
    target.parent.mkdir(parents=True)
    out = agent_tools.write_file.entrypoint(str(target), "placeholder")
    assert "error" in out.lower()
    assert not target.is_file()


def test_normal_skill_file_still_works(skill_root):
    target = skill_root / "foo" / "SKILL.md"
    out = agent_tools.write_file.entrypoint(
        str(target),
        "---\nname: foo\ndescription: x\n---\n# foo\n",
    )
    assert "error" not in out.lower()
    assert target.is_file()
    assert "name: foo" in target.read_text(encoding="utf-8")


def test_normal_scripts_and_references_still_work(skill_root):
    s = skill_root / "foo" / "scripts" / "x.sh"
    s.parent.mkdir(parents=True, exist_ok=True)
    out = agent_tools.write_file.entrypoint(str(s), "#!/bin/sh\necho ok\n")
    assert "error" not in out.lower()
    assert s.is_file()
    r = skill_root / "foo" / "references" / "note.md"
    r.parent.mkdir(parents=True, exist_ok=True)
    out = agent_tools.write_file.entrypoint(str(r), "# note\n")
    assert "error" not in out.lower()
    assert r.is_file()
