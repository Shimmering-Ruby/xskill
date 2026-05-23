<div align="center">

# xskill

**AI agent 做过的事，别让它每次从头再来。xskill 把过往会话里跑通的解法蒸馏成可复用 Skill。**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

[English](./README.md) · **简体中文**

</div>

---

<p align="center">
  <img src="docs/assets/demo.gif" width="764"
       alt="一个 coding agent 列出 xskill 从过往会话里蒸馏出的 Skill">
</p>

## 动态

- **2026-05** — 发布 `v0.5.0`：团队模式（client-server）落地、行号锚定的 atom 切分、`detect-secrets` 自动脱敏、Python 3.9 起跑、运行时不再需要系统 `git`。详见 [Release notes](https://github.com/SkillNerds/xskill/releases/tag/v0.5.0)。
- **2026-05** — MIT 协议开源，PyPI 已上架：`pip install xskill`。
- **2026-05** — Claude Code、Codex、OpenCode 三个生态端到端验证通过；OpenClaw、Cursor 已对接但尚未跑全套 e2e。

## 解决什么问题

agent 每次撞上熟面孔问题，都会把同一套解法重推一遍。你要么再讲一遍，要么自己维护一份 prompt 库——而这份库没人看的时候就慢慢腐烂。

xskill 跑起来之后，这件事不用你管了：

- 跑通过的解题套路自动沉淀成 Skill 文件，agent 自己加载。
- 你照常用 agent 干活，Skill 库自己长出来——没有审核队列，没人需要去"挑选最佳实践"。
- 你手改某个 Skill，xskill 不会回滚——人写的版本被视为 ground truth。
- 新版本只有真的把用户服务得更好，才会顶掉老版本（数据说话，不是 LLM 自己说好）。

## 上手

```bash
pip install xskill          # 需要 Python 3.9+
xskill serve                # 生成 ~/.xskill/config.yaml 模板后退出
```

打开 `~/.xskill/config.yaml`，填好两个模型 endpoint：

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

OpenAI 兼容的 endpoint 都能用。再跑一次 `xskill serve`，它会自动扫机器上装好的所有 agent（Claude Code、Codex、OpenCode、OpenClaw、Cursor）开始监听。如果还有一份历史轨迹归档想一起吃进来：

```bash
xskill registry add /path/to/trajectories
```

## 团队模式：一份共用的 Skill 库

xskill 真正想在组织里铺开的形态是团队模式：一台机器当 server，其他人作为瘦客户端接入，共用 server 上长出来的同一份 Skill 库。

```bash
xskill serve --server                        # 启动后打印 join token
xskill connect <host:port> --token <token>
```

- **库是共享的。** 一个人在自己工作里跑通的解法，可以让全团队复用。
- **轨迹不出 client。** client 上传前先脱敏；只有 server 跑流水线、留完整历史。
- **灰度按人分桶。** Canary 按 `client_id` 分配版本，一个 Skill 改动会先在每个人身上分别衡量，赢了再扩散。
- **手改隔离。** Client 的本地修改进 `user-staging/<client_id>` 分支，永远不会污染 server 上的 `main`。

## 工作原理

几个职责单一的 LLM agent 各管一摊：一个把轨迹切成单一意图的 Atom；一个把每个 Atom 路由到对应 Skill；一个等某个 Skill 攒够素材了就重写它的 `SKILL.md`；一个在真实流量上 A/B 测试新版本，留下赢家。每个 Skill 本身就是一个独立 git 仓库，改了什么、谁改的、能不能回退都有据可查。细节见 [`docs/agent.md`](docs/agent.md)。

## 支持哪些 agent

| Agent | 状态 | 轨迹采集 | Skill 安装 |
| ----- | ---- | -------- | ---------- |
| **Claude Code** | ✅ 已验证 | 扫 `~/.claude/projects/` | symlink → `~/.claude/skills/<name>/` |
| **Codex CLI** | ✅ 已验证 | 扫 `~/.codex/sessions/` | symlink → `~/.agents/skills/<name>/` |
| **OpenCode** | ✅ 已验证 | 读 SQLite `~/.local/share/opencode/opencode.db` | symlink → `~/.agents/skills/<name>/` |
| **OpenClaw** | 🟡 已对接，未跑完 e2e | 扫 `~/.openclaw/agents/` | 拷贝 → `~/.agents/skills/<name>/` |
| **Cursor** | 🟡 已对接，未跑完 e2e | 扫 `~/.cursor/projects/*/agent-transcripts/` | symlink → `~/.cursor/skills/<name>/` |
| **其他 agent** | 手动 | SDK：`xskill.adapters.submit_trajectory` | 自己拷贝 / symlink `SKILL.md` 目录 |

产物遵循 Anthropic 的 `SKILL.md` schema，所以整个库是可移植的——换 agent 也带得走。Trae 在 [roadmap](#roadmap) 上。

## 几个名词

| 术语 | 含义 |
| ---- | ---- |
| **Trajectory（轨迹）** | 一次 agent 执行——一段 session 的完整记录，存成 `traj_*.md`。 |
| **Atom** | 轨迹里单一意图的最小片段。路由判断发生在这一级。 |
| **Skill** | 一个 `SKILL.md` 加可选脚本，住在自己的 git 仓库里，带版本。 |
| **Canary（灰度）** | 现有 Skill 与候选版本在真实流量上做 A/B。 |
| **UX score** | 某个 Skill 在某个 Atom 上服务用户的好坏，从交互本身打 1–10 分。灰度按这个分数选赢家。 |

## 为什么不直接搞个 prompt 文件夹

手维护的 prompt 库没有反馈回路——没东西告诉你哪些条目还能用、哪些早就过时。xskill 把这条回路补上：每个 Skill 版本都在真实流量上跑 A/B，按它产生的用户体验打分，按分数决定留还是淘。我们对比了 10 个已有的 trajectory-to-skill 系统，结论写在 [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md)。

## Roadmap

- 更多 agent adapter：Trae、Goose、OpenHands、Aider
- 原生 MCP server 接口（把 Skill 暴露成 tool）
- Web UI：浏览 Skill 库、看灰度数据
- 按使用情况自动淘汰
- Skill marketplace：导入 / 导出可移植 bundle
- 多租户 Skill 库（每个团队独立 `skill_dir`）

## License

MIT © [370025263](https://github.com/370025263)，详见 [LICENSE](LICENSE)。
