"""
test_ux_score.py -- ux_score.py 打分器单元测试
===============================================
LLM 调用通过 monkeypatch mock，验证：
  - _parse_score 能容错提取 JSON
  - score_trajectory 调 LLM 并返回结构化结果
  - score_and_record 幂等落盘 + 触发 controller
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xskill import canary, ux_score
from xskill.git_lock import run_git


# ──────────────────────────────────────────────────────
# _parse_score
# ──────────────────────────────────────────────────────

class TestParseScore:
    def test_clean_json(self):
        r = ux_score._parse_score('{"score": 7, "reasons": "good"}')
        assert r["score"] == 7
        assert r["reasons"] == "good"

    def test_wrapped_json(self):
        raw = '下面是评分：\n```json\n{"score": 5, "reasons": "ok"}\n```'
        r = ux_score._parse_score(raw)
        assert r["score"] == 5

    def test_garbage_returns_empty(self):
        r = ux_score._parse_score("no json here")
        assert r == {}

    def test_markdown_code_block(self):
        raw = '```\n{"score":9,"reasons":"excellent"}\n```'
        r = ux_score._parse_score(raw)
        assert r["score"] == 9


# ──────────────────────────────────────────────────────
# score_trajectory (mocked LLM)
# ──────────────────────────────────────────────────────

class TestScoreTrajectory:
    def test_normal_score(self):
        llm = MagicMock()
        llm.chat.return_value = '{"score": 8, "reasons": "one-shot fix"}'
        r = ux_score.score_trajectory(
            llm, traj_md="## user\nfix it\n## assistant\ndone",
            skill_name="s", side="main",
        )
        assert r["score"] == 8
        assert "one-shot" in r["reasons"]
        llm.chat.assert_called_once()

    def test_invalid_score_returns_none(self):
        llm = MagicMock()
        llm.chat.return_value = "I cannot score this"
        r = ux_score.score_trajectory(
            llm, traj_md="x", skill_name="s", side="staging",
        )
        assert r["score"] is None

    def test_out_of_range_returns_none(self):
        llm = MagicMock()
        llm.chat.return_value = '{"score": 99, "reasons": "off scale"}'
        r = ux_score.score_trajectory(
            llm, traj_md="x", skill_name="s", side="main",
        )
        assert r["score"] is None


# ──────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────

def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init"], cwd=str(path))
    run_git(["checkout", "-b", "main"], cwd=str(path))
    run_git(["config", "user.email", "t@t"], cwd=str(path))
    run_git(["config", "user.name", "t"], cwd=str(path))
    (path / "SKILL.md").write_text("v1", encoding="utf-8")
    run_git(["add", "-A"], cwd=str(path))
    run_git(["commit", "-m", "init"], cwd=str(path))
    return path


# ──────────────────────────────────────────────────────
# score_and_record
# ──────────────────────────────────────────────────────

class TestScoreAndRecord:
    def test_normal_flow(self, tmp_path):
        sd = _init_repo(tmp_path / "skill")
        sha = canary.main_sha(sd)
        llm = MagicMock()
        llm.chat.return_value = '{"score": 7, "reasons": "ok"}'

        r = ux_score.score_and_record(
            llm=llm, skill_dir=sd, skill_name="s",
            traj_id="traj_001", traj_md="dialog", side="main",
            commit_sha=sha,
        )
        assert r["scored"] is True
        assert r["score"] == 7
        # controller should return no_staging (no staging branch)
        assert r["decision"]["action"] == "no_staging"
        # verify persisted
        records = canary.load_ux_scores(sd)
        assert len(records) == 1

    def test_duplicate_skipped(self, tmp_path):
        sd = _init_repo(tmp_path / "skill")
        sha = canary.main_sha(sd)
        llm = MagicMock()
        llm.chat.return_value = '{"score": 5, "reasons": "meh"}'

        ux_score.score_and_record(
            llm=llm, skill_dir=sd, skill_name="s",
            traj_id="traj_001", traj_md="d", side="main", commit_sha=sha,
        )
        r2 = ux_score.score_and_record(
            llm=llm, skill_dir=sd, skill_name="s",
            traj_id="traj_001", traj_md="d", side="main", commit_sha=sha,
        )
        assert r2["scored"] is False
        assert r2["decision"]["action"] == "duplicate_skipped"

    def test_score_failed_on_bad_llm(self, tmp_path):
        sd = _init_repo(tmp_path / "skill")
        sha = canary.main_sha(sd)
        llm = MagicMock()
        llm.chat.return_value = "garbage"

        r = ux_score.score_and_record(
            llm=llm, skill_dir=sd, skill_name="s",
            traj_id="traj_001", traj_md="d", side="main", commit_sha=sha,
        )
        assert r["scored"] is False
        assert r["decision"]["action"] == "score_failed"
        assert len(canary.load_ux_scores(sd)) == 0

    def test_triggers_controller_promoted(self, tmp_path):
        """当两侧各 3 条且 staging 胜出时，score_and_record 应返回 promoted。"""
        sd = _init_repo(tmp_path / "skill")
        initial = canary.main_sha(sd)
        (sd / "SKILL.md").write_text("v2", encoding="utf-8")
        run_git(["add", "-A"], cwd=str(sd))
        run_git(["commit", "-m", "v2"], cwd=str(sd))
        canary.route_main_history_to_staging(sd, initial)

        m_sha = canary.main_sha(sd)
        s_sha = canary.staging_sha(sd)
        cfg = canary.CanaryConfig(min_samples=3)

        llm = MagicMock()

        # 先直接加 2 条 main(低分) + 3 条 staging(高分)
        for i in range(2):
            canary.append_ux_score(sd, traj_id=f"m{i}", skill_name="s",
                                   side="main", commit_sha=m_sha, score=2, reasons="bad")
        for i in range(3):
            canary.append_ux_score(sd, traj_id=f"s{i}", skill_name="s",
                                   side="staging", commit_sha=s_sha, score=9, reasons="good")

        # 第 3 条 main 通过 score_and_record 投递 → 应触发 promoted
        llm.chat.return_value = '{"score": 3, "reasons": "bad again"}'
        r = ux_score.score_and_record(
            llm=llm, skill_dir=sd, skill_name="s",
            traj_id="m2", traj_md="d", side="main", commit_sha=m_sha,
            canary_config=cfg,
        )
        assert r["scored"] is True
        assert r["decision"]["action"] == "promoted"
        assert not canary.has_staging(sd)
