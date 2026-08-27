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


# ─────────────────────────────────────────────────────────────────
# PR #338 code review（tiammomo）复现的问题：内存预算检查漏洞、
# 推荐结果不能用不完整索引覆盖
# ─────────────────────────────────────────────────────────────────

class TestMemoryBudgetReviewFinding:
    def test_tiny_budget_small_batch_still_aborts(self, tmp_path, monkeypatch):
        """review 复现：memory_budget_mb=1、批量 10 条。旧的检查步长是
        20，10 条的批次永远凑不到一次检查点，budget 形同虚设，10 条会
        被全部处理。

        ``current_rss_mb`` 打桩成固定值而不是依赖真实 RSS——``current_rss_mb``
        在 Windows 上没有 ``resource`` 模块，恒为 0.0，用真实 RSS 断言在
        Windows CI 上必然失败；同文件里 ``TestMemoryBudget`` 的两个既有测试
        已经是这个打桩写法。
        """
        db = tmp_path / "registry.db"
        from xskill.pipeline.registry import get_connection
        get_connection(db).close()
        for i in range(10):
            _store_row(db, f"native:k{i}", f"desc-{i}")
        seed_full_catalog_vector_sweep(db_path=db, existing_index_keys=set())
        assert count_catalog_vector_dirty(db_path=db) == 10

        monkeypatch.setattr(
            "xskill.recommend.heavy_worker.current_rss_mb", lambda: 9999.0,
        )
        index = MemorySkillVectorIndex(dim=DEFAULT_DIM)
        stats = _drain_full_sweep_batch(
            db_path=db, index=index,
            embed=lambda text: fake_embed(text, DEFAULT_DIM),
            limit=10, force_upsert=False,
            memory_budget_mb=1.0,
        )
        assert stats["budget_aborted"] is True
        assert stats["upserted"] == 0  # 一条都不该处理
        assert count_catalog_vector_dirty(db_path=db) == 10  # 全部留给下一轮


class TestRecommendsGatingIntegration:
    """`run_recommend_heavy_once` 按 `_recommends_safe_to_recompute` 门禁
    决定要不要用这一轮的索引重算推荐——不安全时不调用
    `process_dirty_recommends`，已有推荐槽保持原样。

    ``open_skill_vector_index``/``fake_embed``/``MemorySkillVectorIndex`` 是
    ``run_recommend_heavy_once`` 内部局部 import 的，要在它们的源模块
    （``skill_vector_store``）打 monkeypatch 才生效，不能打在
    ``heavy_worker`` 模块对象上；``run_vector_sync``/``process_dirty_recommends``/
    ``_embed_fn_from_engine`` 是同一个模块里定义的函数，直接打在
    ``heavy_worker`` 模块对象上即可。
    """

    def _patch_open_index(self, monkeypatch):
        monkeypatch.setattr(
            "xskill.recommend.skill_vector_store.open_skill_vector_index",
            lambda *_a, **_kw: MemorySkillVectorIndex(dim=DEFAULT_DIM),
        )

    def test_recommends_computed_when_incremental(self, monkeypatch):
        import xskill.recommend.heavy_worker as hw

        calls = {"process": 0}
        monkeypatch.setattr(
            hw, "run_vector_sync",
            lambda **_kw: {"upserted": 0, "deleted": 0, "mode": "incremental", "reason": ""},
        )
        monkeypatch.setattr(
            hw, "process_dirty_recommends",
            lambda **_kw: calls.__setitem__("process", calls["process"] + 1) or 3,
        )
        monkeypatch.setattr(hw, "_embed_fn_from_engine", lambda engine: None)
        self._patch_open_index(monkeypatch)

        result = hw.run_recommend_heavy_once(
            engine=object(), db_path="/tmp/unused-db",
        )
        assert calls["process"] == 1
        assert result["recommends"] == 3
        assert result["recommends_deferred"] is False

    def test_recommends_deferred_when_full_sweep_in_progress(self, monkeypatch):
        import xskill.recommend.heavy_worker as hw

        calls = {"process": 0}
        monkeypatch.setattr(
            hw, "run_vector_sync",
            lambda **_kw: {
                "upserted": 2, "deleted": 0, "mode": "full", "reason": "bootstrap",
                "remaining": 5,
            },
        )
        monkeypatch.setattr(
            hw, "process_dirty_recommends",
            lambda **_kw: calls.__setitem__("process", calls["process"] + 1) or 99,
        )
        monkeypatch.setattr(hw, "_embed_fn_from_engine", lambda engine: None)
        self._patch_open_index(monkeypatch)

        result = hw.run_recommend_heavy_once(
            engine=object(), db_path="/tmp/unused-db",
        )
        assert calls["process"] == 0  # 没调用——索引还不完整，不能拿它覆盖已有推荐
        assert result["recommends"] == 0
        assert result["recommends_deferred"] is True

    def test_recommends_computed_when_full_sweep_completes_this_round(self, monkeypatch):
        import xskill.recommend.heavy_worker as hw

        calls = {"process": 0}
        monkeypatch.setattr(
            hw, "run_vector_sync",
            lambda **_kw: {
                "upserted": 2, "deleted": 0, "mode": "full", "reason": "bootstrap",
                "remaining": 0,  # 持久索引：追平即完整，可信
            },
        )
        monkeypatch.setattr(
            hw, "process_dirty_recommends",
            lambda **_kw: calls.__setitem__("process", calls["process"] + 1) or 7,
        )
        monkeypatch.setattr(hw, "_embed_fn_from_engine", lambda engine: None)
        # memory_index 显式传入 → ephemeral_index=False（当成可复用/持久对待）。
        result = hw.run_recommend_heavy_once(
            engine=object(), db_path="/tmp/unused-db",
            memory_index=MemorySkillVectorIndex(dim=DEFAULT_DIM),
        )
        assert calls["process"] == 1
        assert result["recommends"] == 7
        assert result["recommends_deferred"] is False

    def test_recommends_not_computed_ephemeral_multi_round_tail(self, monkeypatch):
        """无持久索引、多轮播种的尾轮：remaining 归零但这轮没有
        total_indexable（不是本轮播种+消化完的），内存索引只有这轮那一小
        批，不能信。"""
        import xskill.recommend.heavy_worker as hw

        calls = {"process": 0}
        monkeypatch.setattr(
            hw, "run_vector_sync",
            lambda **_kw: {
                "upserted": 2, "deleted": 0, "mode": "full", "reason": "ephemeral",
                "remaining": 0,
            },
        )
        monkeypatch.setattr(
            hw, "process_dirty_recommends",
            lambda **_kw: calls.__setitem__("process", calls["process"] + 1) or 1,
        )
        monkeypatch.setattr(hw, "_embed_fn_from_engine", lambda engine: None)
        self._patch_open_index(monkeypatch)

        result = hw.run_recommend_heavy_once(
            engine=object(), db_path="/tmp/unused-db",
        )
        assert calls["process"] == 0
        assert result["recommends_deferred"] is True
