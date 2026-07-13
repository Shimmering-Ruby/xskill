"""test_team_sync_profile.py — /sync 触发用户画像刷新（Fix #1 接线验证）

证明生产路径接通：team server /sync 前 → engine.update_user_interest → 画像入库。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.recommend.engine import SkillRecommendEngine
from xskill.team.server import api as server_api
from xskill.team.server.client_registry import ClientRegistry
from xskill.team.server.skill_manifest import set_recommend_engine


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_main_skill(parent: Path, name: str):
    d = parent / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} desc\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)


class FakeEmbed:
    def __init__(self, dim=4):
        self.dim = dim

    def encode(self, text):
        v = np.zeros(self.dim, dtype=float)
        for i, ch in enumerate(text):
            v[i % self.dim] += ord(ch) % 97
        return v

    def encode_batch(self, texts):
        return np.stack([self.encode(t) for t in texts])


def _write_atom(root: Path, traj_id: str, atom_id: str, *, summary, used_skills):
    tasks = root / traj_id / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{atom_id}.json").write_text(json.dumps({
        "atom_id": atom_id, "traj_id": traj_id, "offset_start": 1, "offset_end": 2,
        "intent": "i", "summary": summary, "used_skills": used_skills, "tags": [],
    }), encoding="utf-8")


@pytest.fixture
def team_client(tmp_path):
    skill_dir = tmp_path / "skill"
    _make_main_skill(skill_dir, "s0")
    # skill 索引
    import pickle
    with open(skill_dir / ".skill_index.pkl", "wb") as f:
        pickle.dump({"skill_names": ["s0"], "embeddings": np.eye(1, 4),
                     "atom_feats": np.zeros((1, 4)), "atom_feat_present": [False],
                     "schema_version": 2}, f)
    traj_root = tmp_path / "traj"

    reg = ClientRegistry(tmp_path / "clients.db")
    eng = SkillRecommendEngine(
        config={"recommend": {"quality_ratio": 0.8, "staging_need": 3},
                "canary": {"total_samples": 3, "min_samples": 3}},
        skill_dir=skill_dir, traj_root=traj_root,
        embed_client=FakeEmbed(dim=4), profile_db=tmp_path / "profile.db",
    )
    set_recommend_engine(eng)
    server_api.init_team_context(
        join_token="tok", client_registry=reg, skill_dir=skill_dir, traj_root=traj_root,
        probability=0.2, ranked_slots=80, total_slots=100,
        register_dir=lambda p, l: None, allow_anonymous_user=True,
    )
    app = FastAPI()
    app.include_router(server_api.router)
    tc = TestClient(app)
    # 通过 /register 拿到 --name u1 派生的真实 client_id
    rr = tc.post("/api/v1/team/register", json={"token": "tok", "user_name": "u1"})
    cid = rr.json()["client_id"]
    # atom 落在 clients/<cid>/sessions（与生产 team_upload 路径一致）
    _write_atom(traj_root / "clients" / cid / "sessions", "traj_1", "atom_traj_1_0001",
                summary="fix django migration", used_skills=["s0"])
    yield eng, tc, cid
    set_recommend_engine(None)  # 清理全局，避免污染其他测试


def test_sync_populates_user_profile(team_client):
    eng, client, cid = team_client
    # sync 前无画像
    assert eng.profile_store.load(cid) is None
    # 触发 /sync（带正确 token + client_id）
    r = client.get("/api/v1/team/sync",
                   headers={"X-Xskill-Token": "tok", "X-Xskill-Client": cid})
    assert r.status_code == 200
    # sync 后画像已入库（feature_tensor 非 None —— 有 atom）
    row = eng.profile_store.load(cid)
    assert row is not None
    assert row["feature_tensor"] is not None
    assert row["used_skills"][0]["name"] == "s0"


def test_sync_idempotent_when_atoms_unchanged(team_client):
    """指纹缓存：atom 集未变时第二次 sync 不重 embed。"""
    eng, client, cid = team_client
    client.get("/api/v1/team/sync",
               headers={"X-Xskill-Token": "tok", "X-Xskill-Client": cid})
    embed_calls = {"n": 0}
    orig = eng.embed_client.encode_batch

    def counting_batch(texts):
        embed_calls["n"] += 1
        return orig(texts)
    eng.embed_client.encode_batch = counting_batch
    # atom 集未变 → 第二次 sync 的 update_user_interest 应指纹命中跳过
    client.get("/api/v1/team/sync",
               headers={"X-Xskill-Token": "tok", "X-Xskill-Client": cid})
    assert embed_calls["n"] == 0  # 未重 embed


def test_sync_inflight_dedup_serves_cached_profile(team_client):
    """同 client 的画像刷新还挂在 embed 上时，后续 /sync 不重复触发
    embed、立即用现有画像返回。"""
    import threading

    eng, client, cid = team_client
    headers = {"X-Xskill-Token": "tok", "X-Xskill-Client": cid}
    embed_started = threading.Event()
    release_embed = threading.Event()
    embed_calls = {"count": 0}
    original_encode_batch = eng.embed_client.encode_batch

    def blocking_encode_batch(texts):
        embed_calls["count"] += 1
        embed_started.set()
        assert release_embed.wait(10), "test deadlock: release never set"
        return original_encode_batch(texts)

    eng.embed_client.encode_batch = blocking_encode_batch
    first_result = {}
    first_request = threading.Thread(
        target=lambda: first_result.setdefault(
            "response",
            TestClient(client.app).get("/api/v1/team/sync", headers=headers)))
    first_request.start()
    try:
        assert embed_started.wait(10), "first sync never reached embed"
        # 第一个刷新还在 embed 上：第二个 sync 必须立即成功且不再进 embed
        second_response = client.get("/api/v1/team/sync", headers=headers)
        assert second_response.status_code == 200
        assert embed_calls["count"] == 1
    finally:
        release_embed.set()
        first_request.join(10)
    assert first_result["response"].status_code == 200
    # 在途标记已清理：atom 集变化后能再次触发刷新
    _write_atom(eng.traj_root / "clients" / cid / "sessions", "traj_2",
                "atom_traj_2_0001", summary="tune nginx", used_skills=["s0"])
    client.get("/api/v1/team/sync", headers=headers)
    assert embed_calls["count"] == 2


def test_fingerprint_not_poisoned_by_embed_failure(team_client):
    """指纹缓存只在画像 upsert 成功后写入：embed 失败后、atom 集未变，
    下一次 sync 也必须能重算出画像。"""
    eng, client, cid = team_client
    headers = {"X-Xskill-Token": "tok", "X-Xskill-Client": cid}
    original_encode_batch = eng.embed_client.encode_batch

    def failing_encode_batch(texts):
        raise RuntimeError("embed backend down")

    eng.embed_client.encode_batch = failing_encode_batch
    # 刷新失败被路由吞掉（best-effort），manifest 照常返回
    response = client.get("/api/v1/team/sync", headers=headers)
    assert response.status_code == 200
    assert eng.profile_store.load(cid) is None
    # 后端恢复：atom 集没变，也必须能重算出画像（指纹未被污染）
    eng.embed_client.encode_batch = original_encode_batch
    response = client.get("/api/v1/team/sync", headers=headers)
    assert response.status_code == 200
    profile_row = eng.profile_store.load(cid)
    assert profile_row is not None and profile_row["feature_tensor"] is not None
