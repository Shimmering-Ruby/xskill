"""pymilvus 为 optional extra：未安装时不崩，并节流 warn。"""
from __future__ import annotations

import logging

import pytest

from xskill.recommend import skill_vector_store as svs


@pytest.fixture(autouse=True)
def _reset_milvus_gates(monkeypatch):
    monkeypatch.setattr(svs, "_pymilvus_import_ok", None)
    monkeypatch.setattr(svs, "_milvus_last_warn_mono", 0.0)
    yield
    monkeypatch.setattr(svs, "_pymilvus_import_ok", None)
    monkeypatch.setattr(svs, "_milvus_last_warn_mono", 0.0)


def test_open_skill_vector_index_falls_back_without_pymilvus(monkeypatch, caplog):
    monkeypatch.setattr(svs, "_pymilvus_import_ok", False)
    with caplog.at_level(logging.WARNING, logger="xskill.skill_vector_store"):
        index = svs.open_skill_vector_index(memory=False, dim=4)
    assert isinstance(index, svs.MemorySkillVectorIndex)
    assert any("Milvus Lite unavailable" in r.message for r in caplog.records)


def test_milvus_unavailable_warn_throttled_hourly(monkeypatch, caplog):
    monkeypatch.setattr(svs, "_pymilvus_import_ok", False)
    monkeypatch.setattr(svs, "_milvus_last_warn_mono", 0.0)
    with caplog.at_level(logging.WARNING, logger="xskill.skill_vector_store"):
        svs.warn_milvus_unavailable_hourly("pymilvus not installed")
        svs.warn_milvus_unavailable_hourly("pymilvus not installed")
    warns = [r for r in caplog.records if "Milvus Lite unavailable" in r.message]
    assert len(warns) == 1
    # 人为拨回时钟后应再 warn
    monkeypatch.setattr(
        svs, "_milvus_last_warn_mono",
        svs._milvus_last_warn_mono - svs._MILVUS_WARN_INTERVAL_S - 1,
    )
    with caplog.at_level(logging.WARNING, logger="xskill.skill_vector_store"):
        svs.warn_milvus_unavailable_hourly("again")
    warns = [r for r in caplog.records if "Milvus Lite unavailable" in r.message]
    assert len(warns) == 2


def test_try_open_milvus_lite_returns_none_without_pymilvus(monkeypatch):
    monkeypatch.setattr(svs, "_pymilvus_import_ok", False)
    assert svs.try_open_milvus_lite_index(dim=4) is None


def test_pyproject_keeps_pymilvus_optional_only():
    """主依赖不得再硬拉 pymilvus（否则阻断 client 自动更新）。"""
    from pathlib import Path
    import tomllib

    text = Path("pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    deps = "\n".join(data["project"]["dependencies"])
    assert "pymilvus" not in deps
    extras = data["project"]["optional-dependencies"]
    assert "milvus" in extras
    assert any("pymilvus" in x for x in extras["milvus"])
