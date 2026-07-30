"""test_dashboard_p3.py —— P3 社交 + 画像可视化（events/通知/散点/聚类 graph）

覆盖 openspec dashboard-console-redesign P3 的 SHALL 条款：
- 3.1 events 四类埋点 + D7 扇出规则(按 traj 去重/weightscore 阈值/不自通知)
- 3.2 通知读侧:未读游标只前进;世界消息分页
- 3.3 评价口径:ux 分数段 → 好评/一般/差劲
- 3.4 画像散点:points 落盘对齐校验、t-SNE 投影下簇分离、冷启动显式标注、
      skill 向量仅用已缓存索引(D6 不现算)
- 3.5 聚类 graph:相似度阈值连边、孤立节点标冷启动、维度不一致不连边
"""
from __future__ import annotations

import pickle
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.dashboard.profile_viz import ProfileViz, profile_db_for
from xskill.events import (
    CONTRIBUTOR_MIN_WEIGHT,
    EventStore,
    skill_contributors,
    skill_main_producer,
    ux_band,
)
from xskill.pipeline import registry as R
from xskill.recommend.profile_store import ProfileStore


# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def reg_db(tmp_path):
    """registry.db + 两个用户的轨迹 + alice 对 skill-x 的达阈值贡献。"""
    db = tmp_path / "r.db"
    wd = R.register_dir(tmp_path / "wd", label="", db_path=db)
    conn = R.get_connection(db)
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,user_key) VALUES(?,?,?)",
        (wd, "traj_a1.md", "alice"))
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,user_key) VALUES(?,?,?)",
        (wd, "traj_b1.md", "bob"))
    conn.commit()
    conn.close()
    # alice 两个 atom 进 skill-x,合计 weightscore 5 ≥ 阈值
    R.record_atom_adoption(atom_id="atom_traj_a1_0001", skill="skill-x",
                           weightscore=3, was_new=True, db_path=db)
    R.record_atom_adoption(atom_id="atom_traj_a1_0002", skill="skill-x",
                           weightscore=2, was_new=True, db_path=db)
    # bob 只有 1 分的琐碎贡献,低于阈值
    R.record_atom_adoption(atom_id="atom_traj_b1_0001", skill="skill-x",
                           weightscore=1, was_new=True, db_path=db)
    return db


# ── 3.1 贡献者与扇出规则(D7) ────────────────────────────────────────

def test_contributors_sum_and_threshold(reg_db):
    c = skill_contributors("skill-x", db_path=reg_db)
    assert c == {"alice": 5}  # bob 的 ws=1 低于阈值被滤掉
    # 阈值放宽后 bob 也算贡献者
    c2 = skill_contributors("skill-x", min_weight=1, db_path=reg_db)
    assert c2 == {"alice": 5, "bob": 1}
    assert CONTRIBUTOR_MIN_WEIGHT == 3


def test_skill_main_producer_by_traj_count(reg_db):
    """主要贡献人按贡献轨迹条数，而非 weightscore 总和。"""
    # alice 1 条轨迹(2 atoms)、bob 1 条轨迹 → 并列按名字 alice 优先? 我们用 max(len, name)
    # 给 bob 再加一条轨迹，bob 应胜出
    conn = R.get_connection(reg_db)
    wd = conn.execute("SELECT id FROM watch_dirs LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,user_key) VALUES(?,?,?)",
        (wd, "traj_b2.md", "bob"))
    conn.commit()
    conn.close()
    R.record_atom_adoption(atom_id="atom_traj_b2_0001", skill="skill-x",
                           weightscore=1, was_new=True, db_path=reg_db)
    prod = skill_main_producer("skill-x", db_path=reg_db)
    assert prod is not None
    assert prod["user"] == "bob"
    assert prod["traj_count"] == 2
    assert skill_main_producer("no-such", db_path=reg_db) is None


def test_feedback_dedup_by_traj(reg_db):
    es = EventStore(reg_db)
    e1 = es.emit_feedback(actor="bob", skill="skill-x", traj_id="traj_b1",
                          score_avg=8.0, n_atoms=2, side="main", sha="abc")
    assert e1 is not None
    # 同一 (skill, traj) 再发 → 去重不重发(同轨迹多 atom 命中同 skill 只一条)
    assert es.emit_feedback(actor="bob", skill="skill-x", traj_id="traj_b1",
                            score_avg=8.0, n_atoms=3, side="main",
                            sha="abc") is None
    # 不同轨迹可以再发
    assert es.emit_feedback(actor="bob", skill="skill-x", traj_id="traj_b2",
                            score_avg=6.0, n_atoms=1, side="main",
                            sha="abc") is not None


def test_no_self_notification(reg_db):
    es = EventStore(reg_db)
    # alice 触发自己贡献的 skill-x → 事件入库(世界消息可见)但 alice 无通知
    es.emit_feedback(actor="alice", skill="skill-x", traj_id="traj_a1",
                     score_avg=9.0, n_atoms=1, side="main", sha="abc")
    assert len(es.world_feed()) == 1
    assert es.for_user("alice") == []
    assert es.unread_count("alice") == 0
    # bob 触发 → alice(达阈值贡献者)收到通知
    es.emit_feedback(actor="bob", skill="skill-x", traj_id="traj_b1",
                     score_avg=3.0, n_atoms=1, side="main", sha="abc")
    notif = es.for_user("alice")
    assert len(notif) == 1
    assert notif[0]["payload"]["band"] == "差劲"


def test_pin_event_targets_contributors_and_affected_user(reg_db):
    es = EventStore(reg_db)
    # admin 代 bob pin skill-x → 贡献者 alice + 被配置的 bob 都收通知
    es.emit_pin(actor="boss", skill="skill-x", target_user="bob",
                scope="admin")
    assert {e["kind"] for e in es.for_user("alice")} == {"pin"}
    assert {e["kind"] for e in es.for_user("bob")} == {"pin"}
    # 全局 pin('*global*' 不是用户)→ 只有贡献者收
    es.emit_pin(actor="boss", skill="skill-x", target_user="*global*",
                scope="global")
    assert len(es.for_user("alice")) == 2
    assert len(es.for_user("bob")) == 1


def test_push_edit_and_canary_events(reg_db):
    es = EventStore(reg_db)
    es.emit_push_edit(actor="bob", skill="skill-x",
                      branch="user-staging/deadbeef", ref_sha="cafe1234")
    es.emit_canary(skill="skill-x", action="promoted",
                   main_avg=6.0, staging_avg=8.0)
    notif = es.for_user("alice")
    kinds = [e["kind"] for e in notif]
    assert kinds == ["canary", "push_edit"]  # 最新在前
    pe = notif[1]["payload"]
    assert pe["branch"] == "user-staging/deadbeef"
    assert pe["ref_sha"] == "cafe1234"


def test_runner_emit_helper_resolves_actor(reg_db):
    """打分链路的埋点 helper:actor 取该 traj 的 user_key,失败静默不抛。"""
    from xskill.pipeline.runner import DirectoryWatcher
    conn = R.get_connection(reg_db)
    wd_id = conn.execute("SELECT watch_dir_id FROM trajectories LIMIT 1"
                         ).fetchone()[0]
    conn.close()
    DirectoryWatcher._emit_feedback_event(
        wd_id, "traj_b1.md", skill_name="skill-x", traj_id="traj_b1",
        scores=[8.0, 9.0], side="main", sha="abc", db_path=reg_db)
    feed = EventStore(reg_db).world_feed()
    assert len(feed) == 1
    assert feed[0]["actor"] == "bob"
    assert feed[0]["payload"]["score_avg"] == 8.5
    # alice 是达阈值贡献者 → 收到通知
    assert EventStore(reg_db).unread_count("alice") == 1


# ── 3.2 未读游标 + 分页 ──────────────────────────────────────────────

def test_unread_cursor_only_advances(reg_db):
    es = EventStore(reg_db)
    for traj in ("t1", "t2", "t3"):
        es.emit_feedback(actor="bob", skill="skill-x", traj_id=traj,
                         score_avg=8.0, n_atoms=1, side="main", sha="a")
    assert es.unread_count("alice") == 3
    ids = [e["id"] for e in es.for_user("alice")]
    es.mark_read("alice", ids[1])       # 读到第二新
    assert es.unread_count("alice") == 1
    es.mark_read("alice", 0)            # 游标只前进,倒退是 no-op
    assert es.unread_count("alice") == 1
    assert [e["read"] for e in es.for_user("alice")] == [False, True, True]


def test_world_feed_pagination(reg_db):
    es = EventStore(reg_db)
    for i in range(5):
        es.emit_feedback(actor="bob", skill="skill-x", traj_id=f"t{i}",
                         score_avg=8.0, n_atoms=1, side="main", sha="a")
    page1 = es.world_feed(limit=2)
    assert [e["traj_id"] for e in page1] == ["t4", "t3"]
    page2 = es.world_feed(limit=2, before_id=page1[-1]["id"])
    assert [e["traj_id"] for e in page2] == ["t2", "t1"]


# ── 3.3 评价口径 ─────────────────────────────────────────────────────

def test_ux_band_boundaries():
    assert ux_band(10) == ux_band(7) == "好评"
    assert ux_band(6.9) == ux_band(5) == ux_band(4.1) == "一般"
    assert ux_band(4) == ux_band(1) == "差劲"


# ── 3.4 ProfileStore points 落盘 + 散点 ─────────────────────────────

def _seed_profile(pdb: Path):
    """alice:两簇各 3 点(git/docker);bob:同向 mean;carol:反向孤立。"""
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
                 points=pts, point_meta=meta)
    store.upsert("bob", feature_tensor=centers, mean_tensor=pts.mean(0),
                 used_skills=[{"name": "sk1", "use_count": 1}],
                 points=pts[:2], point_meta=meta[:2])
    store.upsert("carol", feature_tensor=None, mean_tensor=-pts.mean(0),
                 used_skills=[])
    return store


def test_points_meta_alignment_enforced(tmp_path):
    store = ProfileStore(tmp_path / "p.db")
    pts = np.ones((2, 4))
    with pytest.raises(ValueError):
        store.upsert("u", feature_tensor=None, mean_tensor=None,
                     used_skills=[], points=pts, point_meta=[{"atom_id": "a"}])
    with pytest.raises(ValueError):  # 只给 points 不给 meta
        store.upsert("u", feature_tensor=None, mean_tensor=None,
                     used_skills=[], points=pts, point_meta=None)


def test_points_roundtrip_and_cold_start(tmp_path):
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    store = ProfileStore(pdb)
    got = store.load_points("alice")
    assert got["points"].shape == (6, 4)
    assert len(got["meta"]) == 6
    # 冷启动:有行无点
    store.upsert("dave", feature_tensor=None, mean_tensor=None, used_skills=[])
    assert store.load_points("dave")["points"] is None
    assert store.load_points("nobody") is None


def test_scatter_clusters_and_labels(tmp_path):
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    sc = ProfileViz(pdb).user_scatter("alice")
    assert len(sc["points"]) == 6 and len(sc["centers"]) == 2
    # 簇归属:前 3 点一簇、后 3 点另一簇;簇语义名=簇内 top tag
    assign = [p["cluster"] for p in sc["points"]]
    assert len(set(assign[:3])) == 1 and len(set(assign[3:])) == 1
    assert assign[0] != assign[3]
    assert {c["label"] for c in sc["clusters"]} == {"git", "docker"}
    assert sc["method"] == "tsne"
    # t-SNE 邻域保持:两簇在 2D 投影里也应分得开(簇间距 > 簇内散布),
    # 不是随手混在一起——这是从 PCA 换成 t-SNE 要保住的核心可视化诉求。
    xy = np.array([[p["x"], p["y"]] for p in sc["points"]])
    ga, gb = xy[:3].mean(axis=0), xy[3:].mean(axis=0)
    within = max(np.linalg.norm(xy[:3] - ga, axis=1).mean(),
                 np.linalg.norm(xy[3:] - gb, axis=1).mean())
    between = np.linalg.norm(ga - gb)
    assert between > 1.5 * within


def test_stratified_sample_keeps_small_cluster():
    from xskill.dashboard.profile_viz import _stratified_sample_indices
    # 990 个属簇0、10 个属簇1(小簇);抽到 cap=100 也不能把小簇整个抽没
    assignment = np.array([0] * 990 + [1] * 10)
    sel = _stratified_sample_indices(assignment, 100)
    assert len(sel) <= 100 + 2  # ≤5 簇,配额总和约等于 cap
    assert {int(assignment[i]) for i in sel} == {0, 1}
    assert sel == sorted(sel)  # 升序
    # n<=cap → 全返回,不抽样
    assert _stratified_sample_indices(np.array([0, 1, 0]), 100) == [0, 1, 2]


def test_scatter_stratified_sampling(tmp_path):
    """点数超上限 → 按兴趣中心分层抽样,shown≤cap、标 sampled、三簇都留代表点。"""
    from xskill.dashboard.profile_viz import _SCATTER_MAX_POINTS
    pdb = tmp_path / "p.db"
    store = ProfileStore(pdb)
    rng = np.random.default_rng(0)
    dim = 8
    centers = np.eye(3, dim)
    pts, meta = [], []
    for c in range(3):
        for k in range(200):  # 3×200 = 600 > cap
            v = centers[c] + 0.15 * rng.standard_normal(dim)
            v = v / np.linalg.norm(v)
            pts.append(v)
            meta.append({"atom_id": f"a_{c}_{k}", "summary": f"s{c}{k}",
                         "ux": 7, "tags": [f"t{c}"]})
    pts = np.asarray(pts)
    store.upsert("u", feature_tensor=centers, mean_tensor=pts.mean(0),
                 used_skills=[], points=pts, point_meta=meta)
    sc = ProfileViz(pdb).user_scatter("u")
    assert sc["sampled"] is True and sc["total"] == 600
    assert sc["shown"] == len(sc["points"]) <= _SCATTER_MAX_POINTS
    assert len(sc["centers"]) == 3  # 兴趣中心全保留
    assert {p["cluster"] for p in sc["points"]} == {0, 1, 2}  # 三簇都有代表点


def test_scatter_no_sampling_under_cap(tmp_path):
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    sc = ProfileViz(pdb).user_scatter("alice")
    assert sc["sampled"] is False and sc["total"] == 6 and sc["shown"] == 6


def test_scatter_umap_method(tmp_path):
    """UMAP 投影:同一份数据换降维算法,簇仍分得开、确定性、method 回显 umap。"""
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    viz = ProfileViz(pdb)
    sc = viz.user_scatter("alice", method="umap")
    assert sc["method"] == "umap"
    assert len(sc["points"]) == 6 and len(sc["centers"]) == 2
    xy = np.array([[p["x"], p["y"]] for p in sc["points"]])
    ga, gb = xy[:3].mean(axis=0), xy[3:].mean(axis=0)
    within = max(np.linalg.norm(xy[:3] - ga, axis=1).mean(),
                 np.linalg.norm(xy[3:] - gb, axis=1).mean())
    between = np.linalg.norm(ga - gb)
    assert between > 1.5 * within
    # 确定性:无随机数,两次坐标一致
    xy2 = np.array([[p["x"], p["y"]] for p in viz.user_scatter("alice", method="umap")["points"]])
    assert np.allclose(xy, xy2)


def test_scatter_umap_many_points_no_blowup(tmp_path):
    """回归:3×100=300 点(命中 _SCATTER_MAX_POINTS 上限,不触发抽样)下 UMAP 布局不发散,三簇仍分得开。"""
    pdb = tmp_path / "p.db"
    store = ProfileStore(pdb)
    rng = np.random.default_rng(1)
    dim = 8
    centers = np.eye(3, dim)
    points, point_meta = [], []
    for cluster_id in range(3):
        for atom_id in range(100):  # 3×100 = 300 == _SCATTER_MAX_POINTS,不抽样
            vector = centers[cluster_id] + 0.15 * rng.standard_normal(dim)
            vector = vector / np.linalg.norm(vector)
            points.append(vector)
            point_meta.append({"atom_id": f"a_{cluster_id}_{atom_id}",
                               "summary": f"s{cluster_id}{atom_id}", "ux": 7,
                               "tags": [f"t{cluster_id}"]})
    points = np.asarray(points)
    store.upsert("u", feature_tensor=centers, mean_tensor=points.mean(0),
                 used_skills=[], points=points, point_meta=point_meta)
    scatter = ProfileViz(pdb).user_scatter("u", method="umap")
    assert scatter["sampled"] is False and scatter["total"] == 300
    xy = np.array([[p["x"], p["y"]] for p in scatter["points"]])
    assignment = np.array([p["cluster"] for p in scatter["points"]])
    cluster_means = [xy[assignment == cluster_id].mean(axis=0) for cluster_id in range(3)]
    cluster_spreads = [np.linalg.norm(xy[assignment == cluster_id] - cluster_means[cluster_id],
                                      axis=1).mean() for cluster_id in range(3)]
    for i in range(3):
        for j in range(i + 1, 3):
            between = np.linalg.norm(cluster_means[i] - cluster_means[j])
            assert between > 2.0 * max(cluster_spreads[i], cluster_spreads[j])
    # 爆炸时单点会被甩到离质心几十倍远;正常收敛的全体坐标半径应与簇间距同量级。
    overall_radius = np.linalg.norm(xy - xy.mean(axis=0), axis=1)
    assert overall_radius.max() < 20.0 * np.median(overall_radius)


def test_umap_direct_fleet_scale_clusters_separate():
    """直调 _UMAP2D(绕过看板 300 点抽样上限):6 簇不均匀、更高维、n=520,全批斥力不随规模爆炸。"""
    from xskill.dashboard.profile_viz import _UMAP2D
    cluster_sizes = [180, 130, 90, 60, 40, 20]
    dim = 32
    rng = np.random.default_rng(3)
    cluster_centers = np.eye(len(cluster_sizes), dim)
    points, labels = [], []
    for cluster_id, size in enumerate(cluster_sizes):
        for _ in range(size):
            vector = cluster_centers[cluster_id] + 0.20 * rng.standard_normal(dim)
            points.append(vector / np.linalg.norm(vector))
            labels.append(cluster_id)
    points = np.asarray(points)
    labels = np.asarray(labels)
    coords = _UMAP2D(n_epochs=250).fit(points)
    radius = np.linalg.norm(coords - coords.mean(axis=0), axis=1)
    assert radius.max() < 40.0  # 爆炸态观测半径达三百余,收敛态应在数十以内
    cluster_means = [coords[labels == cluster_id].mean(axis=0)
                     for cluster_id in range(len(cluster_sizes))]
    cluster_spreads = [np.linalg.norm(coords[labels == cluster_id] - cluster_means[cluster_id],
                                      axis=1).mean() for cluster_id in range(len(cluster_sizes))]
    for i in range(len(cluster_sizes)):
        for j in range(i + 1, len(cluster_sizes)):
            between = np.linalg.norm(cluster_means[i] - cluster_means[j])
            assert between > 3.0 * max(cluster_spreads[i], cluster_spreads[j])
    coords_again = _UMAP2D(n_epochs=250).fit(points)
    assert np.allclose(coords, coords_again)


def test_scatter_unknown_method_rejected(tmp_path):
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    with pytest.raises(ValueError):
        ProfileViz(pdb).user_scatter("alice", method="pca")


def test_scatter_cold_start_and_missing(tmp_path):
    pdb = tmp_path / "p.db"
    store = _seed_profile(pdb)
    store.upsert("dave", feature_tensor=None, mean_tensor=None, used_skills=[])
    viz = ProfileViz(pdb)
    d = viz.user_scatter("dave")
    assert d["points"] == [] and "冷启动" in d["note"]
    with pytest.raises(KeyError):
        viz.user_scatter("nobody")


def test_scatter_skill_vec_only_from_index(tmp_path):
    """D6:skill ▲ 只用 .skill_index.pkl 缓存,无索引不现算不显示。"""
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    skills = tmp_path / "skills"
    skills.mkdir()
    # 无索引 → 无 skill 三角
    assert ProfileViz(pdb, skill_dir=skills).user_scatter("alice")["skills"] == []
    # 有索引且维度匹配 → 投影出现
    idx = {"skill_names": ["sk1"],
           "embeddings": np.array([[0.7, 0.7, 0, 0]], dtype=float)}
    (skills / ".skill_index.pkl").write_bytes(pickle.dumps(idx))
    out = ProfileViz(pdb, skill_dir=skills).user_scatter("alice")["skills"]
    assert [s["name"] for s in out] == ["sk1"]
    # 维度不匹配(换过 embedding 模型)→ 不画,不报错
    idx2 = {"skill_names": ["sk1"], "embeddings": np.ones((1, 7))}
    (skills / ".skill_index.pkl").write_bytes(pickle.dumps(idx2))
    assert ProfileViz(pdb, skill_dir=skills).user_scatter("alice")["skills"] == []


# ── 3.4bis 三方(skillhub)skill ▲ + source 契约 ────────────────────────

class _FakeEmbed:
    """dashboard 无 embed_client;这里只给 SkillHub 落盘缓存用(维度对齐 points=4)。"""

    def __init__(self, dim=4):
        self.dim = dim
        self.model = "fake-embed"

    def encode_batch(self, texts):
        return np.stack([self.encode(t) for t in texts])

    def encode(self, text):
        v = np.zeros(self.dim, dtype=float)
        for i, ch in enumerate(text):
            v[i % self.dim] += ord(ch) % 97
        return v


def _write_hub_skill(hub_dir: Path, rel: str, desc: str):
    d = hub_dir / rel
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {rel}\ndescription: {desc}\n---\n# {rel}\n", encoding="utf-8")


def _seed_alice_with_skills(pdb: Path, used_skills: list[dict]):
    """alice 两簇各 3 点(dim=4),used_skills 自定,便于挂三方 skill。"""
    store = ProfileStore(pdb)
    a = np.array([[1, 0.05 * i, 0, 0] for i in range(3)], dtype=float)
    b = np.array([[0.05 * i, 1, 0, 0] for i in range(3)], dtype=float)
    pts = np.vstack([a, b])
    pts /= np.linalg.norm(pts, axis=1, keepdims=True)
    centers = np.vstack([a.mean(0), b.mean(0)])
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    meta = [{"atom_id": f"atom_{i}", "summary": f"s{i}", "ux": 8,
             "tags": ["git"] if i < 3 else ["docker"]} for i in range(6)]
    store.upsert("alice", feature_tensor=centers, mean_tensor=pts.mean(0),
                 used_skills=used_skills, points=pts, point_meta=meta)


def test_skillhub_index_for_locates_sibling(tmp_path):
    """三方缓存旁推定位:registry 同级 skillhub_skills/.skillhub_index.pkl。"""
    from xskill.dashboard.profile_viz import skillhub_index_for
    p = skillhub_index_for(tmp_path / "registry.db")
    assert p == tmp_path / "skillhub_skills" / ".skillhub_index.pkl"


def test_scatter_marks_skillhub_source(tmp_path):
    """SkillHub.index() 落盘缓存 → 用户用过的三方 skill 画成 ▲、带 source=skillhub、
    有坐标;自产 skill 仍 source=native(不回归)。writer↔reader 契约端到端。"""
    from xskill.recommend.skillhub import SkillHub
    pdb = tmp_path / "p.db"
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "linter", "python lint helper")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=_FakeEmbed(dim=4))
    hub_id = hub.index()[0]["name"]
    assert hub.index_cache_path.is_file()  # 算向量的同时落盘缓存(供 dashboard 读)
    _seed_alice_with_skills(pdb, [{"name": "sk1", "use_count": 3},
                                  {"name": hub_id, "use_count": 5}])
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / ".skill_index.pkl").write_bytes(pickle.dumps(
        {"skill_names": ["sk1"], "embeddings": np.array([[0.7, 0.7, 0, 0]], dtype=float)}))
    out = ProfileViz(pdb, skill_dir=skills,
                     skillhub_index=hub.index_cache_path).user_scatter("alice")["skills"]
    by = {s["name"]: s for s in out}
    assert set(by) == {"sk1", hub_id}
    assert by["sk1"]["source"] == "native"
    assert by[hub_id]["source"] == "skillhub"
    assert by[hub_id]["use_count"] == 5
    for s in out:  # 都有坐标
        assert isinstance(s["x"], float) and isinstance(s["y"], float)


def test_scatter_skillhub_missing_cache_not_drawn(tmp_path):
    """D6:三方缓存缺失 → 三方 skill 不出现、不报错、不现算;自产 ▲ 不回归。"""
    pdb = tmp_path / "p.db"
    _seed_alice_with_skills(pdb, [{"name": "sk1", "use_count": 3},
                                  {"name": "ghosthub@zzz", "use_count": 2}])
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / ".skill_index.pkl").write_bytes(pickle.dumps(
        {"skill_names": ["sk1"], "embeddings": np.array([[0.7, 0.7, 0, 0]], dtype=float)}))
    out = ProfileViz(pdb, skill_dir=skills,
                     skillhub_index=tmp_path / "nope.pkl").user_scatter("alice")["skills"]
    assert [s["name"] for s in out] == ["sk1"]
    assert out[0]["source"] == "native"


def test_scatter_skillhub_dim_mismatch_skipped(tmp_path):
    """三方缓存维度与 points 不一致(换过 embedding 模型)→ 跳过,不画,不报错。"""
    pdb = tmp_path / "p.db"
    _seed_alice_with_skills(pdb, [{"name": "hub@x", "use_count": 1}])
    cache = tmp_path / "hub.pkl"
    cache.write_bytes(pickle.dumps(
        {"skill_names": ["hub@x"], "embeddings": np.ones((1, 7)), "model": "m"}))
    assert ProfileViz(pdb, skillhub_index=cache).user_scatter("alice")["skills"] == []


# ── 3.5 聚类 graph ───────────────────────────────────────────────────

def test_cluster_graph_edges_and_isolated(tmp_path):
    pdb = tmp_path / "p.db"
    _seed_profile(pdb)
    g = ProfileViz(pdb).cluster_graph()
    nodes = {n["user"]: n for n in g["nodes"]}
    assert nodes["alice"]["atoms"] == 6 and nodes["bob"]["atoms"] == 2
    assert not nodes["alice"]["isolated"] and not nodes["bob"]["isolated"]
    assert nodes["carol"]["isolated"]  # 反向 mean,无边 → 冷启动
    assert len(g["edges"]) == 1
    e = g["edges"][0]
    assert {e["source"], e["target"]} == {"alice", "bob"}
    assert e["sim"] > 0.6
    assert e["common_skills"] == ["sk1"]
    assert "git" in e["common_tags"]


def test_cluster_graph_dim_mismatch_no_edge(tmp_path):
    """换过 embedding 模型导致维度不一致 → 不可比,不连边,不崩。"""
    pdb = tmp_path / "p.db"
    store = ProfileStore(pdb)
    store.upsert("u4", feature_tensor=None, mean_tensor=np.ones(4),
                 used_skills=[])
    store.upsert("u8", feature_tensor=None, mean_tensor=np.ones(8),
                 used_skills=[])
    g = ProfileViz(pdb).cluster_graph()
    assert g["edges"] == []
    assert all(n["isolated"] for n in g["nodes"])


# ── 端点接线(scatter 公开敏感区 / events·cluster-graph 控制面) ──────

def test_scatter_endpoint(tmp_path):
    from xskill.dashboard.router import build_dashboard_router
    db = tmp_path / "r.db"
    R.get_connection(db).close()  # 建表
    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=db))
    c = TestClient(app)
    # 画像库不存在 → 404(诚实报缺,不空转)
    assert c.get("/api/v1/dashboard/user/alice/scatter").status_code == 404
    _seed_profile(profile_db_for(db))
    r = c.get("/api/v1/dashboard/user/alice/scatter")
    assert r.status_code == 200 and len(r.json()["points"]) == 6
    assert r.json()["method"] == "tsne"  # 默认 t-SNE
    # method=umap 换算法,同一批数据
    ru = c.get("/api/v1/dashboard/user/alice/scatter?method=umap")
    assert ru.status_code == 200 and ru.json()["method"] == "umap"
    assert len(ru.json()["points"]) == 6
    # 未知算法 → 400
    assert c.get("/api/v1/dashboard/user/alice/scatter?method=pca").status_code == 400
    assert c.get("/api/v1/dashboard/user/ghost/scatter").status_code == 404


@pytest.fixture()
def console_env(tmp_path):
    """auth + console app(alice=普通用户,boss=admin)。"""
    from xskill.dashboard.auth import (
        build_auth_router, configure_auth, ensure_dashboard_secret,
    )
    from xskill.dashboard.console import build_console_router
    from xskill.team.server.api import init_team_context
    from xskill.team.server.client_registry import ClientRegistry

    skills = tmp_path / "skills"
    skills.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=skills, check=True)
    db = tmp_path / "r.db"
    R.get_connection(db).close()
    reg = ClientRegistry(tmp_path / "c.db")
    cid = reg.register(user_name="alice")
    token = reg.ensure_dashboard_token(cid)
    init_team_context(
        join_token="jt", client_registry=reg, skill_dir=skills,
        traj_root=tmp_path / "traj", register_dir=lambda p, l: None)
    # 槽位改由现取 live config(热生效),不再走 init_team_context 快照
    from xskill.api import app as app_mod
    app_mod._config = {"team": {"server": {"skill_slots": 3, "ranked_slots": 2}}}
    configure_auth(
        secret=ensure_dashboard_secret(tmp_path / "sec.json"),
        admins=["boss"], admin_password="pw",
        registry_provider=lambda: reg)
    app = FastAPI()
    app.include_router(build_auth_router())
    app.include_router(build_console_router(db_path=db))
    alice = TestClient(app)
    assert alice.post("/api/v1/dashboard/login",
                      json={"user_name": "alice", "secret": token}
                      ).status_code == 200
    boss = TestClient(app)
    assert boss.post("/api/v1/dashboard/login",
                     json={"user_name": "boss", "secret": "pw"}
                     ).status_code == 200
    return {"app": app, "alice": alice, "boss": boss, "db": db}


def test_events_endpoints_auth_and_flow(console_env):
    anon = TestClient(console_env["app"])
    assert anon.get("/api/v1/dashboard/events").status_code == 401
    alice, db = console_env["alice"], console_env["db"]
    EventStore(db).emit(kind="canary", skill="sk",
                        payload={"action": "promoted"}, targets=["alice"])
    me = alice.get("/api/v1/dashboard/events?scope=me").json()
    assert me["unread"] == 1 and len(me["events"]) == 1
    world = alice.get("/api/v1/dashboard/events").json()
    assert world["scope"] == "world" and len(world["events"]) == 1
    last = me["events"][0]["id"]
    assert alice.post("/api/v1/dashboard/events/read",
                      json={"last_id": last}).status_code == 200
    assert alice.get("/api/v1/dashboard/events/unread").json()["count"] == 0


def test_pin_via_endpoint_emits_event(console_env):
    """POST /my/prefs pin → pin 事件入库;block 不发事件。"""
    alice, db = console_env["alice"], console_env["db"]
    r = alice.post("/api/v1/dashboard/my/prefs",
                   json={"skill_name": "some-skill", "action": "pin"})
    assert r.status_code == 200
    feed = EventStore(db).world_feed()
    assert [e["kind"] for e in feed] == ["pin"]
    assert feed[0]["actor"] == "alice"
    alice.post("/api/v1/dashboard/my/prefs",
               json={"skill_name": "other", "action": "block"})
    assert len(EventStore(db).world_feed()) == 1  # block 不是社交事件


def test_frontend_shell_has_p3_components():
    """壳页面含 P3 容器:全局铃铛/toast/世界消息/画像详情/聚类图/散点 tooltip;
    取数脚本接了事件与可视化端点;Web Notifications 有能力探测(D10)。"""
    static = Path("src/xskill/dashboard/static")
    html = (static / "index.html").read_text(encoding="utf-8")
    for el in ('id="bell-wrap"', 'id="bell-badge"', 'id="toasts"',
               'id="world-feed"', 'id="user-profile"', 'id="cluster-graph"',
               'id="scatter-tip"', 'id="bell-sysnotif"'):
        assert el in html, f"missing {el}"
    js = (static / "app.js").read_text(encoding="utf-8")
    assert "api/v1/dashboard/events" in js
    assert "/scatter" in js
    assert "admin/cluster-graph" in js
    assert "'Notification' in window" in js  # HTTPS 能力分层探测
    assert "sc-hull" in js and "convexHull" in js  # 散点簇凸包描边
    assert "💡" in js and "SKILL:" in js  # 兴趣点灯泡+tag 词图例 / SKILL: 前缀
    # t-SNE/UMAP 切换:URL 路径检测 + 切换按钮 + method query param
    assert "SCATTER_METHOD" in js and "scatter-method" in js
    assert "method=" in js and "umap" in js.lower()
    # 新增 Tailwind 类已进编译产物(BUILD.md 流程),不能只写在 JS 里没编译
    for cls in ("first\\:mt-0", ".space-y-1>"):
        assert cls in html, f"class {cls} not compiled into twcss"


def test_cluster_graph_endpoint_admin_only(console_env):
    alice, boss, db = (console_env["alice"], console_env["boss"],
                       console_env["db"])
    assert alice.get("/api/v1/dashboard/admin/cluster-graph"
                     ).status_code == 403
    # 画像库不存在 → 404
    assert boss.get("/api/v1/dashboard/admin/cluster-graph"
                    ).status_code == 404
    _seed_profile(profile_db_for(db))
    r = boss.get("/api/v1/dashboard/admin/cluster-graph")
    assert r.status_code == 200
    assert {n["user"] for n in r.json()["nodes"]} == {"alice", "bob", "carol"}


def test_scatter_endpoint_resolves_user_name_to_client_id(tmp_path):
    """#97:画像按 client_id 存,看板行 uid 对命名用户是 user_name——端点须按名回退。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from xskill.dashboard.router import build_dashboard_router
    from xskill.team.server.client_registry import ClientRegistry

    registry_db = tmp_path / "registry.db"
    registry_db.touch()
    named_client_id = ClientRegistry(
        tmp_path / "team_clients.db").register(user_name="m00947023")
    profile_store = ProfileStore(tmp_path / "team_profile.db")
    pts = np.eye(4, dtype=float)[:3]
    meta = [{"atom_id": f"atom_x_{i:04d}", "summary": "s", "traj_id": "x",
             "ts": ""} for i in range(3)]
    profile_store.upsert(named_client_id, feature_tensor=pts[:1],
                         mean_tensor=pts.mean(0),
                         used_skills=[], points=pts, point_meta=meta)

    app = FastAPI()
    app.include_router(build_dashboard_router(db_path=registry_db))
    client = TestClient(app)

    by_name = client.get("/api/v1/dashboard/user/m00947023/scatter?method=tsne")
    assert by_name.status_code == 200, by_name.text
    assert len(by_name.json()["points"]) == 3

    by_client_id = client.get(f"/api/v1/dashboard/user/{named_client_id}/scatter")
    assert by_client_id.status_code == 200

    unknown = client.get("/api/v1/dashboard/user/nobody/scatter")
    assert unknown.status_code == 404
