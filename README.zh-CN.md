<div align="center">

# xskill

**从 AI Agent 的执行轨迹中自动蒸馏可复用的 Skill。**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-370025263%2Fxskill-181717?logo=github)](https://github.com/370025263/xskill)

[English](./README.md) · **简体中文**

</div>

---

> 你的 agent 其实早就知道怎么解决问题，只是每次会话结束就忘了。
> **xskill** 在后台默默观察它们做了什么，把行之有效的模式蒸馏成 Skill 库，并通过 A/B 灰度只保留真正胜出的版本。

## 为什么用 xskill

LLM agent 一遍又一遍重复同样的解题过程，因为它们的"经验"在 session 结束时就蒸发了。手工维护 prompt 库是个常见解法，但维护成本高、容易过时，也无法捕捉"为什么这么做"。

**xskill** 把每次 agent 执行（一个 `traj_*.md` 文件）都视为原材料：

```
traj_*.md  ──►  抽 meta ──►  embed ──►  蒸馏 ──►  Skill (main)
                                          │
                                          └─►  Skill (staging) ──A/B──►  merge | discard
```

后台 daemon 监听你注册的轨迹目录。新轨迹会被 embed、聚类，然后蒸馏成一个有名字的 **Skill**。每个 Skill 是一个独立的小 git 仓，拥有 `main` 与 `staging` 两个分支；新候选通过灰度流量 + LLM 评审 UX 打分进行 A/B，赢了才合并到 main。

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
curl -fsSL https://raw.githubusercontent.com/370025263/xskill/main/examples/config.yaml.example \
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

```
                       ┌──────────────────────────────────────┐
   traj_*.md  ────►    │  watcher（后台线程）                 │
   （任意已注册目录） │     ├─ 抽 meta                         │
                       │     ├─ embed + 入索引                │
                       │     ├─ 蒸馏 / 更新 Skill             │
                       │     └─ ux_score（LLM 当裁判）        │
                       └──────────────┬───────────────────────┘
                                      ▼
                       ~/.xskill/skill/<name>/
                          ├── SKILL.md              ← 形如 prompt 的产物
                          ├── candidates/           ← 未升级的候选模式
                          ├── source_trajs/         ← 证据
                          └── .git/                 ← 单 skill 独立 git
                                main  ⇄  staging   （灰度 A/B）
```

当 chat agent 命中某个 Skill 时，请求按概率 `p` 走 staging，其余走 main。两侧各积累 ≥ N 条 UX 评分后，xskill 比较平均分，自动决定把 staging 合进 main 还是丢弃。**全程无人工干预。**

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
  probability: 0.2
  min_samples: 5
  max_days_hold: 14

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
| **Skill**      | 由 ≥ N 条支撑轨迹蒸馏出来的可复用 prompt 产物。落在 `~/.xskill/skill/<name>/`，全版本受控。 |
| **Candidate**  | Skill 内部的未升级模式。攒够支撑轨迹后才会进入 `SKILL.md` 正文。 |
| **Canary**     | 单 skill 级别的 main / staging 灰度。merge 还是 discard 由 UX 分决定，不靠人。 |
| **UX score**   | LLM-as-judge 评分。从 chat 归档反馈中读取，给 skill 服务用户的效果打分。 |
| **Registry**   | 已注册的监听目录列表。一旦 add，watcher 永久轮询。 |

## Roadmap

- [ ] Web UI 浏览 skill、查看灰度数据、手动 merge / discard
- [ ] Skill marketplace：导入 / 导出可移植的 skill bundle
- [ ] 多租户 skill 仓（每个团队一个 `skill_dir`）
- [ ] 原生 MCP server 接口（skill 即工具）
- [ ] 异步 embedding 后端，支撑大型 registry

有想法？请提 [issue](https://github.com/370025263/xskill/issues)。

## 开发

```bash
git clone https://github.com/370025263/xskill
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

如果 xskill 帮你的 agent 不再"重复造轮子"，请给 [GitHub](https://github.com/370025263/xskill) 一颗 ⭐，让更多人看到。

</div>
