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

**首次运行**会自动在 `~/.xskill/config.yaml` 写一份带注释的配置模板，并提示你填写：

```bash
xskill serve
#  Created a config template at ~/.xskill/config.yaml
#  Edit it — fill in llm.api_key and embedding.api_key — then run `xskill serve` again.

# 编辑 ~/.xskill/config.yaml，填入 llm.api_key 和 embedding.api_key，然后：
xskill serve
```

最小 `config.yaml` 就是两个 endpoint：

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://api.deepseek.com
  model:    deepseek-embedding
  api_key:  YOUR_KEY
  dim:      0
```

任何 OpenAI 兼容 endpoint 都行（DeepSeek、OpenAI、Qwen/DashScope、OpenRouter、本地 Ollama 等）。字段缺失直接抛错，不读环境变量。auto-init 写出的 `~/.xskill/config.yaml` 本身就是完整模板——canary、watcher、team、sandbox 各段都有行内注释。

如果你用 Claude Code，到此就够了——daemon 启动时会自动发现 `~/.claude/projects/` 并开始监听。其他 agent 则把它写轨迹的目录注册进来：

```bash
xskill registry add /path/to/your/agent/trajectories
```

## 团队模式

一台机器当 server，其他机器作为瘦客户端加入，共享它的 Skill 库：

```bash
# server —— 启动时打印一个 join token
xskill serve --server

# client —— 首次带 token，之后直接 xskill connect 复用连接
xskill connect <host:port> --token <token>
xskill connect
```

`xskill serve` 不带 `--server` 就是单机模式（skill 不出本机）。server 端跑完整 agent 流水（切分 / 聚类 / 撰写 / 灰度）；client 只负责采集 + 脱敏 + 上传本机轨迹，并持有 server 分配给它的那批 skill 的工作副本。灰度按 `client_id` 分桶做真正的用户级 A/B；client 的本地手改只会进隔离分支 `user-staging/<client_id>`，永远碰不到共享的 `main`。

## 辅助 CLI

```bash
xskill registry add    <绝对路径> [--label NAME]
xskill registry remove <绝对路径>
xskill registry list
xskill search traj  <query> [--top-k 5]
xskill search skill <query> [--top-k 5]
```

## xskill 里有哪几个 agent

后台跑着几个 LLM agent，每个职责都很窄：

| Agent | 一句话职责 |
| ----- | ---------- |
| **TaskAgent** | 读一条原始轨迹，按"用户意图"切成若干 Atom（一个 Atom = 用户实际想做的一件事）。 |
| **TaskClusterAgent** | 给每个新 Atom 拍板：关联已有 Skill、并入已有 Skill，还是新开一个 Skill。优先复用，不轻易新建。 |
| **SkillEditAgent** | 当某个 Skill 累计到了足够多的相关 Atom 时，撰写或重写它的 `SKILL.md`（连带必要的脚本 / references）并提交。 |
| **UserEditAbsorbAgent** | 监听你对装出去的 Skill 文件做的手改，把这些改动作为 ground truth 吸回 Skill 库。 |

## 跨 coding agent 支持

输入端"轨迹采集"和输出端"Skill 安装"都是可插拔的。daemon 启动时自动发现你装了哪些 agent，并在常青运行期间持续扫描——你之后再装一个新 agent，不重启也会被自动接管。

**状态图例：** ✅ 端到端验证过 · 🟡 已实现，尚未端到端验证 · 📋 在 roadmap 上

| Coding agent | 状态 | 轨迹采集（输入） | Skill 安装（输出） |
| ------------ | :--: | ---------------- | ------------------ |
| **Claude Code** | ✅ | 原生——自动发现 `~/.claude/projects/`，把每条 session JSONL 桥成轨迹；灰度评估时还会注入 canary 标记。 | 原生——Skill 以 symlink 装到 `~/.claude/skills/<name>/`。 |
| **OpenClaw** | ✅ | 原生——自动发现 `~/.openclaw/agents/`，桥接每个 `*.trajectory.jsonl`。 | 原生——Skill 以**拷贝**方式装到 `~/.agents/skills/<name>/`（OpenClaw 拒收跑出 root 的 symlink，详见 [docs](docs/ecosystem/openclaw.md)）。 |
| **Codex CLI** | 🟡 | 原生——自动发现 `~/.codex/sessions/`，桥接每条 rollout JSONL。 | 原生——Skill 以 symlink 装到 `~/.agents/skills/<name>/`（跨生态共享的 user-scope skill 目录）。 |
| **OpenCode** | 🟡 | 原生——自动发现 `~/.local/share/opencode/opencode.db`（SQLite）。 | 原生——Skill 以 symlink 装到 `~/.agents/skills/<name>/`（与 Codex 共享）。 |
| **Cursor** | 🟡 | 原生——自动发现 `~/.cursor/projects/*/agent-transcripts/`。 | 原生——Skill 以 symlink 装到 `~/.cursor/skills/<name>/`。 |
| **Trae** | 📋 | 暂未支持。 | 暂未支持。 |
| **其他任意 agent** | — | 手动——通过 SDK（`xskill.adapters.submit_trajectory`）提交 `markdown` / `json` / `raw` 格式轨迹。 | 手动——每个 Skill 是一个带 Anthropic 风格 `SKILL.md` + YAML frontmatter 的目录，拷贝或 symlink 到你 agent 的发现目录即可。 |

输出格式遵循 Anthropic 的 `SKILL.md` schema——任何已经能读 Anthropic Skills 的 agent 都能直接读 xskill 的产物。某个 agent 安装失败会被记录并跳过，绝不阻断其他 agent。

## 热更新 Skill

### 单机模式下手动更新 Skill

对 Claude Code / Codex / OpenCode / Cursor，Skill 是以 symlink 装出去的——所以你（或 agent）改装出去的 skill 文件，改的就是 xskill 的源副本，改动**即时生效**。（OpenClaw 例外：它走拷贝安装，改动要等下一次 install / 灰度切版本才同步——xskill 会自动把你对 OpenClaw 那侧的手改回流到源仓。）

xskill 随后会自己把这次改动吸收回去。该 skill 静默约 3 分钟（没有新改动）后，daemon 就把你的手改 commit 到该 skill 的 `main` 分支，作为新的 ground truth。如果这个 skill 当时正在灰度，**手改优先**——staging 候选直接丢弃，因为一次明确的编辑胜过一次 A/B 猜测。

### 团队模式下手动更新 Skill

团队模式下你的本地改动**不被信任**：它会被 commit 成一个 `user-staging/<client_id>` 分支传到远端 server，在下一轮版本迭代时作为参考信息被纳入考虑，但永远不会直接落到共享 `main`。

## 操作系统支持

xskill 是纯 Python (3.11+)，daemon / watcher / SDK 原则上跨平台。当下能诚实声明的覆盖如下：

| 平台 | 状态 | 备注 |
| ---- | :--: | ---- |
| **Linux** (x86_64) | 已测试 ✅ | 开发与 CI 环境。 |
| **Windows 10 / 11** | 支持 ⚠️ | 提供 `scripts/cursor_setup.ps1` 辅助脚本。Skill 安装要创建**目录级 symlink**，Windows 需要**开发者模式**或以管理员身份运行；否则 install 那一步会失败。尚未纳入 CI，欢迎社区反馈。 |
| **macOS** | 应可运行 | 同 POSIX 表面，预期与 Linux 一致，但尚未端到端验证。 |

如果你在 Windows 上想避开 symlink，可以在 `~/.xskill/config.yaml` 里把 `skill_dir` 直接设成你 agent 的 skill 发现目录，跳过自动 install 那步。

## 概念

| 术语 | 含义 |
| ---- | ---- |
| **Trajectory（轨迹）** | 一次 agent 执行，通常是一个 session 的对话记录，xskill 以 `traj_*.md` 形式存盘。 |
| **Atom** | 轨迹中"一个用户意图"对应的最小片段。一条轨迹会切出 1 个或多个 Atom。所有归类判断都发生在 Atom 粒度上。 |
| **Skill** | 一个可复用、prompt 形态的产物：一个 `SKILL.md` 文件 + 可选脚本 / references。每个 Skill 是 `~/.xskill/skill/` 下一个独立带版本的目录。 |
| **Registry** | xskill 监听的目录列表。add 一条路径，daemon 就会一直轮询它。 |
| **Canary（灰度）** | 在真实流量上把现有 Skill 与新候选版本并行比较，根据用户体验分留下更好的那个。 |
| **UX score** | LLM 充当裁判，对 Skill 在某个 Atom 上的实际服务效果打分；据此判输赢。 |

## xskill 与同类项目对比

动手之前，我们调研了 10 个学术 / 开源的 trajectory-to-skill 系统（Hermes、OpenSpace、EvoSkill、AutoSkill、AgentEvolver、MemSkill、EvoAgentX、SE-Agent、SkillRL、GEPA），完整对比见 [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md)。

xskill 做了同类项目都没做的：

- **用真正的 A/B 灰度驱动 Skill 进化**——chat 流量按概率分流，两侧 UX 评分判赢家，全程无人。
- **每个 Skill 独立版本**——每个 Skill 是独立 git 仓库，`staging` 灰度分支 / `main` 主分支分离。
- **团队部署模式**——开箱即用地在组织内共享 skill，兼容多种 coding agent。

## Roadmap

- [ ] **更多 coding-agent adapter**：Trae、Goose、OpenHands、Aider 的双向支持（轨迹采集 + Skill 安装）
- [ ] 原生 MCP server 接口（Skill 暴露为 tool）
- [ ] 基于使用情况的自动淘汰（被检索很多但从未被实际用到的 Skill 自动下线）
- [ ] Web UI：浏览 Skill 库、查看灰度数据、手动放行 / 丢弃
- [ ] Skill marketplace：导入 / 导出可移植的 Skill bundle
- [ ] 多租户 Skill 库（每个团队一个 `skill_dir`）

有想法？请提 [issue](https://github.com/SkillNerds/xskill/issues)。

## 开发

```bash
git clone https://github.com/SkillNerds/xskill
cd xskill
pip install -e ".[dev]"
pytest -q
```

内部设计文档在 [`docs/`](docs/)（中英混排）。

## 贡献

欢迎 PR，请遵守：

1. 先开 issue 描述问题。
2. 加测试或扩展现有测试（无测试不合并）。
3. 公开 API 增量请保持 `xskill/__init__.py` 极简——这一层我们守得很严。

完整贡献流程（含 bug triage 责任田划分）见 [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md)。

## License

MIT (c) [370025263](https://github.com/370025263)，详见 [LICENSE](LICENSE)。

---

<div align="center">

如果 xskill 帮你的 agent 不再"重复造轮子"，请给 [GitHub](https://github.com/SkillNerds/xskill) 一颗星，让更多人看到。

</div>
