"""test_dashboard_config.py —— config.dashboard 段解析"""
from __future__ import annotations

from xskill.config import dashboard_config


def test_dashboard_config_defaults_when_absent():
    assert dashboard_config({}) == {"enabled": False, "public": False, "password": ""}


def test_dashboard_config_reads_values():
    cfg = {"dashboard": {"enabled": True, "public": True, "password": "s3cret"}}
    assert dashboard_config(cfg) == {"enabled": True, "public": True, "password": "s3cret"}


def test_dashboard_config_partial_fills_defaults():
    assert dashboard_config({"dashboard": {"enabled": True}}) == \
        {"enabled": True, "public": False, "password": ""}
