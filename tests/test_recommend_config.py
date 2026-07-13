"""test_recommend_config.py — §1 recommend / skillhub / allow_anonymous 配置读取

TDD: 配置段读取 + 显式默认 + 坏类型 fail-loud（与 ingest_config / dashboard_config 同风格）。
"""
from pathlib import Path

import pytest

from xskill.config import (
    CONFIG_TEMPLATE,
    allow_anonymous_user,
    profile_refresh_config,
    recommend_config,
    skillhub_config,
)


class TestProfileRefreshConfig:
    def test_defaults_and_overrides(self):
        assert profile_refresh_config({}) == {
            "workers": 4,
            "queue_size": 1024,
            "shutdown_timeout": 5.0,
        }
        assert profile_refresh_config({"server": {
            "profile_refresh_workers": 8,
            "profile_refresh_queue_size": 99,
            "profile_refresh_shutdown_timeout": 1.5,
        }}) == {"workers": 8, "queue_size": 99, "shutdown_timeout": 1.5}

    @pytest.mark.parametrize("key,value", [
        ("profile_refresh_workers", 0),
        ("profile_refresh_workers", 1.5),
        ("profile_refresh_queue_size", -1),
        ("profile_refresh_queue_size", True),
        ("profile_refresh_shutdown_timeout", 0),
        ("profile_refresh_shutdown_timeout", float("inf")),
        ("profile_refresh_shutdown_timeout", float("nan")),
        ("profile_refresh_shutdown_timeout", "5"),
    ])
    def test_invalid_values_fail_loud(self, key, value):
        with pytest.raises(ValueError, match=key):
            profile_refresh_config({"server": {key: value}})

    def test_template_contains_profile_refresh_defaults(self):
        assert "profile_refresh_workers: 4" in CONFIG_TEMPLATE
        assert "profile_refresh_queue_size: 1024" in CONFIG_TEMPLATE
        assert "thread_pool_tokens: 80" in CONFIG_TEMPLATE


# ── recommend_config ──────────────────────────────────────────────

class TestRecommendConfig:
    def test_defaults_when_section_missing(self):
        cfg = recommend_config({})
        assert cfg["quality_ratio"] == 0.8
        assert cfg["cluster_centers"] == 5
        assert cfg["last_n_atoms"] == 5
        assert cfg["staging_need"] is None  # None = 复用 canary.total_samples

    def test_reads_overrides_from_dict(self):
        cfg = recommend_config({
            "recommend": {
                "quality_ratio": 0.6,
                "cluster_centers": 3,
                "last_n_atoms": 7,
                "staging_need": 10,
            }
        })
        assert cfg["quality_ratio"] == 0.6
        assert cfg["cluster_centers"] == 3
        assert cfg["last_n_atoms"] == 7
        assert cfg["staging_need"] == 10

    def test_bad_quality_ratio_fails_loud(self):
        with pytest.raises(ValueError, match="quality_ratio"):
            recommend_config({"recommend": {"quality_ratio": "high"}})

    def test_quality_ratio_out_of_range_fails_loud(self):
        with pytest.raises(ValueError, match="quality_ratio"):
            recommend_config({"recommend": {"quality_ratio": 1.5}})
        with pytest.raises(ValueError, match="quality_ratio"):
            recommend_config({"recommend": {"quality_ratio": -0.1}})

    def test_bad_cluster_centers_fails_loud(self):
        with pytest.raises(ValueError, match="cluster_centers"):
            recommend_config({"recommend": {"cluster_centers": 0}})

    def test_template_contains_recommend_section(self):
        assert "recommend:" in CONFIG_TEMPLATE
        assert "quality_ratio" in CONFIG_TEMPLATE
        assert "cluster_centers" in CONFIG_TEMPLATE


# ── skillhub_config ───────────────────────────────────────────────

class TestSkillhubConfig:
    def test_defaults_when_section_missing(self):
        cfg = skillhub_config({})
        assert cfg["enabled"] is False
        assert cfg["dir"] == Path.home() / ".xskill" / "skillhub_skills"

    def test_reads_overrides_from_dict(self, tmp_path):
        d = tmp_path / "hub"
        cfg = skillhub_config({"skillhub": {"enabled": True, "dir": str(d)}})
        assert cfg["enabled"] is True
        assert cfg["dir"] == d

    def test_bad_enabled_fails_loud(self):
        with pytest.raises(ValueError, match="enabled"):
            skillhub_config({"skillhub": {"enabled": "yes"}})

    def test_template_contains_skillhub_section(self):
        assert "skillhub:" in CONFIG_TEMPLATE
        assert "skillhub_skills" in CONFIG_TEMPLATE


# ── allow_anonymous_user ─────────────────────────────────────────

class TestAllowAnonymousUser:
    def test_default_true_when_missing(self):
        assert allow_anonymous_user({}) is True
        assert allow_anonymous_user({"team": {}}) is True
        assert allow_anonymous_user({"team": {"server": {}}}) is True

    def test_false_when_configured(self):
        cfg = {"team": {"server": {"allow_anonymous_user": False}}}
        assert allow_anonymous_user(cfg) is False

    def test_true_when_configured(self):
        cfg = {"team": {"server": {"allow_anonymous_user": True}}}
        assert allow_anonymous_user(cfg) is True

    def test_bad_type_fails_loud(self):
        with pytest.raises(ValueError, match="allow_anonymous_user"):
            allow_anonymous_user({"team": {"server": {"allow_anonymous_user": "no"}}})

    def test_template_contains_allow_anonymous(self):
        assert "allow_anonymous_user" in CONFIG_TEMPLATE
