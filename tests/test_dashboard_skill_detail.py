"""tests/test_dashboard_skill_detail.py — 单 skill 详情：版本统计 + 树 + 预览 + diff（子项目 D1）"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.metrics import DashboardMetrics
from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline.registry import (
    register_dir, discover_trajectories, mark_skill_used, get_connection,
)


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                   text=True, check=True)


def _seed_db(tmp_path, db):
    d = tmp_path / "ng_sessions"
    d.mkdir()
    # sha 由 .md 头解析（不再落 DB 列）——头写入版本 sha
    (d / "traj_ng_a.md").write_text(
        "<!-- xskill:skill=fix-foo side=main sha=aaaa1111 -->\n# a\n## Tool Call: Bash\n",
        encoding="utf-8")
    (d / "traj_ng_b.md").write_text(
        "<!-- xskill:skill=fix-foo side=staging sha=bbbb2222 -->\n# b\n",
        encoding="utf-8")
    wid = register_dir(d, label="alice", ecosystem="ngagent", db_path=db)
    discover_trajectories(wid, d, db_path=db)
    mark_skill_used(wid, "traj_ng_a.md", "fix-foo", "main", db_path=db)
    mark_skill_used(wid, "traj_ng_b.md", "fix-foo", "staging", db_path=db)
    conn = get_connection(db)
    conn.execute("UPDATE trajectories SET ux_score=8 WHERE filename='traj_ng_a.md'")
    conn.execute("UPDATE trajectories SET ux_score=6 WHERE filename='traj_ng_b.md'")
    conn.commit()
    conn.close()
    # version aaaa 命中的 traj_a 有 2 个工具调用的 atom
    tasks = d / "traj_ng_a" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "atom_traj_ng_a_0001.json").write_text(
        json.dumps({"raw_segment": "## Tool Call: Bash\n## Tool Call: Read\n"}),
        encoding="utf-8")
    return d, wid


def _seed_skill_repo(skill_dir):
    sk = skill_dir / "fix-foo"
    sk.mkdir(parents=True)
    _git(["init", "-q"], sk)
    _git(["config", "user.email", "t@t"], sk)
    _git(["config", "user.name", "t"], sk)
    (sk / "SKILL.md").write_text("---\nname: fix-foo\ndescription: d\n---\n# body\n",
                                 encoding="utf-8")
    (sk / "ref.md").write_text("reference content", encoding="utf-8")
    _git(["add", "."], sk)
    _git(["commit", "-q", "-m", "v1 init"], sk)
    return sk


# ── metrics 层 ─────────────────────────────────────────────────────

def test_skill_version_stats_groups_by_sha(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    m = DashboardMetrics(db_path=db)
    vs = {v["sha"]: v for v in m.skill_version_stats("fix-foo")}

    assert set(vs) == {"aaaa1111", "bbbb2222"}
    assert vs["aaaa1111"]["triggers"] == 1
    assert vs["aaaa1111"]["avg_ux"] == 8.0
    assert vs["bbbb2222"]["avg_ux"] == 6.0
    # version aaaa 的 atom 有 2 个工具调用
    assert vs["aaaa1111"]["avg_tool_calls"] == 2.0


def test_skill_detail_total_triggers_from_trajectories(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    d = DashboardMetrics(db_path=db).skill_detail("fix-foo")
    assert d["total_triggers"] == 2
    assert len(d["versions"]) == 2


def test_skill_by_user_attribution(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    users = DashboardMetrics(db_path=db).skill_by_user("fix-foo")
    assert users[0]["user"] == "alice"
    assert users[0]["triggers"] == 2


# ── router 层 ──────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    _seed_skill_repo(tmp_path / "skill")
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db))
    return TestClient(app)


def test_route_detail(client):
    r = client.get("/api/v1/dashboard/skill/fix-foo/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["total_triggers"] == 2
    assert len(body["versions_git"]) == 1  # 一次 commit


def test_route_tree_lists_files(client):
    r = client.get("/api/v1/dashboard/skill/fix-foo/tree")
    files = {f["path"] for f in r.json()["files"]}
    assert "SKILL.md" in files and "ref.md" in files
    assert not any(".git" in f for f in files)


def test_route_file_preview(client):
    r = client.get("/api/v1/dashboard/skill/fix-foo/file", params={"path": "ref.md"})
    assert r.json()["content"] == "reference content"


def test_route_file_path_traversal_blocked(client):
    r = client.get("/api/v1/dashboard/skill/fix-foo/file",
                   params={"path": "../../../../etc/passwd"})
    assert r.status_code == 400


def test_route_diff_red_green_source(client):
    detail = client.get("/api/v1/dashboard/skill/fix-foo/detail").json()
    sha = detail["versions_git"][0]["sha"]
    r = client.get("/api/v1/dashboard/skill/fix-foo/diff", params={"sha": sha})
    assert r.status_code == 200
    # 首个 commit 的 diff 含新增行（+）
    assert "+" in r.json()["diff"]
