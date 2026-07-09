"""test_dashboard_explore.py —— P1 图形页数据源：进化图 / 血缘 / traj·atom 详情 /
管线进度 / 用户连接状态。"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.explore import (
    TrajExplorer, pipeline_progress, skill_lineage, skill_ux_daily,
    users_status,
)
from xskill.dashboard.gitgraph import skill_commit_graph
from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline.registry import (
    get_connection, record_atom_adoption, record_canary_decision,
)


# ── fixtures ────────────────────────────────────────────────────────

def _seed_traj(tmp_path, db, *, atoms=3):
    d = tmp_path / "sessions"
    d.mkdir(exist_ok=True)
    lines = [f"line-{i}" for i in range(1, 31)]
    (d / "traj_t1.md").write_text("\n".join(lines), encoding="utf-8")
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem)"
                 " VALUES(1,?,?,?)", (str(d), "alice", "team_client"))
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted,"
        "source_harness,source_model) VALUES(1,'traj_t1.md','done',?,"
        "'claude_code','dsv4')", (atoms,))
    conn.commit()
    conn.close()
    tasks = d / "traj_t1" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    ids = [f"atom_traj_t1_{i:04d}" for i in range(1, atoms + 1)]
    for i, aid in enumerate(ids):
        rec = {
            "atom_id": aid, "traj_id": "traj_t1",
            "offset_start": 1 + i * 10, "offset_end": 11 + i * 10,
            "intent": f"intent-{i}", "summary": f"sum-{i}",
            "tags": ["t"], "used_skills": ["s-a"] if i == 0 else [],
            "pre_atom_id": ids[i - 1] if i > 0 else None,
            "post_atom_id": ids[i + 1] if i < atoms - 1 else None,
            "source_model": "dsv4",
        }
        (tasks / f"{aid}.json").write_text(json.dumps(rec), encoding="utf-8")
    return d, ids


def _init_skill_repo(sub):
    def g(*args):
        subprocess.run(["git", "-C", str(sub)] + list(args),
                       capture_output=True, text=True, check=True)
    sub.mkdir(parents=True, exist_ok=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (sub / "SKILL.md").write_text("v1", encoding="utf-8")
    g("add", "."); g("commit", "-q", "-m", "v1 init")
    g("checkout", "-q", "-b", "staging")
    (sub / "SKILL.md").write_text("v2", encoding="utf-8")
    g("add", "."); g("commit", "-q", "-m", "staging work")
    g("checkout", "-q", "main")


def _sha(sub, ref):
    return subprocess.run(["git", "-C", str(sub), "rev-parse", ref],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


# ── 进化图 ──────────────────────────────────────────────────────────

def test_commit_graph_lanes_and_decision_annotation(tmp_path):
    db = tmp_path / "r.db"
    sub = tmp_path / "skill" / "s-a"
    _init_skill_repo(sub)
    s_sha, m_sha = _sha(sub, "staging"), _sha(sub, "main")
    record_canary_decision(skill="s-a", action="rejected", main_avg=7.3,
                           staging_avg=5.9, main_samples=3, staging_samples=3,
                           age_days=1.0, main_sha=m_sha, staging_sha=s_sha,
                           db_path=db)
    g = skill_commit_graph(tmp_path / "skill", "s-a", db_path=db)
    nodes = {n["sha"]: n for n in g["nodes"]}
    assert nodes[m_sha]["is_head_main"]
    assert "staging" in nodes[s_sha]["lanes"]
    assert nodes[s_sha]["decision"] == "rejected"
    assert g["decisions_unlocated"] == []


def test_commit_graph_legacy_decision_unlocated(tmp_path):
    """存量无 sha 的裁决进 unlocated，不模糊挂到节点。"""
    db = tmp_path / "r.db"
    sub = tmp_path / "skill" / "s-a"
    _init_skill_repo(sub)
    record_canary_decision(skill="s-a", action="promoted", main_avg=7.0,
                           staging_avg=8.0, main_samples=3, staging_samples=3,
                           age_days=1.0, db_path=db)  # 无 sha
    g = skill_commit_graph(tmp_path / "skill", "s-a", db_path=db)
    assert len(g["decisions_unlocated"]) == 1
    assert all(n["decision"] is None for n in g["nodes"])


def test_commit_graph_includes_rejected_refs(tmp_path):
    from xskill.canary import discard_staging
    db = tmp_path / "r.db"
    sub = tmp_path / "skill" / "s-a"
    _init_skill_repo(sub)
    rejected = _sha(sub, "staging")
    assert discard_staging(sub)
    g = skill_commit_graph(tmp_path / "skill", "s-a", db_path=db)
    nodes = {n["sha"]: n for n in g["nodes"]}
    assert "rejected" in nodes[rejected]["lanes"]


# ── traj / atom 详情 ────────────────────────────────────────────────

def test_traj_atoms_linked_list_order(tmp_path):
    db = tmp_path / "r.db"
    _seed_traj(tmp_path, db)
    ex = TrajExplorer(db, tmp_path / "skill")
    atoms = ex.traj_atoms("traj_t1")
    assert [a["atom_id"][-4:] for a in atoms] == ["0001", "0002", "0003"]
    assert all(a["chain"] == "linked" for a in atoms)


def test_atom_detail_raw_slice_by_line_offsets(tmp_path):
    db = tmp_path / "r.db"
    _seed_traj(tmp_path, db)
    d = TrajExplorer(db, tmp_path / "skill").atom_detail(
        "traj_t1", "atom_traj_t1_0001")
    assert d["raw_status"] == "ok"
    assert d["raw"].startswith("line-1\n")     # offset 1-based 行号
    assert d["raw"].endswith("line-10")        # [1, 11) → line-1..line-10


def test_atom_detail_source_cleaned_when_md_missing(tmp_path):
    db = tmp_path / "r.db"
    d, _ = _seed_traj(tmp_path, db)
    (d / "traj_t1.md").unlink()
    det = TrajExplorer(db, tmp_path / "skill").atom_detail(
        "traj_t1", "atom_traj_t1_0001")
    assert det["raw_status"] == "source_cleaned"
    assert det["raw"] is None


def test_atom_detail_destinations(tmp_path):
    db = tmp_path / "r.db"
    _seed_traj(tmp_path, db)
    record_atom_adoption(atom_id="atom_traj_t1_0001", skill="s-a",
                         weightscore=4, was_new=True, db_path=db)
    det = TrajExplorer(db, tmp_path / "skill").atom_detail(
        "traj_t1", "atom_traj_t1_0001")
    assert det["destinations"] == [
        {"skill": "s-a", "weightscore": 4, "state": "adopted",
         "ts": det["destinations"][0]["ts"]}]


def test_traj_detail_404(tmp_path):
    db = tmp_path / "r.db"
    get_connection(db).close()
    with pytest.raises(KeyError):
        TrajExplorer(db, None).traj_detail("nope")


# ── 血缘 / ux daily ─────────────────────────────────────────────────

def test_skill_lineage_attribution_and_cleaned_flag(tmp_path):
    db = tmp_path / "r.db"
    d, ids = _seed_traj(tmp_path, db)
    sub = tmp_path / "skill" / "s-a"
    sub.mkdir(parents=True)
    record_atom_adoption(atom_id=ids[0], skill="s-a", weightscore=4,
                         was_new=True, db_path=db)
    record_atom_adoption(atom_id="atom_traj_gone_0001", skill="s-a",
                         weightscore=2, was_new=True, db_path=db)  # 断链
    lin = skill_lineage(tmp_path / "skill", "s-a", db_path=db)
    rows = {a["atom_id"]: a for a in lin["atoms"]}
    assert rows[ids[0]]["user"] == "alice"
    assert rows[ids[0]]["intent"] == "intent-0"
    assert rows[ids[0]]["source_cleaned"] is False
    assert rows["atom_traj_gone_0001"]["source_cleaned"] is True  # 显式标注
    assert {"user": "alice", "atoms": 1} in lin["by_user"]


def test_skill_ux_daily_groups_by_day_and_side(tmp_path):
    sub = tmp_path / "skill" / "s-a"
    sub.mkdir(parents=True)
    recs = [
        {"atom_id": "a1", "skill_name": "s-a", "side": "main",
         "commit_sha": "x", "score": 8.0, "reasons": "",
         "scored_at": "2026-07-01T05:00:00+00:00"},
        {"atom_id": "a2", "skill_name": "s-a", "side": "main",
         "commit_sha": "x", "score": 6.0, "reasons": "",
         "scored_at": "2026-07-01T09:00:00+00:00"},
        {"atom_id": "a3", "skill_name": "s-a", "side": "staging",
         "commit_sha": "y", "score": 9.0, "reasons": "",
         "scored_at": "2026-07-02T09:00:00+00:00"},
    ]
    with (sub / ".ux_scores.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    daily = skill_ux_daily(tmp_path / "skill", "s-a")
    assert daily == [
        {"date": "2026-07-01", "side": "main", "avg_ux": 7.0, "n": 2},
        {"date": "2026-07-02", "side": "staging", "avg_ux": 9.0, "n": 1},
    ]


# ── 管线进度 / 连接状态 ─────────────────────────────────────────────

def test_pipeline_progress_stage_counts_and_candidates(tmp_path):
    db = tmp_path / "r.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path) VALUES(1,'/x')")
    for i, st in enumerate(["discovered", "splitting", "split_done",
                            "done", "done"]):
        conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status)"
                     " VALUES(1,?,?)", (f"t{i}.md", st))
    conn.commit()
    conn.close()
    sub = tmp_path / "skill" / "s-a"
    sub.mkdir(parents=True)
    (sub / ".candidates.yml").write_text(
        "candidates:\n- atom_id: a1\n  weightscore: 4\n"
        "- atom_id: a2\n  weightscore: 3\n", encoding="utf-8")
    p = pipeline_progress(db, tmp_path / "skill")
    assert p["stages"]["pending_split"] == 1
    assert p["stages"]["splitting"] == 1
    assert p["stages"]["clustering"] == 1
    assert p["stages"]["done"] == 2
    assert p["cold_start"] is None  # 信号不存在 → 区块不渲染
    assert p["candidates"] == [{
        "skill": "s-a", "weightscore": 7, "threshold": 10,
        "atoms": 2, "progress": 0.7}]


def test_users_status_online_by_last_seen(tmp_path):
    db = tmp_path / "r.db"
    _seed_traj(tmp_path, db)
    cdb = tmp_path / "team_clients.db"
    conn = sqlite3.connect(str(cdb))
    conn.execute("CREATE TABLE clients (client_id TEXT PRIMARY KEY,"
                 " label TEXT DEFAULT '', hostname TEXT DEFAULT '',"
                 " user_name TEXT, joined_at TEXT, last_seen TEXT)")
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO clients VALUES(?,?,?,?,?,?)",
                 ("c-1", "alice", "h", "alice",
                  "2026-07-01 00:00:00",
                  now.strftime("%Y-%m-%d %H:%M:%S")))
    conn.execute("INSERT INTO clients VALUES(?,?,?,?,?,?)",
                 ("c-2", "bob", "h", "bob", "2026-07-01 00:00:00",
                  (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    st = users_status(db)
    by = {u["user"]: u for u in st["users"]}
    assert by["alice"]["online"] is True
    assert by["bob"]["online"] is False
    assert st["online"] == 1
    assert by["alice"]["trajs"] == 1 and by["alice"]["atoms"] == 3
    assert by["alice"]["harness"][0]["harness"] == "claude_code"


def test_users_status_no_clients_db(tmp_path):
    db = tmp_path / "r.db"
    get_connection(db).close()
    st = users_status(db)
    assert st["users"] == [] and "team_clients.db" in st["reason"]


# ── router 挂载冒烟 ─────────────────────────────────────────────────

def test_router_new_endpoints_smoke(tmp_path):
    db = tmp_path / "registry.db"
    _seed_traj(tmp_path, db)
    _init_skill_repo(tmp_path / "skill" / "s-a")
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db))
    c = TestClient(app)
    assert c.get("/api/v1/dashboard/pipeline").status_code == 200
    assert c.get("/api/v1/dashboard/users/status").status_code == 200
    assert c.get("/api/v1/dashboard/traj/traj_t1").status_code == 200
    assert c.get("/api/v1/dashboard/traj/traj_t1/atoms").status_code == 200
    assert c.get(
        "/api/v1/dashboard/traj/traj_t1/atom/atom_traj_t1_0001"
    ).status_code == 200
    assert c.get("/api/v1/dashboard/skill/s-a/graph").status_code == 200
    assert c.get("/api/v1/dashboard/skill/s-a/lineage").status_code == 200
    assert c.get("/api/v1/dashboard/skill/s-a/ux/daily").status_code == 200
    assert c.get("/api/v1/dashboard/traj/nope").status_code == 404
    assert c.get("/api/v1/dashboard/skill/nope/graph").status_code == 404


def test_readonly_instance_sensitive_endpoints_not_mounted(tmp_path):
    """公网只读实例内容白名单（§1.3 / 验收 F3）：expose_sensitive=False 时
    轨迹/原子/用户端点物理 404，聚合端点正常。"""
    db = tmp_path / "registry.db"
    _seed_traj(tmp_path, db)
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db,
                                              expose_sensitive=False))
    c = TestClient(app)
    for ep in ["traj/traj_t1", "traj/traj_t1/atoms",
               "traj/traj_t1/atom/atom_traj_t1_0001", "users/status", "users"]:
        assert c.get(f"/api/v1/dashboard/{ep}").status_code == 404, ep
    assert c.get("/api/v1/dashboard/overview").status_code == 200
    assert c.get("/api/v1/dashboard/pipeline").status_code == 200
