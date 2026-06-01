"""test_dashboard_metrics.py —— DashboardMetrics 衍生指标"""
from __future__ import annotations

from xskill.pipeline.registry import get_connection
from xskill.dashboard.metrics import DashboardMetrics


def _seed(db):
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES('/cc','cc','claude_code')")
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES('/oc','oc','opencode')")
    rows = [  # (wd, status, atoms, skill_generated, retry, ux, model)
        (1, 'done', 6, 'nginx-skill', 0, 8.0, 'deepseek-v4-pro'),
        (1, 'done', 4, '', 1, 7.0, 'deepseek-v4-flash'),
        (1, 'splitting', 2, None, 0, None, 'deepseek-v4-flash'),
        (2, 'done', 3, 'oc-skill', 0, 7.5, 'deepseek-v4-flash'),
    ]
    for wd, st, a, sg, rt, ux, m in rows:
        conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted,"
                     "skill_generated,retry_count,ux_score,source_model) VALUES(?,?,?,?,?,?,?,?)",
                     (wd, f"f{a}{st}", st, a, sg, rt, ux, m))
    conn.commit()
    conn.close()


def test_overview_ratios(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    o = DashboardMetrics(db_path=db).overview()
    assert o["trajs"] == 4 and o["atoms"] == 15
    assert o["avg_atoms_per_traj"] == 3.75          # 15/4
    assert o["success_rate"] == 75.0                # 3 done / 4
    assert o["skill_yield"] == 50.0                 # 2 有 skill / 4
    assert o["retry_rate"] == 25.0                  # 1 retried / 4
    assert round(o["avg_ux"], 2) == 7.5             # (8+7+7.5)/3


def test_overview_empty_db_no_zerodiv(tmp_path):
    db = tmp_path / "e.db"
    get_connection(db).close()
    o = DashboardMetrics(db_path=db).overview()
    assert o == {"trajs": 0, "atoms": 0, "avg_atoms_per_traj": 0.0, "success_rate": 0.0,
                 "skill_yield": 0.0, "retry_rate": 0.0, "avg_ux": 0.0}


def test_by_ecosystem(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    rows = {r["ecosystem"]: r for r in DashboardMetrics(db_path=db).by_ecosystem()}
    assert rows["claude_code"]["trajs"] == 3 and rows["claude_code"]["atoms"] == 12
    assert rows["claude_code"]["skills"] == 1
    assert rows["opencode"]["trajs"] == 1


def test_by_model(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    rows = {r["model"]: r for r in DashboardMetrics(db_path=db).by_model()}
    assert rows["deepseek-v4-flash"]["trajs"] == 3
    assert rows["deepseek-v4-pro"]["skills"] == 1
