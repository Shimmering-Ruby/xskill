"""recommend-heavy 重活单轮的内存治理（issue #328）。

覆盖三层：
1. ``recommend_heavy_config``——批大小/内存预算配置校验。
2. ``heavy_worker.current_rss_mb`` / 内存预算中止——单轮批处理超预算时
   提前停止，已处理的部分正常生效，没处理的留给下一轮。
3. ``_workers.run_recommend_heavy_once`` 的接线——配置值传到
   ``heavy_worker.run_recommend_heavy_once``，状态文件带新字段。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.config import recommend_heavy_config
from xskill.recommend.heavy_worker import (
    _drain_full_sweep_batch,
    current_rss_mb,
)
from xskill.recommend.skill_vector_store import (
    DEFAULT_DIM,
    MemorySkillVectorIndex,
    content_sha_for_text,
    fake_embed,
)
from xskill.recommend.vector_dirty import (
    count_catalog_vector_dirty,
    seed_full_catalog_vector_sweep,
)


# ─────────────────────────────────────────────────────────────────
# 配置校验
# ─────────────────────────────────────────────────────────────────

class TestRecommendHeavyConfig:
    def test_defaults(self):
        cfg = recommend_heavy_config({})
        assert cfg == {"batch_limit": 256, "memory_budget_mb": 1024.0}

    def test_custom_values(self):
        cfg = recommend_heavy_config({
            "server": {
                "vector_sync_batch_limit": 50,
                "recommend_heavy_memory_budget_mb": 512,
            },
        })
        assert cfg == {"batch_limit": 50, "memory_budget_mb": 512.0}

    @pytest.mark.parametrize("bad", [0, -1, 1.5, True])
    def test_rejects_non_positive_int_batch_limit(self, bad):
        with pytest.raises(ValueError, match="vector_sync_batch_limit"):
            recommend_heavy_config({"server": {"vector_sync_batch_limit": bad}})

    @pytest.mark.parametrize("bad", [0, -1, "big", True])
    def test_rejects_non_positive_memory_budget(self, bad):
        with pytest.raises(ValueError, match="recommend_heavy_memory_budget_mb"):
            recommend_heavy_config(
                {"server": {"recommend_heavy_memory_budget_mb": bad}}
            )


# ─────────────────────────────────────────────────────────────────
# RSS 观测与内存预算中止
# ─────────────────────────────────────────────────────────────────

class TestMemoryBudget:
    def test_current_rss_mb_returns_non_negative_float(self):
        value = current_rss_mb()
        assert isinstance(value, float)
        assert value >= 0.0

    def test_batch_aborts_when_over_budget(self, tmp_path, monkeypatch):
        db = tmp_path / "registry.db"
        from xskill.pipeline.registry import get_connection
        get_connection(db).close()

        for i in range(50):
            _store_row(db, f"native:k{i}", f"desc-{i}")
        seed_full_catalog_vector_sweep(db_path=db, existing_index_keys=set())
        assert count_catalog_vector_dirty(db_path=db) == 50

        calls = {"n": 0}

        def fake_rss():
            calls["n"] += 1
            # 前几次正常，之后报「超预算」，验证批内提前中止。
            return 10.0 if calls["n"] < 2 else 9999.0

        monkeypatch.setattr(
            "xskill.recommend.heavy_worker.current_rss_mb", fake_rss,
        )
        index = MemorySkillVectorIndex(dim=DEFAULT_DIM)
        stats = _drain_full_sweep_batch(
            db_path=db, index=index,
            embed=lambda text: fake_embed(text, DEFAULT_DIM),
            limit=50, force_upsert=False, memory_budget_mb=100.0,
        )
        assert stats["budget_aborted"] is True
        # 没有处理完全部 50 条：一部分仍留在脏表里，交给下一轮。
        assert count_catalog_vector_dirty(db_path=db) > 0
        assert stats["upserted"] < 50

    def test_no_budget_configured_never_aborts(self, tmp_path, monkeypatch):
        db = tmp_path / "registry.db"
        from xskill.pipeline.registry import get_connection
        get_connection(db).close()
        for i in range(30):
            _store_row(db, f"native:k{i}", f"desc-{i}")
        seed_full_catalog_vector_sweep(db_path=db, existing_index_keys=set())

        monkeypatch.setattr(
            "xskill.recommend.heavy_worker.current_rss_mb",
            lambda: 9999.0,
        )
        index = MemorySkillVectorIndex(dim=DEFAULT_DIM)
        stats = _drain_full_sweep_batch(
            db_path=db, index=index,
            embed=lambda text: fake_embed(text, DEFAULT_DIM),
            limit=30, force_upsert=False, memory_budget_mb=None,
        )
        assert stats["budget_aborted"] is False
        assert count_catalog_vector_dirty(db_path=db) == 0
        assert stats["upserted"] == 30


def _store_row(db: Path, key: str, description: str) -> None:
    from xskill.pipeline.registry import pooled_connection

    with pooled_connection(db) as conn:
        conn.execute(
            """
            INSERT INTO skills_catalog(
                catalog_key, name, source, state, description, distributable,
                content_sha
            ) VALUES (?, ?, 'native', 'main', ?, 1, ?)
            """,
            (key, key.split(":", 1)[-1], description, content_sha_for_text(description)),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────
# _workers.run_recommend_heavy_once 接线
# ─────────────────────────────────────────────────────────────────

class TestWorkersWiring:
    def test_config_values_flow_into_heavy_tick_and_status_file(
        self, tmp_path, monkeypatch,
    ):
        import xskill._workers as workers_mod
        from xskill.utils.status_file import PROFILE_STATUS_FILE, read_status_file

        monkeypatch.setattr(workers_mod, "XSKILL_HOME", tmp_path, raising=False)
        monkeypatch.setattr(
            "xskill.config.XSKILL_HOME", tmp_path, raising=False,
        )
        monkeypatch.setattr(
            "xskill.config.load_config",
            lambda: {
                "server": {
                    "vector_sync_batch_limit": 77,
                    "recommend_heavy_memory_budget_mb": 333,
                },
            },
        )
        monkeypatch.setattr(
            "xskill.team.server.engine_factory.build_recommend_engine",
            lambda config: object(),
        )
        monkeypatch.setattr(workers_mod, "run_profile_refresh_once", lambda engine: 0)

        captured = {}

        def fake_heavy_tick(*, engine, vector_sync_batch_limit, memory_budget_mb):
            captured["batch_limit"] = vector_sync_batch_limit
            captured["memory_budget_mb"] = memory_budget_mb
            return {
                "vector": {
                    "upserted": 3, "deleted": 1, "skipped": 2,
                    "mode": "full", "reason": "bootstrap", "remaining": 5,
                    "budget_aborted": False,
                },
                "recommends": 4,
                "index_kind": "memory",
                "rss_peak_mb": 42.5,
            }

        monkeypatch.setattr(
            "xskill.recommend.heavy_worker.run_recommend_heavy_once",
            fake_heavy_tick,
        )

        rc = workers_mod.run_recommend_heavy_once()
        assert rc == 0
        assert captured == {"batch_limit": 77, "memory_budget_mb": 333.0}

        status = read_status_file(tmp_path / PROFILE_STATUS_FILE)
        assert status["ok"] is True
        stats = status["stats"]
        assert stats["vector_upserted"] == 3
        assert stats["vector_deleted"] == 1
        assert stats["vector_skipped"] == 2
        assert stats["vector_mode"] == "full"
        assert stats["vector_reason"] == "bootstrap"
        assert stats["vector_remaining"] == 5
        assert stats["vector_budget_aborted"] is False
        assert stats["recommends"] == 4
        assert stats["index_kind"] == "memory"
        assert stats["rss_peak_mb"] == 42.5
