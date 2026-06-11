"""看板离线探针触发率端点：历史 / 逐 case / 重跑 action（Phase 2）。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.router import build_dashboard_router
from xskill.pipeline.registry import record_trigger_eval


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                   text=True, check=True)


def _seed_skill_with_exp(skill_dir: Path) -> Path:
    sk = skill_dir / "log-analyzer"
    sk.mkdir(parents=True)
    _git(["init", "-q"], sk)
    _git(["config", "user.email", "t@t"], sk)
    _git(["config", "user.name", "t"], sk)
    (sk / "SKILL.md").write_text(
        "---\nname: log-analyzer\ndescription: analyze logs\n---\n# body\n",
        encoding="utf-8")
    _git(["add", "."], sk)
    _git(["commit", "-q", "-m", "v1"], sk)
    # 一个优化实验目录 + 逐 case json + summary
    exp = sk / ".description_optimization" / "001_20260611-000000" / "logs"
    exp.mkdir(parents=True)
    (exp / "iter0_train_00.json").write_text(json.dumps({
        "should_trigger": True, "did_trigger": True, "passed": True,
        "query": "parse the error log", "topic": "logs",
        "triggered_skill": "log-analyzer", "catalog": ["other"], "runs": [],
    }), encoding="utf-8")
    (sk / ".description_optimization" / "001_20260611-000000" / "summary.json"
     ).write_text(json.dumps({"chosen_reason": "x"}), encoding="utf-8")
    return sk


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "registry.db"
    _seed_skill_with_exp(tmp_path / "skill")
    record_trigger_eval(skill="log-analyzer", version_sha="abc123",
                        exp_id="001", train_score=0.75, test_score=0.9,
                        n_cases=8, catalog_size=5, db_path=db)
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db))
    return TestClient(app)


def test_trigger_history(client):
    r = client.get("/api/v1/dashboard/skill/log-analyzer/trigger")
    assert r.status_code == 200
    hist = r.json()["history"]
    assert len(hist) == 1
    assert hist[0]["test_score"] == 0.9
    assert hist[0]["n_cases"] == 8


def test_trigger_cases_reads_latest_exp(client):
    r = client.get("/api/v1/dashboard/skill/log-analyzer/trigger/cases")
    assert r.status_code == 200
    body = r.json()
    assert body["exp"] == "001_20260611-000000"
    assert len(body["cases"]) == 1
    assert body["cases"][0]["triggered_skill"] == "log-analyzer"
    assert body["summary"]["chosen_reason"] == "x"


def test_trigger_cases_empty_when_no_exp(client):
    r = client.get("/api/v1/dashboard/skill/log-analyzer/trigger/cases",
                   params={"exp": "999_nope"})
    # exp 不存在 → 回退最新（仍是 001）
    assert r.json()["exp"] == "001_20260611-000000"


def test_rerun_gated_off(client, monkeypatch):
    monkeypatch.setattr("xskill.config.get_config",
                        lambda: {"skill_opt": {"rerun_enabled": False}})
    r = client.post("/api/v1/dashboard/skill/log-analyzer/trigger/rerun",
                    json={"query": "parse log"})
    assert r.status_code == 403


def test_rerun_requires_query(client, monkeypatch):
    monkeypatch.setattr("xskill.config.get_config",
                        lambda: {"skill_opt": {"rerun_enabled": True}})
    r = client.post("/api/v1/dashboard/skill/log-analyzer/trigger/rerun",
                    json={})
    assert r.status_code == 400


def test_rerun_happy_path(client, monkeypatch):
    monkeypatch.setattr("xskill.config.get_config",
                        lambda: {"skill_opt": {"rerun_enabled": True}})
    monkeypatch.setattr(
        "xskill.skill.trigger_probe.rerun_probe_case",
        lambda root, name, query, *, config: {
            "query": query, "did_trigger": True,
            "catalog": ["other"], "runs": [{"triggered_skill": name, "hit": True}],
        },
    )
    r = client.post("/api/v1/dashboard/skill/log-analyzer/trigger/rerun",
                    json={"query": "parse the error log"})
    assert r.status_code == 200
    assert r.json()["did_trigger"] is True


def test_trigger_invalid_skill_name_blocked(client):
    r = client.get("/api/v1/dashboard/skill/..%2F..%2Fetc/trigger")
    assert r.status_code in (400, 404)
