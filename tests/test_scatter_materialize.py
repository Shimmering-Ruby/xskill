"""test_scatter_materialize.py —— #106 画像散点事件触发物化 + multiprocessing 隔离

覆盖:registry 缓存读写往返/指纹语义、端点只读化(命中/未命中入队/去重)、纯 worker
确定性、真 spawn 进程池冒烟、ProfileRefreshService 完成画像后投递散点重算。
"""
from __future__ import annotations

import json
import multiprocessing
import pickle
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.profile_viz import (
    ProfileViz, compute_scatter_payload, profile_db_for, skillhub_index_for)
from xskill.dashboard.router import _skill_dir_for, build_dashboard_router
from xskill.pipeline import registry as R
from xskill.recommend.profile_store import ProfileStore
from xskill.team.server.profile_refresh import ProfileRefreshService


def _seed_alice(pdb: Path) -> ProfileStore:
    """alice:两簇各 3 点(git/docker),供散点投影;source_revision 记一个稳定串。"""
    store = ProfileStore(pdb)
    a = np.array([[1, 0.05 * i, 0, 0] for i in range(3)], dtype=float)
    b = np.array([[0.05 * i, 1, 0, 0] for i in range(3)], dtype=float)
    pts = np.vstack([a, b])
    pts /= np.linalg.norm(pts, axis=1, keepdims=True)
    centers = np.vstack([a.mean(0), b.mean(0)])
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    meta = [{"atom_id": f"atom_t_{i:04d}", "summary": f"s{i}", "ux": 8,
             "tags": ["git"] if i < 3 else ["docker"]} for i in range(6)]
    store.upsert("alice", feature_tensor=centers, mean_tensor=pts.mean(0),
                 used_skills=[{"name": "sk1", "use_count": 3}],
                 points=pts, point_meta=meta, source_revision="rev-1")
    return store


def _endpoint_fingerprint(registry_db: Path, user_key: str) -> str:
    """按端点同款构造 ProfileViz 取指纹,保证测试与端点算出的指纹一致。"""
    viz = ProfileViz(profile_db_for(registry_db),
                     skill_dir=_skill_dir_for(registry_db), db_path=registry_db,
                     skillhub_index=skillhub_index_for(registry_db))
    return viz.scatter_input_fingerprint(user_key)


# ── a. registry 缓存读写往返 + 指纹语义 ──────────────────────────────

def test_scatter_cache_roundtrip_and_overwrite(tmp_path):
    db = tmp_path / "r.db"
    payload = {"points": [{"x": 1.0, "y": 2.0}], "method": "tsne"}
    R.write_scatter_cache("alice", "tsne", "fp-1", payload, db_path=db)
    got = R.read_scatter_cache("alice", "tsne", db_path=db)
    assert got is not None
    assert json.loads(got["payload"]) == payload
    assert got["fingerprint"] == "fp-1" and got["computed_at"]
    # 覆盖:同 (user,method) 更新指纹与坐标
    R.write_scatter_cache("alice", "tsne", "fp-2", {"points": []}, db_path=db)
    got2 = R.read_scatter_cache("alice", "tsne", db_path=db)
    assert got2["fingerprint"] == "fp-2" and json.loads(got2["payload"]) == {"points": []}
    # 不同 method / 缺失键
    assert R.read_scatter_cache("alice", "umap", db_path=db) is None
    assert R.read_scatter_cache("bob", "tsne", db_path=db) is None


def test_fingerprint_mismatch_is_miss(tmp_path):
    """缓存指纹与当前输入指纹不一致 → 视为未命中(端点会入队重算)。"""
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    current = _endpoint_fingerprint(db, "alice")
    R.write_scatter_cache("alice", "tsne", "stale-fp", {"points": []}, db_path=db)
    cached = R.read_scatter_cache("alice", "tsne", db_path=db)
    assert cached["fingerprint"] != current  # 命中判定 = 指纹相等,此处不等 → miss


# ── b. 端点只读化 ───────────────────────────────────────────────────

def _dashboard_client(registry_db: Path) -> TestClient:
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=registry_db))
    return TestClient(app)


def test_endpoint_returns_cached_payload_on_hit(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    monkeypatch.setattr("xskill.api.app._profile_refresh_ref", {})  # 无常驻服务
    fingerprint = _endpoint_fingerprint(db, "alice")
    sentinel = {"points": [{"x": 9.0, "y": 9.0}], "method": "tsne", "marker": "cached"}
    R.write_scatter_cache("alice", "tsne", fingerprint, sentinel, db_path=db)
    resp = _dashboard_client(db).get("/api/v1/dashboard/user/alice/scatter?method=tsne")
    assert resp.status_code == 200
    assert resp.json() == sentinel  # 命中直返物化坐标包,未重算


def test_endpoint_miss_returns_pending_and_enqueues_once(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))

    class _RecordingService:
        def __init__(self):
            self.enqueued = []
            self._inflight = set()

        def submit_scatter(self, user_key, method):
            key = (user_key, method)
            if key in self._inflight:
                return True
            self._inflight.add(key)
            self.enqueued.append(key)
            return True

    service = _RecordingService()
    monkeypatch.setattr("xskill.api.app._profile_refresh_ref", {"instance": service})
    client = _dashboard_client(db)
    first = client.get("/api/v1/dashboard/user/alice/scatter?method=tsne")
    assert first.status_code == 200 and first.json()["status"] == "pending"
    # 同 (user,method) 再请求 → 仍 pending,但服务侧去重,不重复入队
    second = client.get("/api/v1/dashboard/user/alice/scatter?method=tsne")
    assert second.json()["status"] == "pending"
    assert service.enqueued == [("alice", "tsne")]


def test_endpoint_inline_materializes_without_service(tmp_path, monkeypatch):
    """独立只读实例(无常驻服务):未命中 → 直算并物化,返回真实坐标包。"""
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    monkeypatch.setattr("xskill.api.app._profile_refresh_ref", {})
    resp = _dashboard_client(db).get("/api/v1/dashboard/user/alice/scatter?method=umap")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" not in body and len(body["points"]) == 6
    # 已物化:缓存里出现指纹一致的坐标包
    cached = R.read_scatter_cache("alice", "umap", db_path=db)
    assert cached["fingerprint"] == _endpoint_fingerprint(db, "alice")


def test_endpoint_unknown_method_and_missing_user(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    monkeypatch.setattr("xskill.api.app._profile_refresh_ref", {})
    client = _dashboard_client(db)
    assert client.get("/api/v1/dashboard/user/alice/scatter?method=pca").status_code == 400
    assert client.get("/api/v1/dashboard/user/ghost/scatter?method=tsne").status_code == 404


# ── worker 纯函数:确定性 + 可 pickle ───────────────────────────────

def test_worker_pure_function_deterministic(tmp_path):
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    viz = ProfileViz(profile_db_for(db))
    scatter_inputs = viz.gather_scatter_inputs("alice", method="umap")
    # 纯数据可 pickle(跨进程边界前提);函数按引用可 pickle
    pickle.loads(pickle.dumps(scatter_inputs))
    pickle.loads(pickle.dumps(compute_scatter_payload))
    first = compute_scatter_payload(scatter_inputs)
    second = compute_scatter_payload(scatter_inputs)
    xy1 = [(p["x"], p["y"]) for p in first["points"]]
    xy2 = [(p["x"], p["y"]) for p in second["points"]]
    assert xy1 == xy2 and len(xy1) == 6


# ── c. 真 spawn 进程池冒烟 ──────────────────────────────────────────

@pytest.mark.timeout(60)
def test_spawn_process_pool_smoke(tmp_path):
    db = tmp_path / "r.db"
    _seed_alice(profile_db_for(db))
    scatter_inputs = ProfileViz(profile_db_for(db)).gather_scatter_inputs(
        "alice", method="tsne")
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        payload = pool.submit(compute_scatter_payload, scatter_inputs).result()
    assert payload["method"] == "tsne" and len(payload["points"]) == 6


# ── c. ProfileRefreshService 事件触发 + 去重 ────────────────────────

class _FakeEngine:
    """具备散点取数属性(skill_dir/profile_store)的假引擎,启用散点物化子系统。"""

    def __init__(self, db_path: Path):
        self.skill_dir = db_path.parent / "skill"
        self.profile_store = SimpleNamespace(db_path=db_path)
        self.skillhub = None
        self.calls: list[str] = []

    def update_user_interest(self, interest, *, should_commit=None):
        self.calls.append(interest.user_id)
        return SimpleNamespace(changed=True, cancelled=False, embed_items=1)


@pytest.mark.flaky(reruns=3, reruns_delay=1)
def test_service_event_trigger_submits_both_methods(tmp_path):
    engine = _FakeEngine(tmp_path / "team_profile.db")
    service = ProfileRefreshService(engine, workers=1, queue_size=4)
    recorder = _RecorderScatter()
    service.submit_scatter = recorder.submit_scatter  # 只验证投递,不跑真重算
    try:
        assert service.request("client-x")
        assert service.wait_idle(timeout=3)
        time.sleep(0.05)  # 事件触发在 finalize 前,给投递一点点余量
        assert recorder.enqueued == [("client-x", "tsne"), ("client-x", "umap")]
    finally:
        assert service.stop(timeout=3)


def test_service_submit_scatter_dedup(tmp_path):
    engine = _FakeEngine(tmp_path / "team_profile.db")
    service = ProfileRefreshService(engine, workers=1, queue_size=4, autostart=False)
    started = threading.Event()
    release = threading.Event()
    seen: list[tuple[str, str]] = []

    def _blocking_recompute(user_key, method):
        seen.append((user_key, method))
        started.set()
        assert release.wait(3)

    service._recompute_scatter = _blocking_recompute
    try:
        assert service.submit_scatter("u", "tsne") is True
        assert started.wait(3)  # 派发线程已取走并进入(阻塞的)重算
        assert service.submit_scatter("u", "tsne") is True  # 在飞 → 去重
        assert service.metrics["scatter_deduped"] == 1
        release.set()
    finally:
        assert service.stop(timeout=3)
    assert seen == [("u", "tsne")]  # 只重算一次


def test_service_stop_cancels_queued_scatter_and_reports_running_thread(tmp_path):
    """回归:旧实现把 STOP 排在队尾且返回值漏看 scatter thread。

    首任务仍在执行时 stop(timeout=0) 不得谎报成功；第二个未开始任务必须从
    queue/inflight 同步取消，首任务释放后派发线程直接退出，不再继续算第二个。
    """
    engine = _FakeEngine(tmp_path / "team_profile.db")
    service = ProfileRefreshService(engine, workers=1, queue_size=4, autostart=False)
    started = threading.Event()
    release = threading.Event()
    seen: list[tuple[str, str]] = []

    def _blocking_recompute(user_key, method):
        seen.append((user_key, method))
        started.set()
        release.wait(3)

    service._recompute_scatter = _blocking_recompute
    assert service.submit_scatter("running", "tsne") is True
    assert started.wait(3)
    assert service.submit_scatter("queued", "tsne") is True

    # 运行项尚未退出，stop 必须返回 False；排队项已经同步取消并清掉去重状态。
    assert service.stop(timeout=0) is False
    assert service._scatter_thread.is_alive()
    assert service._scatter_inflight == set()

    release.set()
    assert service.stop(timeout=3) is True
    assert not service._scatter_thread.is_alive()
    assert seen == [("running", "tsne")]


def test_service_stop_discards_projection_result_that_finishes_late(
    tmp_path, monkeypatch,
):
    """运行中的投影可在 timeout 后收尾，但停机结果不得再写缓存。"""
    engine = _FakeEngine(tmp_path / "team_profile.db")
    service = ProfileRefreshService(engine, workers=1, queue_size=4, autostart=False)
    projection_started = threading.Event()
    release_projection = threading.Event()
    writes: list[tuple] = []

    class _FakeViz:
        @staticmethod
        def scatter_input_fingerprint(_user_key):
            return "fp"

        @staticmethod
        def gather_scatter_inputs(user_key, method):
            return {"user": user_key, "method": method}

    def _blocking_project(_inputs, _worker):
        projection_started.set()
        release_projection.wait(3)
        return {"points": [], "method": "tsne"}

    service._scatter_viz = _FakeViz()
    service._project_scatter = _blocking_project
    monkeypatch.setattr(R, "read_scatter_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        R, "write_scatter_cache",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    assert service.submit_scatter("running", "tsne") is True
    assert projection_started.wait(3)
    assert service.stop(timeout=0) is False
    release_projection.set()
    assert service.stop(timeout=3) is True
    assert writes == []
    assert service.metrics["scatter_materialized"] == 0


class _RecorderScatter:
    def __init__(self):
        self.enqueued: list[tuple[str, str]] = []
        self._inflight: set = set()

    def submit_scatter(self, user_key, method):
        key = (user_key, method)
        if key in self._inflight:
            return True
        self._inflight.add(key)
        self.enqueued.append(key)
        return True
