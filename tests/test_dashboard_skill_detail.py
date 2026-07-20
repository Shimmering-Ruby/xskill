"""tests/test_dashboard_skill_detail.py — 单 skill 详情：版本统计 + 树 + 预览 + diff（子项目 D1）"""
from __future__ import annotations

import json
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.metrics import DashboardMetrics
from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline.registry import register_dir, discover_trajectories


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                   text=True, check=True)


def _seed_db(tmp_path, db):
    """使用事实源是 <skill_dir>/fix-foo/.ux_scores.jsonl（审计 P0-1）；
    registry 只提供 traj→用户 归因（watch_dir label）。skill_dir 约定为
    db 同级 ``skill/``（与 router 的 _skill_dir_for 一致）。"""
    d = tmp_path / "ng_sessions"
    d.mkdir()
    (d / "traj_ng_a.md").write_text("# a\n", encoding="utf-8")
    (d / "traj_ng_b.md").write_text("# b\n", encoding="utf-8")
    wid = register_dir(d, label="alice", ecosystem="ngagent", db_path=db)
    discover_trajectories(wid, d, db_path=db)
    sk = tmp_path / "skill" / "fix-foo"
    sk.mkdir(parents=True, exist_ok=True)
    with (sk / ".ux_scores.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "atom_id": "atom_traj_ng_a_0001", "skill_name": "fix-foo",
            "side": "main", "commit_sha": "aaaa1111", "score": 8.0,
            "reasons": "", "scored_at": "2026-07-01T10:00:00+00:00"}) + "\n")
        f.write(json.dumps({
            "atom_id": "atom_traj_ng_b_0001", "skill_name": "fix-foo",
            "side": "staging", "commit_sha": "bbbb2222", "score": 6.0,
            "reasons": "", "scored_at": "2026-07-02T10:00:00+00:00"}) + "\n")
    return d, wid


def _seed_skill_repo(skill_dir):
    sk = skill_dir / "fix-foo"
    sk.mkdir(parents=True, exist_ok=True)  # _seed_db 可能已放入 .ux_scores.jsonl
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
    m = DashboardMetrics(db_path=db, skill_dir=tmp_path / "skill")
    vs = {v["sha"]: v for v in m.skill_version_stats("fix-foo")}

    assert set(vs) == {"aaaa1111", "bbbb2222"}
    assert vs["aaaa1111"]["triggers"] == 1
    assert vs["aaaa1111"]["avg_ux"] == 8.0
    assert vs["bbbb2222"]["avg_ux"] == 6.0
    # 按首次使用时间排序（审计 P1-6）
    order = [v["sha"] for v in m.skill_version_stats("fix-foo")]
    assert order == ["aaaa1111", "bbbb2222"]


def test_skill_detail_total_triggers_from_trajectories(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    d = DashboardMetrics(db_path=db,
                         skill_dir=tmp_path / "skill").skill_detail("fix-foo")
    assert d["total_triggers"] == 2
    assert len(d["versions"]) == 2


def test_skill_by_user_attribution(tmp_path):
    db = tmp_path / "registry.db"
    _seed_db(tmp_path, db)
    users = DashboardMetrics(
        db_path=db, skill_dir=tmp_path / "skill").skill_by_user("fix-foo")
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
