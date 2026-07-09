"""为 P1 行为验收造一套演示数据（独立 XSKILL_HOME，不碰真实 ~/.xskill）。"""
import json
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
            "source_harness,source_model,discovered_at) VALUES(?,?,?,?,?,?,?)",
            (wd_id, f"{tid}.md", status, n_atoms, harness, model,
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

print("demo home:", ROOT)
print("db:", DB)
