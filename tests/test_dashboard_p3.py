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
        traj_root=tmp_path / "traj", probability=0.2, ranked_slots=2,
        total_slots=3, register_dir=lambda p, l: None)
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
