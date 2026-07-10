"""为 P1 行为验收造一套演示数据（独立 XSKILL_HOME，不碰真实 ~/.xskill）。"""
import json
import pickle
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1
            else "/tmp/xskill-dashboard-demo-home")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import shutil
if ROOT.exists():
    shutil.rmtree(ROOT)
ROOT.mkdir(parents=True)
DB = ROOT / "registry.db"

from xskill.pipeline.registry import (
    get_connection, record_atom_adoption, record_canary_decision,
    record_recommendation, record_usage)

# ── watch dirs + 轨迹 + 原子 ────────────────────────────────────────
conn = get_connection(DB)
users = [("alice", 6), ("bob", 4), ("m0032", 3)]
now = datetime.now(timezone.utc)
wd_id = 0
for uname, n_traj in users:
    wd_id += 1
    d = ROOT / "uploads" / uname
    d.mkdir(parents=True)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(?,?,?,?)",
                 (wd_id, str(d), uname, "team_client"))
    for t in range(n_traj):
        tid = f"traj_{uname}_{t:02d}"
        lines = [f"user: 需求 {t}-{i}" if i % 8 == 0 else
                 f"assistant: 执行步骤 {i}（工具调用与输出略）"
                 for i in range(1, 61)]
        (d / f"{tid}.md").write_text("\n".join(lines), encoding="utf-8")
        status = "done" if t % 5 else ("splitting" if uname == "alice" and t == 0 else "done")
        harness = "claude_code" if (t + wd_id) % 3 else "codex"
        model = ["dsv4", "sonnet-5", "k2"][(t + wd_id) % 3]
        n_atoms = 3
        conn.execute(
            "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted,"
            "source_harness,source_model,user_key,discovered_at) VALUES(?,?,?,?,?,?,?,?)",
            (wd_id, f"{tid}.md", status, n_atoms, harness, model, uname,
             (now - timedelta(days=t)).strftime("%Y-%m-%d %H:%M:%S")))
        tasks = d / tid / "tasks"
        tasks.mkdir(parents=True)
        ids = [f"atom_{tid}_{i:04d}" for i in range(1, n_atoms + 1)]
        for i, aid in enumerate(ids):
            (tasks / f"{aid}.json").write_text(json.dumps({
                "atom_id": aid, "traj_id": tid,
                "offset_start": 1 + i * 20, "offset_end": 21 + i * 20,
                "intent": f"{uname} 的意图 {t}-{i}：配置 nginx 子路径反代" if i == 0
                          else f"{uname} 的意图 {t}-{i}：修复单测",
                "summary": "agent 完成该意图（演示数据）",
                "tags": ["nginx", "部署"] if i == 0 else ["单测", "python"],
                "used_skills": ["nginx-subpath-proxy"] if i == 0 else [],
                "pre_atom_id": ids[i-1] if i > 0 else None,
                "post_atom_id": ids[i+1] if i < n_atoms - 1 else None,
                "source_model": model,
            }, ensure_ascii=False), encoding="utf-8")
conn.commit()
conn.close()

# ── skill 仓（main+staging 历史 + 被拒 ref）────────────────────────
SKILL = ROOT / "skill"
def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd)] + list(args),
                   capture_output=True, text=True, check=True)

def mk_skill(name, desc, commits, with_staging=False):
    sub = SKILL / name
    sub.mkdir(parents=True)
    git(sub, "init", "-q", "-b", "main")
    git(sub, "config", "user.email", "x@x"); git(sub, "config", "user.name", "xskill")
    for i, msg in enumerate(commits):
        (sub / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  version: {i+1}\n---\n# v{i+1}\n",
            encoding="utf-8")
        git(sub, "add", "."); git(sub, "commit", "-q", "-m", msg)
    if with_staging:
        git(sub, "checkout", "-q", "-b", "staging")
        (sub / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc} (staging 增强)\n---\n# staging\n",
            encoding="utf-8")
        git(sub, "add", "."); git(sub, "commit", "-q", "-m", "staging: 检索缓存与条款别名")
        git(sub, "checkout", "-q", "main")
    return sub

s1 = mk_skill("nginx-subpath-proxy", "nginx 单文件配置子路径反代到内网端口",
              ["v1: 初版蒸馏", "v2: 补 rewrite 规则", "v3: 静态资源相对路径"],
              with_staging=True)
s2 = mk_skill("pytest-retry-fix", "flaky 单测重试与隔离修复手法", ["v1: 初版蒸馏"])

# 一次真实的"拒绝"：走 discard_staging 留 refs/rejected
from xskill.canary import discard_staging, staging_sha, main_sha
git(s2, "checkout", "-q", "-b", "staging")
(s2 / "SKILL.md").write_text("---\nname: pytest-retry-fix\ndescription: bad rewrite\n---\n", encoding="utf-8")
git(s2, "add", "."); git(s2, "commit", "-q", "-m", "staging: 表格解析重写(劣化)")
rej_sha = staging_sha(s2)
git(s2, "checkout", "-q", "main")
discard_staging(s2)
record_canary_decision(skill="pytest-retry-fix", action="rejected",
                       main_avg=7.3, staging_avg=5.9, main_samples=3,
                       staging_samples=3, age_days=2.0,
                       main_sha=main_sha(s2) or "", staging_sha=rej_sha or "",
                       db_path=DB)
record_canary_decision(skill="nginx-subpath-proxy", action="promoted",
                       main_avg=7.0, staging_avg=8.1, main_samples=4,
                       staging_samples=4, age_days=3.0,
                       main_sha=main_sha(s1) or "",
                       staging_sha=staging_sha(s1) or "", db_path=DB)

# ── 使用打分（.ux_scores.jsonl）────────────────────────────────────
import random
random.seed(7)
m_sha = main_sha(s1)
st_sha = staging_sha(s1)
with (s1 / ".ux_scores.jsonl").open("w", encoding="utf-8") as f:
    day = now - timedelta(days=14)
    i = 0
    for uname, n_traj in users:
        for t in range(n_traj):
            tid = f"traj_{uname}_{t:02d}"
            side = "staging" if i % 3 == 0 else "main"
            base = 8.3 if side == "staging" else 7.6
            f.write(json.dumps({
                "atom_id": f"atom_{tid}_0001", "skill_name": "nginx-subpath-proxy",
                "side": side, "commit_sha": st_sha if side == "staging" else m_sha,
                "score": round(base + random.uniform(-0.8, 0.8), 1),
                "reasons": "", "user_model": ["dsv4", "sonnet-5", "k2"][i % 3],
                "scored_at": (day + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            }) + "\n")
            i += 1

# ── candidates（孵化进度）────────────────────────────────────────
cand = SKILL / "traj2skill-release"
cand.mkdir(parents=True)
(cand / ".candidates.yml").write_text(
    "candidates:\n- atom_id: atom_traj_alice_00_0002\n  weightscore: 4\n"
    "- atom_id: atom_traj_bob_00_0002\n  weightscore: 3\n"
    "- atom_id: atom_traj_m0032_00_0002\n  weightscore: 2\n", encoding="utf-8")

# ── adoption + 推荐曝光 + 成本 ─────────────────────────────────────
record_atom_adoption(atom_id="atom_traj_alice_00_0001", skill="nginx-subpath-proxy",
                     weightscore=4, was_new=True, db_path=DB)
record_atom_adoption(atom_id="atom_traj_bob_00_0001", skill="nginx-subpath-proxy",
                     weightscore=3, was_new=True, db_path=DB)
record_atom_adoption(atom_id="atom_traj_gone_0001", skill="nginx-subpath-proxy",
                     weightscore=2, was_new=True, db_path=DB)  # 断链演示
for u, _ in users:
    record_recommendation(client_id=u, skill="nginx-subpath-proxy", side="main",
                          bucket="recommended", sha=m_sha or "", db_path=DB)
    record_recommendation(client_id=u, skill="pytest-retry-fix", side="main",
                          bucket="recommended", sha="", db_path=DB)
for step, cost in [("split", 0.8), ("cluster", 1.4), ("skill_edit", 1.2)]:
    record_usage(step=step, model="deepseek-v4", prompt=120000, completion=30000,
                 total=150000, cost_usd=cost, price_source="config", db_path=DB)

# ── team_clients.db（连接状态）─────────────────────────────────────
cdb = sqlite3.connect(str(ROOT / "team_clients.db"))
cdb.execute("CREATE TABLE clients (client_id TEXT PRIMARY KEY, label TEXT DEFAULT '',"
            " hostname TEXT DEFAULT '', user_name TEXT, joined_at TEXT, last_seen TEXT)")
seen = [now, now - timedelta(minutes=3), now - timedelta(days=2)]
for (u, _), ls in zip(users, seen):
    cdb.execute("INSERT INTO clients VALUES(?,?,?,?,?,?)",
                (f"cid-{u}", u, "host", u, "2026-06-01 00:00:00",
                 ls.strftime("%Y-%m-%d %H:%M:%S")))
cdb.commit(); cdb.close()

# ── P3 画像 + events mini 样例（散点③ / 聚类图 / 世界消息·铃铛）────────
# 画像库 team_profile.db 与 registry.db 同目录（profile_viz.profile_db_for
# 旁推约定）。三个用户:alice/bob 主题重叠→聚类图有边;m0032 孤立→冷启动灰点。
import numpy as np
from xskill.recommend.profile_store import ProfileStore
from xskill.events import EventStore

np.random.seed(11)
DIM = 16  # 画像点维度 = skill 索引 embedding 维度（散点 ▲ 要求一致，否则不画）


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# 固定主题基向量（git/docker/frontend/data 各一个中心方向，16 维近似正交）
_THEMES = {name: _unit(np.random.randn(DIM))
           for name in ("git", "docker", "frontend", "data")}
_TAGS = {"git": ["git", "rebase"], "docker": ["docker", "compose"],
         "frontend": ["frontend", "react"], "data": ["data", "etl"]}
_SUMS = {"git": "解决 rebase 冲突并保留双方改动",
         "docker": "修 compose 网络与卷挂载失败",
         "frontend": "React 组件状态与样式回归修复",
         "data": "ETL 管道去重与增量拉取"}


def _cluster_points(theme, n, spread=0.32):
    """围绕主题中心撒 n 个已 L2 归一的原子点（分簇明显）。"""
    pts = _THEMES[theme] + spread * np.random.randn(n, DIM)
    return np.vstack([_unit(p) for p in pts])


PROFILE_DB = ROOT / "team_profile.db"
pstore = ProfileStore(PROFILE_DB)


def _seed_profile(user, theme_counts, ux_cycle):
    pts_list, meta, centers = [], [], []
    k = 0
    for theme, n in theme_counts:
        centers.append(_THEMES[theme])
        for row in _cluster_points(theme, n):
            k += 1
            pts_list.append(row)
            meta.append({"atom_id": f"atom_traj_{user}_{k:02d}_0001",
                         "summary": _SUMS[theme], "ux": ux_cycle[k % len(ux_cycle)],
                         "tags": _TAGS[theme]})
    points = np.vstack(pts_list)
    feature_tensor = np.vstack([_unit(c) for c in centers])   # ≤3 兴趣中心
    mean_tensor = _unit(points.mean(axis=0))                  # 与 points 同源
    used_skills = [{"name": "nginx-subpath-proxy", "use_count": 6},
                   {"name": "pytest-retry-fix", "use_count": 2}]
    pstore.upsert(user, feature_tensor=feature_tensor, mean_tensor=mean_tensor,
                  used_skills=used_skills, points=points, point_meta=meta)


# alice: git+docker+frontend（三兴趣中心，24 点）
_seed_profile("alice", [("git", 10), ("docker", 8), ("frontend", 6)], [9, 8, 7, 6])
# bob: git+docker（与 alice 主题重叠→ mean 相似→聚类图有边，14 点）
_seed_profile("bob", [("git", 8), ("docker", 6)], [8, 7, 5])
# m0032: data（孤立主题→ 与 alice/bob 相似度低于阈值→冷启动灰点，12 点）
_seed_profile("m0032", [("data", 12)], [6, 4])

# skill 向量缓存 .skill_index.pkl（维度 = DIM，散点里 ▲ 才会出现）——
# nginx-subpath-proxy 落在 git 簇附近、pytest-retry-fix 落在 docker 簇附近。
skill_index = {
    "skill_names": ["nginx-subpath-proxy", "pytest-retry-fix"],
    "embeddings": np.vstack([
        _unit(_THEMES["git"] + 0.10 * np.random.randn(DIM)),
        _unit(_THEMES["docker"] + 0.10 * np.random.randn(DIM))]),
}
with (SKILL / ".skill_index.pkl").open("wb") as f:
    pickle.dump(skill_index, f)

# events：四类各来几条（feedback 好评+差劲 / push_edit / canary promoted+rejected
# / pin）。actor/skill 用已有用户与 skill;贡献者 = nginx-subpath-proxy 的
# atom_adoption 达阈值者（alice ws4 / bob ws3）→ 铃铛与世界消息有内容。
estore = EventStore(DB)
estore.emit_feedback(actor="bob", skill="nginx-subpath-proxy",
                     traj_id="traj_ext_good_01", score_avg=8.6, n_atoms=2,
                     side="main", sha=m_sha or "")          # → 通知 alice（好评）
estore.emit_feedback(actor="m0032", skill="nginx-subpath-proxy",
                     traj_id="traj_ext_bad_01", score_avg=3.4, n_atoms=1,
                     side="main", sha=m_sha or "")          # → 通知 alice+bob（差劲）
estore.emit_push_edit(actor="m0032", skill="nginx-subpath-proxy",
                      branch="user-staging/m0032", ref_sha="a1b2c3d")
estore.emit_canary(skill="nginx-subpath-proxy", action="promoted",
                   main_avg=7.0, staging_avg=8.1)
estore.emit_canary(skill="pytest-retry-fix", action="rejected",
                   main_avg=7.3, staging_avg=5.9)            # 无贡献者→仅世界消息
estore.emit_pin(actor="admin", skill="nginx-subpath-proxy",
                target_user="alice", scope="user")

print("demo home:", ROOT)
print("db:", DB)
print("profile db:", PROFILE_DB)
