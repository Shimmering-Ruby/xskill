from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from xskill.team.shared.git_bundle import (
    make_repo_bundle, apply_repo_bundle, make_branch_bundle, fetch_branch_from_bundle,
)


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _seed_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], root)
    _git(["checkout", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    (root / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], root)
    _git(["commit", "-q", "-m", "v1"], root)
    _git(["checkout", "-q", "-b", "staging"], root)
    (root / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], root)
    _git(["checkout", "-q", "main"], root)
    return root


def test_make_and_apply_bundle_clones_all_branches(tmp_path):
    src = _seed_repo(tmp_path / "central" / "fix-foo")
    bundle = make_repo_bundle(src)
    assert isinstance(bundle, bytes) and len(bundle) > 0

    dest = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(bundle, dest)
    assert (dest / ".git").is_dir()
    main_sha = _git(["rev-parse", "main"], dest)
    staging_sha = _git(["rev-parse", "staging"], dest)
    assert main_sha != staging_sha


def test_apply_bundle_updates_existing_repo(tmp_path):
    src = _seed_repo(tmp_path / "central" / "fix-foo")
    dest = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(make_repo_bundle(src), dest)
    # central 上 main 前进一格
    (src / "SKILL.md").write_text("v3", encoding="utf-8")
    _git(["commit", "-q", "-am", "v3"], src)
    new_main = _git(["rev-parse", "main"], src)
    apply_repo_bundle(make_repo_bundle(src), dest)
    assert _git(["rev-parse", "main"], dest) == new_main


def test_push_branch_roundtrip(tmp_path):
    central = _seed_repo(tmp_path / "central" / "fix-foo")
    client = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(make_repo_bundle(central), client)
    # client 在 main 基础上做一笔"用户手改"提交到 _useredit
    _git(["checkout", "-q", "-B", "_useredit", "main"], client)
    (client / "SKILL.md").write_text("user-edit", encoding="utf-8")
    _git(["commit", "-q", "-am", "user edit"], client)
    bundle = make_branch_bundle(client, "_useredit")
    sha = fetch_branch_from_bundle(bundle, central, "_useredit",
                                   "refs/heads/user-staging/cid-1")
    assert _git(["rev-parse", "user-staging/cid-1"], central) == sha
    assert _git(["rev-parse", "main"], central) != sha   # main 没被动


def test_make_repo_bundle_rejects_non_repo(tmp_path):
    with pytest.raises(NotADirectoryError):
        make_repo_bundle(tmp_path / "nope")
