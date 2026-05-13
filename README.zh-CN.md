<div align="center">

# xskill

**从 AI Agent 的执行轨迹中自动蒸馏可复用的 Skill。**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

[English](./README.md) · **简体中文**

</div>

---

> 你的 agent 其实早就知道怎么解决问题，只是每次会话结束就忘了。
> **xskill** 在后台默默观察它们做了什么，把行之有效的模式蒸馏成 Skill 库，并通过 A/B 灰度只保留真正胜出的版本。

> ⚠️ **v0.4.0a1 —— AtomTask 重构（alpha）。** 流水线粒度从"整条轨迹"下沉到 **AtomTask**（一次用户意图），每个 Skill 变成 `baby` → `main` → `staging` 三分支的状态机。对外 SDK 与 `SKILL.md` schema 不变；运行时 state（DB、磁盘 skill 仓）**不向后兼容** —— 从 `0.3.x` 升级请清掉 `~/.xskill/`。

## 为什么用 xskill

LLM agent 一遍又一遍重复同样的解题过程，因为它们的"经验"在 session 结束时就蒸发了。手工维护 prompt 库是个常见解法，但维护成本高、容易过时，也无法捕捉"为什么这么做"。

**xskill** 把每次 agent 执行（一个 `traj_*.md` 文件）都视为原材料 —— 但蒸馏的单位**不是**整条轨迹。一条轨迹会先被拆成若干 **AtomTask**（每个 atom = 一段用户意图），每个 atom 单独跟现有 skill 目录做聚类，命中的 Skill 会经过三条 git 分支才成熟：

```
traj_*.md  ──split──►  AtomTask*  ──cluster──►  候选 buffer        ──edit──►  Skill
                                       │       （每个 skill 独立）              │
                                       └── 复用 / 整合 / 新建                   │
                                                                                ▼
                          baby 分支  ──promoted──►  main  ──canary──►  staging  ──A/B──►  merge | discard
                          (stub，CC 不可见)        (CC 可见)         (≥5 ux 样本)
```

聚类 agent 的优先级是**复用 > 整合 > 新建**，相似 atom 会收敛到同一个 Skill 而非生出近义副本。SkillEditAgent 只在某个 skill 的候选 buffer 累计权重过阈值时才触发。Canary 跑在独立的 watcher 轮次里 —— 不绑在 cluster 链上 —— 单个 Skill 评分阻塞不会拖累其他 Skill。

## 跨 code agent 兼容

xskill 站在**任何写出轨迹的 agent** 与**任何最终消费 skill 的 agent** 之间。两端都是可插拔的。

| 方向 | 当前支持 | 路线图 |
| ---- | -------- | ------ |
| **轨迹入口**（你的 agent 写出来的） | Claude Code (`traj_*.md`，带 `<!-- xskill: -->` 头) | Codex CLI、OpenCode、Goose、OpenHands、Cursor、Aider —— 每个 agent 一个 adapter |
| **Skill 出口**（谁读这份 skill 库） | Anthropic 风格 `SKILL.md` + YAML frontmatter —— Claude Code 的 `.claude/skills/<name>/` 直接就能读 | Codex（symlink）、OpenCode（路径标准化）、Goose、通用 MCP server（每个 skill 暴露成一个工具） |

输出格式遵循事实标准 `agentskills.io` 的 SKILL.md schema——任何已经能读 Anthropic Skills 的 agent 都能直接读 xskill 的产物。不兼容的 agent 通过一个薄薄的 per-agent adapter 把 skill 翻译成它要的形态（system prompt block、tool description、结构化 JSON 等）。

## 核心特性

- **零接入成本**：把 `traj_*.md` 丢进监听目录，剩下的全自动。
- **Skill 即代码**：每个 Skill 是一个版本受控的目录，包含 `SKILL.md`、支撑轨迹、候选模式与独立 git 历史。
- **内置灰度**：staging vs. main 双分支发布，自带样本量门槛与自动 merge / discard。
- **极简 CLI**：5 条命令。筛选与格式化交给 `grep`/`awk`，不内置 `--filter`/`--json`。
- **OpenAI 兼容**：DeepSeek、Qwen、Ark、OpenAI——只要支持 `/v1/chat/completions` 与 embedding 即可。
- **唯一状态源**：所有状态都在 `~/.xskill/` 里。无环境变量、无配置 fallback，缺了直接抛错。

## 快速上手

```bash
pip install xskill

mkdir -p ~/.xskill
curl -fsSL https://raw.githubusercontent.com/SkillNerds/xskill/main/examples/config.yaml.example \
  -o ~/.xskill/config.yaml
# 编辑 llm.api_key 和 embedding.api_key

xskill registry add /path/to/your/agent/trajectories
xskill serve   # 后台 daemon：FastAPI + watcher + Web UI，监听 :8000
```

到此为止。把新的 `traj_*.md` 丢到注册目录里，daemon 会自动接管：抽 meta → embed → 入索引 → 更新 Skill 库。

## CLI

只有 5 条命令。

```bash
xskill serve [--host 0.0.0.0] [--port 8000]
xskill registry add    <绝对路径> [--label NAME]
xskill registry remove <绝对路径>
xskill registry list
xskill search traj  <query> [--top-k 5]
xskill search skill <query> [--top-k 5]
```

`search` 输出 tab 分隔，可直接 pipe：

```bash
$ xskill search skill "form validation" | sort -k4 -nr | head -3
0.350  fix-early-return-in-validation-functions   3   7.8(15)  -
0.343  fix-cli-language-validation                2   8.1(12)  staging
0.309  fix-api-method-parameter-validation        0   -        -
# 列：similarity  name  use_count  ux_avg(N)  canary_status
```

## Python SDK

公开面是 **4 个类 + 6 个 dataclass**。

```python
from xskill import XSkill, Skill, Trajectory, Evaluator

x = XSkill()  # 自动加载 ~/.xskill/config.yaml

# 跨所有注册目录搜索 skill
for hit in x.search_skills("django form", top_k=5):
    print(f"{hit.similarity:.3f}  {hit.skill.name}  uses={hit.skill.use_count}")

# 浏览 skill 仓
for skill in x.skill_repo:
    print(skill.name,
          skill.canary_status(),
          skill.ux_avg(side="main", days=30))

# 注册新的监听目录
x.registry.add("/abs/path/to/trajs", label="prod-eng")

# 自己跑评估门 (CI / 单测)
ev = Evaluator(x.llm, x.config)
score = ev.evaluate(x.skill_repo["fix-foo"])
if Evaluator.should_merge(score):
    print("ready to merge")

# 或者直接起 daemon，剩下的让它自己跑
x.serve(host="0.0.0.0", port=8000)
```

进阶（少量场景）：`from xskill import Registry, SkillRepo` 直接拿子系统。

## 工作原理

Watcher 是单一轮询循环（默认 30s），驱动 5 个**互相独立**的扫描阶段 —— 任一阶段故障都不会阻塞别的路径。

```
                ┌─────────────────────────────── watcher（轮询：30s） ────────────────────────────────┐
                │                                                                                    │
  traj_*.md ──► │  1. 发现  →  2. split (TaskAgent)  →  3. embed  →  4. cluster                     │
                │              （按用户意图切 atom）    （向量索引）   （TaskClusterAgent）           │
                │                                                              │                     │
                │                                                              ▼                     │
                │                                                  ~/.xskill/skill/<name>/           │
                │                                                  ├── .candidates.yml  ← buffer    │
                │                                                  ├── SKILL.md         ← prompt    │
                │                                                  ├── scripts/、references/         │
                │                                                  └── .git              baby/main/  │
                │                                                                        staging     │
                │                                                                                    │
                │  5. SkillEditAgent  ◄── 候选权重 ≥ 阈值（独立扫描，不依赖 cluster 成功）            │
                │     ├─ 写 SKILL.md + 任意辅助文件（脚本、references）                              │
                │     ├─ 在 baby： baby → main         （此刻才让 CC 看到）                          │
                │     └─ 在 main： 基于 main fork staging 分支（进入 canary）                        │
                │                                                                                    │
                │  6. AtomCanary       ◄── 独立 polling，不被 cluster 失败阻塞                        │
                │     ├─ 按 `canary.probability` 在 main / staging 间分流                            │
                │     └─ 两侧各 ≥ `min_samples` → 比较 ux_avg → merge | discard                     │
                │                                                                                    │
                │  7. UserEditAbsorb   ◄── 检测 ~/.claude/skills/<name>/ 下的用户手改                 │
                │     └─ 稳定 ≥3 分钟 → commit 用户改动回 main，作为 ground truth                   │
                └────────────────────────────────────────────────────────────────────────────────────┘
```

**为什么三条分支。** Skill 新建时落在 `baby`（CC 不可见，只有一份 stub）。**只有当 edit 成功**才 graduate 到 `main` —— 这避免了空壳 / 半成品 skill 暴露给用户。`main` 上再生候选时，会 fork `staging` 走灰度，只有胜出的那一侧留下。

**候选就是纯 buffer。** `.candidates.yml` 被 git 忽略。每条记录形如 `{atom_id, weightscore, note}`。聚类 agent 改主意时可以**覆盖**已有记录。SkillEditAgent 触发条件是权重**之和**过阈值 —— **不是**条数过 10，**不是**累计源轨迹数。

**Symlink 安装。** Skill 升级到 `main` 时，xskill 会在 `~/.claude/skills/<name>/` 建一个软链指向 `~/.xskill/skill/<name>/`。skill 仓内任何改动 CC 立刻可见，无需 copy；用户手改也落在同一份仓里，由 `UserEditAbsorb` 自动 commit 回 `main`。

## 配置

所有配置都在 `~/.xskill/config.yaml`。文件缺失或字段缺失 → 直接抛错，不做静默 fallback。

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://api.example.com/v1
  model:    your-embedding-model
  api_key:  YOUR_KEY
  dim:      0   # 0 = 自动探测

canary:
  enabled:     true
  probability: 0.2   # 路由到 staging 的流量占比
  min_samples: 5     # 两侧各 ≥5 ux 样本后才 promote / reject

watcher:
  poll_interval: 30   # 秒
```

完整模板见 [`examples/config.yaml.example`](examples/config.yaml.example)。

```
~/.xskill/
├── config.yaml         # 唯一配置文件（无环境变量 fallback）
├── registry.db         # 监听目录 + 单条轨迹处理状态（sqlite）
├── chat_sessions.db    # chat 历史
├── logs/               # 每条轨迹一份处理日志
├── chat_archive/       # serve 自动注册的 chat 归档目录
└── skill/              # 全局 skill 仓（每个 skill 是独立 git 子仓）
```

## 概念

| 术语         | 说明 |
| ------------ | ---- |
| **Trajectory** | 一次 agent 执行，落盘成 `traj_*.md`。可在头部嵌入 `<!-- xskill:skill=... side=... sha=... -->` 元注释，watcher 据此打 UX 分。 |
| **AtomTask**   | 最小用户意图单元，由 `TaskAgent` 从轨迹中拆出。一条 traj → 1..N 个 atom。聚类发生在 atom 粒度，不是 traj 粒度。 |
| **Skill**      | 由聚类后的 atom 蒸馏成的可复用 prompt 产物。落在 `~/.xskill/skill/<name>/`，每个 skill 是独立 git 仓。 |
| **baby / main / staging** | 单 skill 的三条分支状态机：`baby` = 隐藏 stub（刚建好还没 surface 到 CC）；`main` = 已上线 skill；`staging` = 从 main fork 出来跑 canary 的候选。 |
| **候选 buffer** | 每个 skill 内的 `.candidates.yml`，git 忽略，**覆盖式**写入。聚类 agent 追加 `{atom_id, weightscore}`；SkillEditAgent 在 weightscore **求和**过阈值时触发。 |
| **Canary**     | 单 skill 级别的 main / staging 灰度。**独立**于 cluster 链的 watcher 轮次跑，两侧各 ≥5 ux 样本后自动 promote / reject。 |
| **UX score**   | 单 atom 粒度的 LLM-as-judge 评分，从 chat 归档反馈读取，给 skill 服务用户的效果打分。 |
| **Registry**   | 已注册的监听目录列表。一旦 add，watcher 永久轮询。 |

## xskill 与同类项目对比

在动手之前，我们横向调研了 10 个学术 / 开源的 trajectory→skill 系统（Hermes、OpenSpace、EvoSkill、AutoSkill、AgentEvolver、MemSkill、EvoAgentX、SE-Agent、SkillRL、GEPA）。完整的 ~270 行交叉矩阵在 [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md)，每个单元格都带 `path:line` 的代码证据。

**xskill 借鉴了什么**

- *把 SKILL.md 当作跨 agent 的共同单位* —— OpenSpace / EvoSkill / AutoSkill 已经收敛在这里，我们沿用同一份 Anthropic frontmatter schema，保证可移植。
- *LLM-as-judge 的 UX 评分* —— AutoSkill 的 per-turn `relevant/used` 信号（`autoskill/interactive/usage_tracking.py`）启发了我们的 `ux_score` 量表。
- *单 skill 独立 git 版本控制* —— EvoSkill 的 "git branch = program version"（`src/registry/manager.py:33-95`）；我们把 `.git` 放进每个 skill 目录。
- *完整 provenance* —— OpenSpace 记录 `parent_skill_ids + source_task_id + created_by + change_summary`；xskill 在每个 skill 的 git log 里留同等信息。

**xskill 做了 10 个项目都没做的事**

> *"真正灰度 / A-B：10 个项目无一实现。"* —— 调研报告 §10
- **真正的 canary A/B**：每个 skill 自带 `main` / `staging` 分支；chat 流量按概率分流，两侧各 ≥ N 条 UX 分后自动判定 merge 还是 discard。**全程无人。**
- **对称的两端入口**：per-turn 流式（丢一份 `traj_*.md` → watcher 自动接住）与批量回填（`xskill registry add /path` 把整个归档目录全量入库）同等优先——大多数被调研的项目只挑一种。

**调研报告点出的空白（=我们的 Roadmap）**

- 基于使用统计的自动淘汰（AutoSkill 的 `retrieved>=40 && used<=0` 规则）
- 基于共同祖先的 git-style 三方合并（GEPA `merge.py:118-207`）
- BM25 → embedding cosine → LLM-judge 三段式检索（OpenSpace）
- 更多 code-agent adapter —— 见下方

## Roadmap

- [ ] **更多 code-agent adapter** —— Codex、OpenCode、Goose、OpenHands、Cursor、Aider 双向（轨迹入口 + skill 出口）
- [ ] 基于使用统计的自动淘汰（`retrieved>=N && used<=0` → 删除）
- [ ] git-style 三方合并，处理多源 skill 整合
- [ ] BM25 + embedding + LLM-judge 三段式检索 reranker
- [ ] Web UI 浏览 skill、查看灰度数据、手动 merge / discard
- [ ] Skill marketplace：导入 / 导出可移植的 skill bundle
- [ ] 多租户 skill 仓（每个团队一个 `skill_dir`）
- [ ] 原生 MCP server 接口（skill 即工具）
- [ ] 异步 embedding 后端，支撑大型 registry

有想法？请提 [issue](https://github.com/SkillNerds/xskill/issues)。

## 开发

```bash
git clone https://github.com/SkillNerds/xskill
cd xskill
pip install -e .[dev]
pytest -q
```

内部设计文档在 [`docs/`](docs/)（中英混排）。

## 贡献

欢迎 PR，请遵守：
1. 先开 issue 描述问题。
2. 加测试或扩展现有测试（无测试不合并）。
3. 公开 API 增量请保持 `xskill/__init__.py` 极简——这一层我们守得很严。

## License

MIT © [370025263](https://github.com/370025263)，详见 [LICENSE](LICENSE)。

---

<div align="center">

如果 xskill 帮你的 agent 不再"重复造轮子"，请给 [GitHub](https://github.com/SkillNerds/xskill) 一颗 ⭐，让更多人看到。

</div>
