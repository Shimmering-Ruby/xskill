<div align="center">

# xskill

**把你的 AI agent 已经做过的事，自动沉淀成一个可复用的 Skill 库。**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

[English](./README.md) · **简体中文**

</div>

---

> xskill 在后台默默盯着你的 coding agent 跑过的会话历史，把里面真正起作用的解题模式整理成 Skill 文件。下次遇到类似问题，agent 直接命中已有 Skill，而不是从零开始原地踏步。

## 为什么用 xskill

如果你日常在用 Claude Code（或 Codex、Cursor、Trae…）写代码，大概率碰到过这样的循环：

- 今天你手把手教 agent 怎么修某一类 bug。
- 明天它忘了，又 Google 一遍，重新走一遍弯路。
- 你要么每天早上重新讲一遍，要么手维护一份 prompt 库，看着它慢慢过时。

xskill 把这个循环关上。把它指向 agent 写会话历史的目录，它会默默把"用过的有效套路"整理成一个 Skill 库；下次 agent 跑的时候自动加载这份库。你照常干活，Skill 库自己长。

它在两件事上比较有"主见"：

- **你不用手工挑**：新经验是该并入已有 Skill 还是单独开一个，由 xskill 自己判断。
- **只有真能帮上用户的 Skill 才上线**：新版本会和旧版本并行跑一段时间，输的那一份会被丢掉。

## 安装

```bash
pip install xskill
```

PyPI 包名是 [`xskill`](https://pypi.org/project/xskill/)，CLI 入口也叫 `xskill`。需要 Python 3.11+。

然后在 `~/.xskill/config.yaml` 放一份配置：

```bash
mkdir -p ~/.xskill
curl -fsSL https://raw.githubusercontent.com/SkillNerds/xskill/main/examples/config.yaml.example \
  -o ~/.xskill/config.yaml
# 编辑 llm.api_key 和 embedding.api_key
```

最小 `config.yaml`：

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model:    doubao-embedding-vision-251215
  api_key:  YOUR_KEY
  dim:      0
```

任何 OpenAI 兼容 endpoint 都行（DeepSeek、Qwen / Ark、OpenAI 等）。字段缺失直接抛错，不读环境变量。完整模板见 [`examples/config.yaml.example`](examples/config.yaml.example)。

启动 daemon：

```bash
xskill serve            # FastAPI + watcher + Web UI，监听 :8000
```

如果你用 Claude Code，到此就够了——daemon 启动时会自动发现 `~/.claude/projects/` 并开始监听。其他 agent 则把它写轨迹的目录注册进来：

```bash
xskill registry add /path/to/your/agent/trajectories
```

## CLI

只有 5 条命令。筛选与格式化交给 `grep` / `awk`，不内置 flag。

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

```python
from xskill import XSkill

x = XSkill()  # 自动加载 ~/.xskill/config.yaml

# 跨所有注册目录搜索 Skill
for hit in x.search_skills("django form", top_k=5):
    print(f"{hit.similarity:.3f}  {hit.skill.name}  uses={hit.skill.use_count}")

# 浏览 Skill 库
for skill in x.skill_repo:
    print(skill.name, skill.canary_status(), skill.ux_avg(side="main", days=30))

# 注册新的监听目录
x.registry.add("/abs/path/to/trajectories", label="prod-eng")

# 或者直接起 daemon
x.serve(host="0.0.0.0", port=8000)
```

进阶：`from xskill import Registry, SkillRepo, Evaluator, Skill, Trajectory` 直接拿子系统。

## xskill 里有哪几个 agent

后台跑着几个 LLM agent，每个职责都很窄：

| Agent | 一句话职责 |
| ----- | ---------- |
| **TaskAgent** | 读一条原始轨迹，按"用户意图"切成若干 Atom（一个 Atom = 用户实际想做的一件事）。 |
| **TaskClusterAgent** | 给每个新 Atom 拍板：命中已有 Skill、并入已有 Skill，还是新开一个 Skill。优先复用，不轻易新建。 |
| **SkillEditAgent** | 当某个 Skill 累计到了足够多的相关 Atom 时，撰写或重写它的 `SKILL.md`（连带必要的脚本 / references）并提交。 |
| **UserEditAbsorbAgent** | 监听你对装出去的 Skill 文件做的手改，把这些改动作为 ground truth 吸回 Skill 库。 |
| **AtomCanary** | 把现有 Skill 和新候选并行跑在真实流量上，根据每个 Atom 的用户体验分判定谁留下。 |

## 跨 code agent 支持

输入端"轨迹采集"和输出端"Skill 安装"都是可插拔的。当前的真实状态如下：

| Coding agent | 轨迹采集（输入） | Skill 安装（输出） |
| ------------ | ---------------- | ------------------ |
| **Claude Code** | 原生支持——`xskill serve` 启动时自动发现 `~/.claude/projects/`，把每条 session JSONL 桥成 xskill 轨迹；Skill 在灰度评估时还会自动注入 canary 标记。 | 原生支持——Skill 以 symlink 装到 `~/.claude/skills/<name>/`，更新随 Claude Code 下次启动即时生效，无需 copy。 |
| **Codex CLI** | 暂未支持，欢迎 issue / PR。 | 暂未支持。 |
| **Cursor** | 暂未支持。 | 暂未支持。 |
| **Trae** | 暂未支持。 | 暂未支持。 |
| **OpenCode** | 暂未支持。 | 暂未支持。 |
| **OpenClaw** | 暂未支持。 | 暂未支持。 |
| **其他 agent** | 手动接入——用 SDK (`xskill.adapters.submit_trajectory`) 提交 `markdown` / `json` / `raw` 三种格式之一。 | 手动接入——每个 Skill 就是一个目录，含 Anthropic 风格 `SKILL.md` + YAML frontmatter；把它 copy / symlink 到你 agent 的发现路径里即可。 |

输出格式遵循 Anthropic 的 `SKILL.md` schema——任何已经能读 Anthropic Skills 的 agent 都能直接读 xskill 的产物。我们正在逐个加原生 adapter——Codex、Cursor、OpenCode 等都在 [roadmap](#roadmap) 上，欢迎 PR。

## 概念

| 术语 | 含义 |
| ---- | ---- |
| **Trajectory（轨迹）** | 一次 agent 执行，通常是一个 session 的对话记录，xskill 以 `traj_*.md` 形式存盘。 |
| **Atom** | 轨迹中"一个用户意图"对应的最小片段。一条轨迹会切出 1 个或多个 Atom。所有归类判断都发生在 Atom 粒度上。 |
| **Skill** | 一个可被 agent 加载的、prompt 形态的产物：一份 `SKILL.md` + 可选的脚本和 references。每个 Skill 在 `~/.xskill/skill/` 下有独立的版本化目录。 |
| **Canary（灰度）** | 在真实流量上把现有 Skill 与新候选版本并行比较，根据用户体验分留下更好的那个。 |
| **Registry** | xskill 监听的目录列表。add 一条路径，daemon 就会一直轮询它。 |
| **UX score** | LLM 充当裁判，对 Skill 在某个 Atom 上的实际服务效果打分；canary 据此判输赢。 |

## xskill 与同类项目对比

动手之前，我们调研了 10 个学术 / 开源的 trajectory-to-skill 系统（Hermes、OpenSpace、EvoSkill、AutoSkill、AgentEvolver、MemSkill、EvoAgentX、SE-Agent、SkillRL、GEPA）。完整矩阵在 [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md)，每格都带 `path:line` 代码证据。

xskill 借鉴的：

- **`SKILL.md` 作为跨 agent 共同单位**——OpenSpace / EvoSkill / AutoSkill 已在此收敛，xskill 沿用同一份 Anthropic frontmatter schema，保证可移植。
- **LLM-as-judge 的 UX 评分**——受 AutoSkill 的 per-turn `relevant / used` 信号启发。
- **每个 Skill 独立版本**——每个 Skill 是独立 git 仓库，历史 / diff / 回滚都是一等公民。

xskill 做了同类项目都没做的：

- **真正的 A/B 灰度**——chat 流量按概率分流，两侧 UX 评分判赢家，全程无人。
- **对称的两端入口**——per-session 流式接入（丢一条轨迹 → watcher 自动接住）与批量回填（`xskill registry add /archive` 把整段历史全量入库）同等优先。

## Roadmap

- [ ] **更多 coding-agent adapter**：Codex CLI、Cursor、Trae、OpenCode、OpenClaw、Goose、OpenHands、Aider 的双向支持（轨迹采集 + Skill 安装）
- [ ] 原生 MCP server 接口（Skill 即工具）
- [ ] Web UI：浏览 Skill 库、查看灰度数据、手动放行 / 丢弃
- [ ] 基于使用情况的自动淘汰（被检索很多但从未被实际用到的 Skill 自动下线）
- [ ] Skill marketplace：导入 / 导出可移植的 Skill bundle
- [ ] 多租户 Skill 库（每个团队一个 `skill_dir`）

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

MIT (c) [370025263](https://github.com/370025263)，详见 [LICENSE](LICENSE)。

---

<div align="center">

如果 xskill 帮你的 agent 不再"重复造轮子"，请给 [GitHub](https://github.com/SkillNerds/xskill) 一颗星，让更多人看到。

</div>
