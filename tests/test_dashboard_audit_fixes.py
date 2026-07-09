"""test_dashboard_audit_fixes.py —— 2026-07 指标审计修复的行为锁定。

覆盖：使用事实源切换到 .ux_scores.jsonl（P0-1）、推荐触发率事件级配对（P0-2）、
recommendation_log 曝光去重（P0-2 写入侧）、adoption 清理（P1-7）、
终态成功率口径（P2-10）、canary 分桶新口径（P1-4）、版本时序排序（P1-6）。
"""
from __future__ import annotations

import json

from xskill.dashboard.metrics import DashboardMetrics, load_usage_records
from xskill.pipeline.registry import (
    get_connection, record_atom_adoption, record_recommendation,
    reset_trajectories, unregister_dir,
)


def _write_scores(skill_dir, name, records):
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    with (d / ".ux_scores.jsonl").open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _seed_client(db, *, wd_id=1, path="/tc", label="alice", eco="team_client",
                 trajs=()):
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(?,?,?,?)",
        (wd_id, path, label, eco))
    for fn in trajs:
        conn.execute(
            "INSERT INTO trajectories(watch_dir_id,filename,status) VALUES(?,?,?)",
            (wd_id, fn, "done"))
    conn.commit()
    conn.close()


def _rec(skill, atom_id, *, side="main", sha="s1", score=8.0,
         ts="2026-07-02T10:00:00+00:00", model="dsv4"):
    return {"atom_id": atom_id, "skill_name": skill, "side": side,
            "commit_sha": sha, "score": score, "reasons": "",
            "user_model": model, "scored_at": ts}


# ── P0-1：使用事实源 = .ux_scores.jsonl ─────────────────────────────

def test_load_usage_records_normalizes_atom_and_traj_rows(tmp_path):
    sd = tmp_path / "skill"
    _write_scores(sd, "s-a", [
        _rec("s-a", "atom_traj_x_0001"),
        # 历史 traj 级记录（append_ux_score 形状）也要进统一视图
        {"traj_id": "traj_y", "skill_name": "s-a", "side": "staging",
         "commit_sha": "s2", "score": 6.5, "reasons": "",
         "scored_at": "2026-07-01T09:00:00+00:00"},
    ])
    recs = load_usage_records(sd)
    assert len(recs) == 2
    by_traj = {r["traj_id"]: r for r in recs}
    assert by_traj["traj_x"]["atom_id"] == "atom_traj_x_0001"  # atom→traj 解析
    assert by_traj["traj_y"]["side"] == "staging"


def test_load_usage_records_missing_dir_returns_empty(tmp_path):
    assert load_usage_records(tmp_path / "nope") == []
    assert load_usage_records(None) == []


def test_skill_detail_reads_usage_not_skill_used_column(tmp_path):
    """trajectories.skill_used 不再是使用信号：DB 里没有任何 skill_used 行，
    只要 .ux_scores.jsonl 有记录，详情就有触发数（CS 模式的真实形态）。"""
    db = tmp_path / "r.db"
    _seed_client(db, trajs=("traj_x.md",))
    sd = tmp_path / "skill"
    _write_scores(sd, "s-a", [_rec("s-a", "atom_traj_x_0001"),
                              _rec("s-a", "atom_traj_x_0002", sha="s2",
                                   ts="2026-07-03T10:00:00+00:00")])
    m = DashboardMetrics(db_path=db, skill_dir=sd)
    d = m.skill_detail("s-a")
    assert d["total_triggers"] == 2
    assert d["by_user"] == [{"user": "alice", "triggers": 2, "avg_ux": 8.0}]


def test_version_stats_sorted_by_first_use_time(tmp_path):
    """P1-6：版本按首次使用时间排序，不按 sha 字典序。"""
    sd = tmp_path / "skill"
    _write_scores(sd, "s-a", [
        _rec("s-a", "a1", sha="zzz", ts="2026-07-01T00:00:00+00:00"),
        _rec("s-a", "a2", sha="aaa", ts="2026-07-05T00:00:00+00:00"),
    ])
    m = DashboardMetrics(db_path=tmp_path / "r.db", skill_dir=sd)
    shas = [v["sha"] for v in m.skill_version_stats("s-a")]
    assert shas == ["zzz", "aaa"]  # 时间序：zzz 先被使用


# ── P0-2：推荐触发率事件级配对 ──────────────────────────────────────

def test_trigger_rate_pairs_exposure_with_later_use(tmp_path):
    db = tmp_path / "r.db"
    _seed_client(db, trajs=("traj_x.md",))
    sd = tmp_path / "skill"
    # 曝光在使用之前 → 配对成功
    record_recommendation(client_id="alice", skill="s-a", side="main",
                          bucket="recommended", sha="s1", db_path=db)
    _write_scores(sd, "s-a", [_rec("s-a", "atom_traj_x_0001",
                                   ts="2027-01-01T00:00:00+00:00")])
    m = DashboardMetrics(db_path=db, skill_dir=sd)
    t = m.trigger_rate()
    assert t["overall"] == 100.0
    assert t["by_skill"] == [
        {"skill": "s-a", "recommended": 1, "used": 1, "rate": 100.0}]


def test_trigger_rate_use_before_exposure_not_counted(tmp_path):
    """时间因果：先用后推不算命中（旧口径的谬误之一）。"""
    db = tmp_path / "r.db"
    _seed_client(db, trajs=("traj_x.md",))
    sd = tmp_path / "skill"
    _write_scores(sd, "s-a", [_rec("s-a", "atom_traj_x_0001",
                                   ts="2020-01-01T00:00:00+00:00")])
    record_recommendation(client_id="alice", skill="s-a", side="main",
                          bucket="recommended", sha="s1", db_path=db)
    m = DashboardMetrics(db_path=db, skill_dir=sd)
    t = m.trigger_rate()
    assert t["by_skill"][0]["used"] == 0
    assert t["overall"] == 0.0


def test_record_recommendation_idempotent_across_syncs(tmp_path):
    """同一 (client, skill, side, sha) 反复 sync 只留一条曝光。"""
    db = tmp_path / "r.db"
    for _ in range(50):
        record_recommendation(client_id="alice", skill="s-a", side="main",
                              bucket="recommended", sha="s1", db_path=db)
    conn = get_connection(db)
    n = conn.execute("SELECT COUNT(*) FROM recommendation_log").fetchone()[0]
    conn.close()
    assert n == 1


def test_migration_dedupes_legacy_inflated_rows(tmp_path):
    """存量注水行在建唯一索引前被一次性去重，保留最早一条。"""
    db = tmp_path / "r.db"
    conn = get_connection(db)  # 建表 + 已建唯一索引
    conn.execute("DROP INDEX idx_reco_dedup")  # 模拟旧库：无索引、有重复
    for i in range(5):
        conn.execute(
            "INSERT INTO recommendation_log(ts,client_id,skill,side,bucket,sha)"
            " VALUES(?,?,?,?,?,?)",
            (f"2026-07-0{i+1} 00:00:00", "alice", "s-a", "main",
             "recommended", ""))
    conn.commit()
    conn.close()
    conn = get_connection(db)  # 重开触发迁移
    rows = conn.execute(
        "SELECT ts FROM recommendation_log WHERE client_id='alice'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["ts"] == "2026-07-01 00:00:00"  # 保住首次曝光时间


# ── P1-7：adoption 分子随 reset/unregister 清理 ─────────────────────

def test_reset_trajectories_clears_adoption_rows(tmp_path):
    db = tmp_path / "r.db"
    wd = tmp_path / "wd"
    wd.mkdir()
    _seed_client(db, path=str(wd), trajs=("traj_x.md",))
    record_atom_adoption(atom_id="atom_traj_x_0001", skill="s-a",
                         weightscore=3, was_new=True, db_path=db)
    reset_trajectories(traj_id="traj_x", db_path=db)
    conn = get_connection(db)
    n = conn.execute("SELECT COUNT(*) FROM atom_adoption").fetchone()[0]
    conn.close()
    assert n == 0


def test_unregister_dir_clears_adoption_rows(tmp_path):
    db = tmp_path / "r.db"
    wd = tmp_path / "wd"
    wd.mkdir()
    _seed_client(db, path=str(wd.resolve()), trajs=("traj_x.md",))
    record_atom_adoption(atom_id="atom_traj_x_0001", skill="s-a",
                         weightscore=3, was_new=True, db_path=db)
    assert unregister_dir(wd, db_path=db)
    conn = get_connection(db)
    n = conn.execute("SELECT COUNT(*) FROM atom_adoption").fetchone()[0]
    conn.close()
    assert n == 0


# ── P2-10 / P1-4：口径修正 ──────────────────────────────────────────

def test_overview_success_rate_terminal_denominator(tmp_path):
    """在途轨迹不进成功率分母：3 done + 1 error + 2 discovered → 75%。"""
    db = tmp_path / "r.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path) VALUES(1,'/x')")
    for i, st in enumerate(["done", "done", "done", "error",
                            "discovered", "discovered"]):
        conn.execute(
            "INSERT INTO trajectories(watch_dir_id,filename,status) VALUES(1,?,?)",
            (f"t{i}.md", st))
    conn.commit()
    conn.close()
    o = DashboardMetrics(db_path=db).overview()
    assert o["success_rate"] == 75.0
    assert o["avg_ux"] is None  # 无使用记录不显示假数
    assert o["ux_n"] == 0


def test_canary_sides_from_usage_records(tmp_path):
    """P1-4：分桶来自使用打分记录，与轨迹总量无关。"""
    db = tmp_path / "r.db"
    _seed_client(db, trajs=tuple(f"t{i}.md" for i in range(20)))  # 20 条无关轨迹
    sd = tmp_path / "skill"
    _write_scores(sd, "s-a", [
        _rec("s-a", "a1", side="main", score=7.0),
        _rec("s-a", "a2", side="staging", score=9.0),
        _rec("s-a", "a3", side="staging", score=8.0),
    ])
    sides = {s["side"]: s for s in
             DashboardMetrics(db_path=db, skill_dir=sd).canary_sides()}
    assert sides["main"]["uses"] == 1
    assert sides["staging"]["uses"] == 2
    assert sides["staging"]["avg_ux"] == 8.5


# ── D9：canary 裁决可定位（进化图数据地基） ─────────────────────────

def _init_skill_repo(path):
    import subprocess
    def g(*args):
        subprocess.run(["git", "-C", str(path)] + list(args),
                       capture_output=True, text=True, check=True)
    path.mkdir(parents=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (path / "SKILL.md").write_text("v1", encoding="utf-8")
    g("add", "."); g("commit", "-q", "-m", "v1")
    g("checkout", "-q", "-b", "staging")
    (path / "SKILL.md").write_text("v2-staging", encoding="utf-8")
    g("add", "."); g("commit", "-q", "-m", "staging work")
    g("checkout", "-q", "main")


def test_discard_staging_preserves_rejected_ref(tmp_path):
    """被拒 staging 的 commit 经 refs/rejected/* 仍对 git log 可达。"""
    import subprocess
    from xskill.canary import discard_staging, staging_sha
    sk = tmp_path / "skill" / "s-a"
    _init_skill_repo(sk)
    rejected = staging_sha(sk)
    assert discard_staging(sk)
    out = subprocess.run(
        ["git", "-C", str(sk), "for-each-ref", "refs/rejected",
         "--format=%(objectname)"],
        capture_output=True, text=True, check=True).stdout.split()
    assert rejected in out
    # 且 commit 内容可达（能 show）
    show = subprocess.run(["git", "-C", str(sk), "show", "--stat", rejected],
                          capture_output=True, text=True, check=True)
    assert "staging work" in show.stdout


def test_record_canary_decision_stores_shas(tmp_path):
    from xskill.pipeline.registry import record_canary_decision
    db = tmp_path / "r.db"
    record_canary_decision(skill="s-a", action="rejected", main_avg=7.3,
                           staging_avg=5.9, main_samples=3, staging_samples=3,
                           age_days=2.0, main_sha="m" * 40, staging_sha="s" * 40,
                           db_path=db)
    conn = get_connection(db)
    row = conn.execute("SELECT main_sha, staging_sha FROM canary_decision").fetchone()
    conn.close()
    assert row["main_sha"] == "m" * 40 and row["staging_sha"] == "s" * 40
