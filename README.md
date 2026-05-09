# traj2skill (`t2s`)

从 AI Agent 执行轨迹中自动蒸馏可复用的 Skill。**watcher 后台自动跑全 pipeline**（meta → embed → distill → ux 反馈），CLI 仅暴露 5 条用户真正用的命令。

---

## 安装 + 配置

```bash
pip install -e .

mkdir -p ~/.t2s
cp examples/config.yaml.example ~/.t2s/config.yaml
# 编辑 ~/.t2s/config.yaml 填入 llm.api_key 和 embedding.api_key
```

**所有状态/配置都在 `~/.t2s/`**：

```
~/.t2s/
├── config.yaml         # 所有配置（无环境变量、无 ~/.aikey fallback；缺即抛错）
├── registry.db         # 监听目录 + 轨迹处理状态
├── chat_sessions.db    # chat 会话历史
├── logs/               # 每条轨迹的 process 日志
├── chat_archive/       # serve 自动注册的 chat 归档目录
└── skill/              # 全局 skill 仓库（每个 skill 自带 .git 子仓维护 main/staging 灰度）
```

---

## CLI（5 条）

```bash
t2s serve [--host 0.0.0.0] [--port 8000]
        # 唯一长跑入口：FastAPI + Web UI + watcher 后台线程

t2s registry add    <path>
t2s registry remove <path>
t2s registry list

t2s search traj  <query> [--top-k 5]    # 跨所有 registry
t2s search skill <query> [--top-k 5]    # 跨所有 registry
```

`search` 输出 tab 分隔的列，shell 友好：

```bash
$ t2s search skill "form validation"
0.350  fix-early-return-in-validation-functions  3   7.8(15)  -
0.343  fix-cli-language-validation               2   8.1(12)  staging
0.309  fix-api-method-parameter-validation       0   -        -
# 列：similarity name use_count ux_avg(N) canary
```

筛选/排序交给 shell（`grep` / `awk` / `sort`），不内置 `--filter` / `--json`。

---

## Python SDK

```python
from traj2skill import T2S, Skill, Trajectory, Evaluator

t2s = T2S()                       # 默认从 ~/.t2s/config.yaml 加载

# 检索
hits = t2s.search_skills("django form")
for h in hits:
    print(h.skill.name, h.similarity, h.skill.use_count)

# 注册新目录
wd = t2s.registry.add("/path/to/traj_dir")

# 浏览 skill 仓库
for skill in t2s.skill_repo:
    print(skill.name, skill.canary_status(), skill.ux_avg(side="main", days=30))
    for c in skill.candidates:
        print("  candidate:", c.pattern, c.kind, len(c.supporting_trajs))

# 评估（CI / 单测用）
ev = Evaluator(t2s.llm, t2s.config)
score = ev.evaluate(t2s.skill_repo["fix-foo"])
print(score.tier, score.eval_score, score.scores)
if Evaluator.should_merge(score):
    print("merge!")

# 启动 daemon
t2s.serve(host="0.0.0.0", port=8000)
```

**4 类 + 6 dataclass** 是公开面：`T2S / Skill / Trajectory / Evaluator` + `WatchDir / SkillHit / TrajectoryHit / EvalScore / Candidate / UxScoreResult`。

---

## 工作流

1. `t2s registry add /path/to/agent/trajectory/dir` 注册目录（绝对路径）
2. `t2s serve` 起 daemon，watcher 后台轮询所有注册目录
3. agent 每跑完一条任务写 `traj_*.md` 到注册目录
4. watcher 自动：抽 meta → 建索引 → 蒸馏成新 skill 或更新已有 skill → 灰度 staging
5. 用户用 chat UI 提问，路由到匹配的 skill；chat 归档反馈 → ux_score 自动闭环

---

## 设计 / 测试方案

- 设计：[`docs/superpowers/specs/2026-05-09-cli-compaction-design.md`](docs/superpowers/specs/2026-05-09-cli-compaction-design.md)
- E2E 测试方案：[`docs/superpowers/test-plans/2026-05-09-cli-compaction-e2e.md`](docs/superpowers/test-plans/2026-05-09-cli-compaction-e2e.md)
