<div align="center">

# xskill

**别再反复教你的 AI agent。xskill 把它已经做过的工作,变成它能复用的 Skill。**

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

- **2026-05** — xskill 以 MIT 协议开源,并发布到 PyPI:`pip install xskill`(0.4.2)。
- **2026-05** — Claude Code 与 OpenClaw 已端到端验证;Codex、Cursor 支持已实现。

## 用了之后有什么不同

没有 xskill 时,coding agent 每次遇到一个似曾相识的问题,都会把同一套解法重新推一遍;
而你要么再讲一遍,要么手维护一份 prompt 库,看着它慢慢过时。

xskill 跑起来之后,这部分维护就没了:

- 起作用的解题模式会变成 Skill 文件,agent 自动加载。
- 你照常干活,Skill 库自己更新——没有审核队列,不用手工挑选。
- 你手动改了某个 Skill,xskill 会保留这次改动,并把它当成 ground truth。
- 新版本只有在确实把用户服务得更好时,才会替换掉旧版本。

你照常工作,Skill 库是顺带长出来的副产品。

## 快速开始

```bash
pip install xskill          # 需要 Python 3.11+
xskill serve                # 写出 ~/.xskill/config.yaml,然后退出
```

在 `~/.xskill/config.yaml` 里填两个模型 endpoint:

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

任何 OpenAI 兼容 endpoint 都行。再跑一次 `xskill serve`——它会自动发现并监听你机器上
所有受支持的 agent(Claude Code、Codex、OpenClaw、Cursor)。如果还想把一份旧轨迹存档
也纳入索引,把那个目录注册进来:

```bash
xskill registry add /path/to/trajectories
```

## 团队:一个共享的 Skill 库

团队模式是把 xskill 铺到整个组织的理由。一台机器当 server,其他人作为瘦客户端加入。

```bash
xskill serve --server                        # 打印一个 join token
xskill connect <host:port> --token <token>
```

- **共享库。** 每个 client 都能用上从全团队工作里蒸馏出的 Skill,而不只是自己那份。
- **轨迹隐私。** client 上传前先脱敏;只有 server 跑流水线、持有完整历史。
- **按人做 A/B。** 灰度按 `client_id` 分桶,一个 Skill 改动会先在每个人身上分别衡量,
  再扩散开。
- **本地改动安全。** client 的手改进入隔离的 `user-staging/<client_id>` 分支,
  绝不直接落到共享的 `main`。

## 工作原理

几个职责很窄的 LLM agent 在做事:一个把轨迹切成单一意图的 Atom;一个把每个 Atom
归到某个 Skill;一个在 Skill 攒够素材后重写它的 `SKILL.md`;一个在真实流量上
A/B 测试新版本、留下赢家。每个 Skill 是独立的 git 仓库,所以改动有版本、可回退。
细节见 [`docs/agent.md`](docs/agent.md)。

## 支持哪些 agent

| Agent | 状态 | 轨迹采集 | Skill 安装 |
| ----- | ---- | -------- | ---------- |
| **Claude Code** | ✅ 已验证 | 自动发现 `~/.claude/projects/` | symlink → `~/.claude/skills/<name>/` |
| **OpenClaw** | ✅ 已验证 | 自动发现 `~/.openclaw/agents/` | 拷贝 → `~/.agents/skills/<name>/` |
| **Codex CLI** | 🟡 已实现 | 自动发现 `~/.codex/sessions/` | symlink → `~/.agents/skills/<name>/` |
| **Cursor** | 🟡 已实现 | 自动发现 `~/.cursor/projects/*/agent-transcripts/` | symlink → `~/.cursor/skills/<name>/` |
| **其他任意 agent** | 手动 | SDK:`xskill.adapters.submit_trajectory` | 拷贝或 symlink 那个 `SKILL.md` 目录 |

产物遵循 Anthropic 的 `SKILL.md` schema,所以整个库是可移植的。OpenCode 和 Trae
在 [roadmap](#roadmap) 上。

## 概念

| 术语 | 含义 |
| ---- | ---- |
| **Trajectory(轨迹)** | 一次 agent 执行——一个 session 的完整记录,以 `traj_*.md` 存盘。 |
| **Atom** | 轨迹中单一意图的最小片段。归类判断发生在这一粒度。 |
| **Skill** | 一个 `SKILL.md` 加可选脚本,各自在一个带版本的 git 目录里。 |
| **Canary(灰度)** | 在真实流量上,把现有 Skill 与新候选做 A/B 比较。 |
| **UX score** | 某个 Skill 在某个 Atom 上把用户服务得有多好,按这次交互本身打 1–10 分。灰度保留分高的那个版本。 |

## 为什么不直接维护一个 prompt 文件夹

手维护的 prompt 库没有反馈回路——没有任何东西告诉你哪些条目还有用、哪些已经过时。
xskill 把这个回路接上:每个 Skill 版本都在真实流量上做 A/B,按它产生的用户体验打分,
再据此结果留下或淘汰。与 10 个已有 trajectory-to-skill 系统的对比见
[`docs/research/related-work-survey.md`](docs/research/related-work-survey.md)。

## Roadmap

- 更多 agent adapter——OpenCode、Trae、Goose、OpenHands、Aider
- 原生 MCP server 接口(Skill 暴露为 tool)
- Web UI:浏览 Skill 库、查看灰度数据
- 基于使用情况的自动淘汰
- Skill marketplace:导入 / 导出可移植的 Skill bundle
- 多租户 Skill 库(每个团队一个 `skill_dir`)

## License

MIT © [370025263](https://github.com/370025263),详见 [LICENSE](LICENSE)。
