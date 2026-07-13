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
# P3 散点 ▲:再补三个与画像主题对应的 skill——描述会被真实 embedding,
# 三角自然落在各自语义簇附近(git/docker/frontend)。
mk_skill("git-conflict-resolver",
         "多分支 rebase/merge 冲突的系统化排查与解决手法,含三方冲突整合",
         ["v1: 初版蒸馏"])
mk_skill("docker-compose-doctor",
         "docker compose 网络不通/卷挂载/启动顺序依赖问题的诊断决策树",
         ["v1: 初版蒸馏"])
mk_skill("react-render-optimizer",
         "React 长列表卡顿与重渲染性能问题的优化清单(虚拟滚动/memo/懒加载)",
         ["v1: 初版蒸馏"])

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
# 画像 points/兴趣中心/skill 向量 SHALL 用真实 embedding API 计算——原子摘要
# 文本各不相同、内容真实，走生产同款 SkillRecommendEngine.update_user_interest
# 链路（含真实 numpy k-means 聚类出 feature_tensor），不再手造随机向量；
# t-SNE 投影出来的簇分离必须来自真实语义差异，假向量投影出来的分离没有意义。
# 需要真实 embedding 凭证：环境变量 EMBED_API_KEY / EMBED_BASE_URL / EMBED_MODEL
# （不在脚本里硬编码密钥）。三个用户:alice/bob 都做 git+docker 开发任务→内容
# 真实相似→聚类图有真实边;m0032 是 IT 支持角色（不写代码,处理账号/网络/
# 设备类工单）→与研发内容语义上足够远→冷启动孤立点也是真实相似度算出来的，
# 不是靠调阈值凑出来的。
import os
from xskill.utils.llm import EmbedClient
from xskill.recommend.engine import SkillRecommendEngine
from xskill.recommend.client_interest import ClientInterest
from xskill.skill.repo import rebuild_skill_index
from xskill.events import EventStore

embed_client = EmbedClient.from_config({
    "base_url": os.environ["EMBED_BASE_URL"],
    "model": os.environ["EMBED_MODEL"],
    "api_key": os.environ["EMBED_API_KEY"],
})

TRAJ_ROOT = ROOT / "team_traj"
PROFILE_DB = ROOT / "team_profile.db"

# 四主题各若干条真实、彼此不同、带具体情境的任务摘要（一到两句话,不是关键词
# 短语）——t-SNE 靠这些文本本身的真实语义差异分簇,文本越具体、语义差异才
# 越能被 embedding 模型分开,不是靠人工加噪声撑出来的假分离。
_GIT_SUMS = [
    "合并 release 分支时遇到 rebase 冲突,双方都改了同一处逻辑,逐行核对保留双方语义正确的改动后重新跑单测。",
    "误在 detached HEAD 状态下提交了三个 commit,排查后用 cherry-pick 挪回正确分支并清理游离提交。",
    "用 git bisect 二分排查上周引入的性能回归,最终定位到一次看似无关的依赖升级提交。",
    "有人误把几百 MB 的日志文件提交进仓库,用 filter-repo 重写历史彻底清除并通知全员强制拉取。",
    "submodule 指针没跟着主仓库一起提交,导致 CI 构建时子模块代码停留在旧版本,补一次同步提交修复。",
    "队友 force-push 覆盖了远端分支历史,协调本地已有提交做一次 rebase 对齐,避免直接丢弃工作。",
    "两个开发三个月的长期分支要合并,冲突集中在同一模块的重构逻辑,拆成多轮小合并逐步收敛。",
    ".gitignore 没配全,构建产物和 IDE 临时文件混进了提交历史,清理后补全忽略规则防止复发。",
    "紧急 hotfix 需要同步到三个还在维护的发布分支,用 cherry-pick 逐个应用并处理各分支上下文差异。",
    "团队里 Windows 和 Linux 同事换行符设置不一致,导致 diff 全是无意义变更,统一配置 .gitattributes 修复。",
    "一个提交塞了太多不相关改动不好 review,用 interactive rebase 拆成几个语义清晰的原子提交。",
    "同事手滑删掉了一个还没合并的功能分支,靠 reflog 找到最后一次提交哈希把分支恢复回来。",
    "CI 环境里的 pre-commit hook 因为权限问题一直失败,排查是脚本没有可执行权限导致的。",
    "三方合并冲突里同一个代码块被两个人分别改了不同逻辑,理解双方意图后手工整合到一起。",
    "排查发现仓库历史里混进过一次误提交的密钥文件,用 filter-repo 清理并轮换了对应密钥。",
    "tag 签名验证在 CI 里突然失败,查出是签名用的 GPG key 过期,重新签发后修复。",
    "用浅克隆拉代码导致 git blame 只能追溯到截断点,改成完整克隆恢复完整责任人信息。",
    "多个 worktree 并行开发不同功能分支时路径互相冲突,调整 worktree 目录结构规避。",
]
_DOCKER_SUMS = [
    "docker-compose 起服务后容器互相连不上,排查是自定义 bridge 网络配置错误导致的 DNS 解析失败。",
    "容器内系统时区跟宿主机不一致,导致日志时间戳全部错位,统一挂载时区配置文件修复。",
    "镜像体积膨胀到 2GB 多,改成多阶段构建只保留运行时依赖,体积压到 300MB 以内。",
    "排查两个容器之间偶发连不通,发现是自定义网络下 DNS 解析偶尔超时导致的。",
    "docker-compose 里几个服务启动顺序有依赖,没等数据库就绪就连接导致偶发启动失败。",
    "容器里的进程以 root 身份写文件,宿主机挂载目录权限不匹配导致写入被拒绝。",
    "生产容器频繁被 OOM Killer 杀掉,排查发现内存 limit 设置过低,调整并加了告警。",
    "每次构建都重新下载全部依赖,调整 Dockerfile 层顺序让依赖缓存正确复用。",
    "healthcheck 探测逻辑太严格,服务刚启动还没预热完就被判定不健康触发重启。",
    "容器内有子进程没被正确回收变成僵尸进程,改用正确的 init 进程管理生命周期。",
    "跨机房的容器网络延迟异常升高,排查发现是 overlay 网络的某个节点路由表过期。",
    "Dockerfile 里的 apt 源在某些网络环境下不可达,换成可靠的国内镜像源修复构建失败。",
    "容器重启后挂载的数据卷内容丢失,发现是用了匿名卷而不是命名卷导致的。",
    "docker swarm 滚动更新时卡在中间状态,排查是健康检查超时时间设置得太短。",
]
_FRONTEND_SUMS = [
    "React 组件在某个状态切换时样式突然回归成旧版本,定位到条件渲染逻辑漏了一个分支。",
    "表单校验在 Safari 里始终不触发,排查是用到的正则语法在 Safari 引擎下行为不一致。",
    "一个上千行的长列表滚动时明显掉帧,改用虚拟滚动只渲染可视区域内的条目。",
    "深色模式下部分文字和背景色对比度太低几乎看不清,重新核对了配色方案。",
    "路由懒加载的页面切换时会有一瞬间白屏闪烁,加了骨架屏做过渡体验。",
    "移动端点击弹层外部关闭时底层元素也被误触发了点击,修复了事件穿透问题。",
]
# m0032 是 IT 支持角色，不写代码——处理账号/网络/设备类工单，语义上跟研发
# 任务足够远，聚类图里应该自然孤立（不是调阈值凑出来的）。
_SUPPORT_SUMS = [
    "同事反馈企业邮箱登录一直提示密码错误,远程协助确认账号被误锁,走后台重置密码并重新绑定多因素认证。",
    "新入职同事电脑连不上公司 VPN,排查是客户端证书过期,重新签发证书并指导对方重新导入配置。",
    "财务同事的打印机驱动装不上,远程排查发现是打印机固件版本和驱动不兼容,更新固件后解决。",
    "季度报销单据堆积,逐条核对发票抬头与报销政策是否匹配,退回几张信息不全的单据。",
    "会议室投屏经常连不上笔记本,排查是无线投屏器固件老旧,升级固件并重新配对。",
    "有同事离职,按流程收回工卡、笔记本并注销其在各系统里的账号权限。",
    "内网共享盘权限配置混乱,梳理各部门文件夹权限,收紧了几个误开放的敏感目录。",
    "新员工入职当天需要开通邮箱、内网账号、门禁卡,整理了一份标准化开通清单。",
    "公司 WiFi 在某个楼层信号很弱,现场勘测后建议在该区域加装一个无线 AP。",
    "有同事的笔记本疑似中了勒索软件,立即断网隔离并联系安全团队做后续处置。",
    "会议室预定系统经常被占而不用,梳理规则加了超时未签到自动释放的机制。",
    "帮行政部门核对下季度办公用品采购清单,确认预算范围内的品类和数量。",
]
_TAGS = {"git": ["git"], "docker": ["docker"], "frontend": ["frontend"], "support": ["support"]}


def _write_atoms(user, themes, *, skill_hits=None):
    """在 traj_root/clients/<user>/sessions/ 下落真实结构 atom json——
    与 AtomTaskStore 同款文件布局，engine.update_user_interest 直接读得到。

    ``skill_hits``: ``{原子序号: skill 名}``，标记该原子用过哪个 skill——
    真实 use_count 由这些标记聚合出来（``_user_used_skills``），不再手填。
    """
    skill_hits = skill_hits or {}
    sessions = TRAJ_ROOT / "clients" / user / "sessions"
    traj_id = f"traj_{user}_profile_seed"
    tasks = sessions / traj_id / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    ux_cycle = [9, 8, 8, 7, 6, 4]
    k = 0
    for theme, texts in themes:
        for summary in texts:
            k += 1
            atom_id = f"atom_{user}_profile_{k:03d}"
            (tasks / f"{atom_id}.json").write_text(json.dumps({
                "atom_id": atom_id, "traj_id": traj_id,
                "offset_start": 1, "offset_end": 2, "intent": summary,
                "summary": summary, "tags": _TAGS[theme],
                "used_skills": [skill_hits[k]] if k in skill_hits else [],
                "ux_score": ux_cycle[k % len(ux_cycle)],
            }, ensure_ascii=False), encoding="utf-8")


# alice: git+docker+frontend（24 原子）；命中五个 skill(原子序号按主题分段:
# 1-10 git / 11-18 docker / 19-24 frontend),▲ 会散落在对应语义簇附近。
_write_atoms("alice", [("git", _GIT_SUMS[:10]), ("docker", _DOCKER_SUMS[:8]),
                       ("frontend", _FRONTEND_SUMS[:6])],
            skill_hits={1: "nginx-subpath-proxy", 2: "nginx-subpath-proxy",
                       3: "nginx-subpath-proxy",
                       4: "git-conflict-resolver", 6: "git-conflict-resolver",
                       11: "pytest-retry-fix",
                       12: "docker-compose-doctor", 14: "docker-compose-doctor",
                       19: "react-render-optimizer", 21: "react-render-optimizer"})
# bob: git+docker（14 原子，与 alice 主题重叠→内容真实相似→聚类图有边；
# 原子序号 1-8 git / 9-14 docker）
_write_atoms("bob", [("git", _GIT_SUMS[10:18]), ("docker", _DOCKER_SUMS[8:14])],
            skill_hits={1: "nginx-subpath-proxy", 2: "nginx-subpath-proxy",
                       3: "git-conflict-resolver",
                       9: "docker-compose-doctor", 10: "docker-compose-doctor"})
# m0032: IT 支持角色，只处理账号/网络/设备工单（12 原子）→ 与 alice/bob 的
# 研发内容语义不重叠→冷启动孤立点
_write_atoms("m0032", [("support", _SUPPORT_SUMS)])

engine = SkillRecommendEngine(
    config={"recommend": {"quality_ratio": 0.8, "staging_need": 3},
           "canary": {"total_samples": 3}},
    skill_dir=SKILL, traj_root=TRAJ_ROOT,
    embed_client=embed_client, profile_db=PROFILE_DB,
)
for uname, _ in users:
    engine.update_user_interest(ClientInterest(uname))

# skill 向量缓存 .skill_index.pkl——复用生产同一份 rebuild_skill_index，从
# SKILL.md 的 description 真实计算（与画像 points 同一模型/同一维度空间，
# 散点里 ▲ 才能和圆点落在同一投影里）。
rebuild_skill_index(skill_dir=SKILL, embed_client=embed_client)

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
