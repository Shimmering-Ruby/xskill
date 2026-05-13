# OpenClaw 接入面

> [`openclaw`](https://www.npmjs.com/package/openclaw)（npm 包，Node 实现，无公开 git repo）。
>
> **本机版本：`openclaw@2026.5.7 (eeef486)`**，安装位置 `~/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/`。所有事实均基于本机数据 + 官方 docs (`<install>/docs/tools/skills.md`) + d.ts 类型声明。js 源码全部 minified 字符串被清光，**不能从 dist/ 反推路径常量**，需要靠官方文档 + 本机实测交叉验证。

## 1. 背景：OpenClaw 的独特定位

OpenClaw 不是 IDE 内 coding agent，而是 **macOS-first 通用 personal agent**（兼容 Linux）：自带 53+ 个 bundled skill 覆盖 Apple Notes / Reminders / Sonos / Hue / Whisper / Spotify / Slack / Discord / Trello / Things 等"日常生活/工作"工具集成。运行模式上有 Gateway daemon + 多 agent workspace + ClawHub 公开 skill 注册中心。

设计哲学跟 OpenCode 类似（XML 注入 + 无 tool）但**目录分层最复杂**：6 个 skill 加载源，每个 plugin 还能注入自己的 skill，加上 ClawHub 远程注册中心，整体最像 Linux desktop 生态的多层级 mount。

## 2. Skill 加载机制

### 2.1 加载目录（官方权威，按优先级 **高→低**）

来源：`<install>/docs/tools/skills.md:17-30`（本机 install 路径）

| # | 源 | 路径 |
|---|---|---|
| 1 | **Workspace skills** | `<workspace>/skills/` |
| 2 | **Project agent skills** | `<workspace>/.agents/skills/` |
| 3 | **Personal agent skills** | `~/.agents/skills/` |
| 4 | **Managed/local skills** | `~/.openclaw/skills/` |
| 5 | **Bundled skills** | `<install>/skills/`（npm pkg 自带，本机 53 个：`apple-notes`, `things-mac`, `sonoscli`, ...） |
| 6 | **Extra skill folders** | `skills.load.extraDirs[]`（config，最低优先级） |

`<workspace>` 是当前 agent 的工作目录，多 agent 时每个 agent 独立 workspace（默认 agent `main` 的 workspace 在 `~/.openclaw/workspace/`）。

**Plugin skills 是特殊路径**：每个 plugin 在自己根下声明 `skills/`，被 symlink 到 `~/.openclaw/plugin-skills/<plugin-id>/`。本机实测：

```
~/.openclaw/plugin-skills/browser-automation -> 
    ~/.nvm/.../openclaw/dist/extensions/browser/skills/browser-automation
```

Plugin skill 合并入 **Extra dirs 同层**（最低优先级），可被命名冲突的高层 skill 覆盖。

### 2.2 加载顺序与冲突

`Workspace > Project-agent > Personal-agent > Managed > Bundled > Extra`。**高优先级胜**。docs 明示："Same name in multiple places → highest source wins"。

去重用 `fs.realpathSync` 按 inode（survey 已确认）。symlink 兼容。

### 2.3 Skill 阅读工具：无 tool，XML 注入

跟 OpenCode 类似，启动时把所有 eligible skill 元数据格式化为 XML 注入 system prompt（`docs/tools/skills.md:444-455` 给出**精确 token cost 公式**）：

```
total_chars = 195 + Σ (97 + len(name) + len(description) + len(location))
            ≈ 195 + Σ (24 tokens + 字段长度)
```

注入由 `pi-coding-agent` 模块的 `formatSkillsForPrompt` 完成。**没有 activate_skill tool**；模型直接看到 `<location>` 后用普通 file-read 工具拉 SKILL.md。

### 2.4 Agent allowlist（独有）

每个 agent 可以单独配 `agents.list[<id>].skills: [...]` 限制可见 skill 子集 —— skill 装在那不代表 agent 能看到。xskill 落 skill 时只管"装到对应目录"，可见性由用户配置。

### 2.5 Snapshot + Watcher

**Skill snapshot** 在 session 开始时冻结（`docs/tools/skills.md:397-411`）；同一 session 内不重读 skill 库。如果用户启用 `skills.load.watch: true`，watcher 会在 `SKILL.md` 改动时 bump snapshot，下一个 turn 起生效（**"hot reload"**）。

对 xskill 的影响：**xskill 改完 SKILL.md 后，正在跑的 session 不立刻看到，需要新 session 或 watcher 触发 hot reload**。Canary 切 symlink target 同理。

### 2.6 Gating（其他生态都没有的特性）

`metadata.openclaw.requires.{bins,anyBins,env,config}` 在 load 时过滤 skill。例如要求 `bins: ["uv"]` —— `uv` 不在 PATH 上时 skill 隐藏。`os: "darwin"|"linux"|"win32"` 字段限定平台。xskill 输出的 SKILL.md 若不写这些字段，默认始终可用，与其他生态一致。

### 2.7 SKILL.md frontmatter

最小字段同其他生态（`name + description`），可选独有字段：

```yaml
---
name: image-lab
description: Generate or edit images via a provider-backed image workflow
homepage: https://...
user-invocable: true           # 是否暴露为 /image-lab slash 命令
disable-model-invocation: false # 是否阻止模型自主调用
command-dispatch: tool          # 直接 dispatch 到 tool 而不走模型
command-tool: image_generate
metadata:
  {
    "openclaw": {
      "emoji": "🎨",
      "requires": { "bins": ["uv"], "env": ["GEMINI_API_KEY"] },
      "primaryEnv": "GEMINI_API_KEY",
      "install": [...]
    }
  }
---
```

**关键**：`metadata` 必须是 **单行 JSON**（`docs/tools/skills.md:177-180`，"The parser used by the embedded agent supports single-line frontmatter keys only"）。多行 YAML metadata 不识别。xskill 写 SKILL.md 时如果只有 `name + description` 没事；若以后要加 OpenClaw-specific metadata 必须用 inline JSON。

## 3. Trajectory 摄取机制

### 3.1 文件路径（本机实测）

```
~/.openclaw/agents/<agent-id>/sessions/<sid>.trajectory.jsonl
~/.openclaw/agents/<agent-id>/sessions/<sid>.trajectory-path.json    ← pointer
~/.openclaw/agents/<agent-id>/sessions/<sid>.jsonl                    ← runtime session（非 trajectory）
~/.openclaw/agents/<agent-id>/sessions/<sid>.jsonl.reset.<ISO>.<ms>Z  ← reset 备份
```

本机 `~/.openclaw/agents/main/sessions/` 含多个 session（agent `main` 是默认 agent）。`.trajectory-path.json` 是指针文件：

```json
{
  "traceSchema": "openclaw-trajectory-pointer",
  "schemaVersion": 1,
  "sessionId": "06401578-04d1-492d-b225-c11d5d4437f9",
  "runtimeFile": "/home/admin/.openclaw/agents/main/sessions/06401578-04d1-492d-b225-c11d5d4437f9.trajectory.jsonl"
}
```

env 覆盖：
- `OPENCLAW_TRAJECTORY=false`：完全关掉 trajectory 写入
- `OPENCLAW_TRAJECTORY_DIR`：换目录

### 3.2 JSONL schema

每行一条 trace event。本机首行实例：

```json
{
  "traceSchema": "openclaw-trajectory",
  "schemaVersion": 1,
  "traceId": "06401578-04d1-492d-b225-c11d5d4437f9",
  "source": "runtime",
  "type": "session.started",
  "ts": "2026-05-09T09:16:59.878Z",
  "seq": 1,
  "sourceSeq": 1,
  "sessionId": "06401578-04d1-492d-b225-c11d5d4437f9",
  "sessionKey": "agent:main:main",
  "runId": "b30a7490-...",
  "workspaceDir": "/home/admin/.openclaw/workspace",   ← xskill 取"cwd"的位置
  "provider": "deepseek",
  "modelId": "deepseek-v4-pro",
  "modelApi": "openai-completions",
  "data": {
    "trigger": "user",
    "sessionFile": ".../<sid>.jsonl",
    "workspaceDir": "...",
    "agentId": "main",
    "messageProvider": "openclaw-weixin",
    "toolCount": 28,
    ...
  }
}
```

字段保留：每条行都带 `traceSchema/schemaVersion/traceId/seq/sourceSeq/ts/type/data/...`，self-describing，schema 演进容易。

**关键**：OpenClaw 没有传统意义的 cwd（不是 coding agent），但 `workspaceDir`（agent 的 workspace 根）扮演同样角色。xskill 蒸馏时把 `workspaceDir + agentId` 当 "cwd 替身" 分桶。

### 3.3 写入特性

同步 append（survey 已确认："每事件同步入队 → 队列化文件写"），单事件 ≤256 KB，单文件 ≤50 MB，超限发 `trace.truncated` event。**inotify 友好** ✅。

### 3.4 与 SQLite 的关系

OpenClaw 不像 OpenCode/Hermes 那样有主 SQLite 存 session 内容。trajectory 完全走 JSONL。

## 4. xskill 接入设计

### 4.1 安装侧

**推荐**：装到 `~/.agents/skills/<name>/`（Personal agent skills 层，tier 3）。

| 优先级选择 | 路径 | 取舍 |
|---|---|---|
| `~/.openclaw/skills/<name>/`（managed, tier 4） | OpenClaw 专属，与其他生态不互相干扰 | OpenClaw "managed/local"，更聚焦 |
| `~/.agents/skills/<name>/`（personal-agent, tier 3） | 跨生态通用 | 优先级高一档；同时被 Codex / Gemini / OpenCode 看到 |

对称 `install_to_claude_code` 即可，symlink 兼容（OpenClaw 用 realpath 去重）。

Workspace skill（tier 1）需要写 `<workspace>/skills/` 但 workspace 路径是 per-agent 的（默认 `~/.openclaw/workspace/`），xskill 不掌握具体 agent，**不推荐**写 workspace tier。

### 4.2 摄取侧

```python
src/xskill/adapters.py
  + _adapt_openclaw_trajectory_jsonl(jsonl_path) -> AdaptedTraj
      # 1. 扫所有行的 traceSchema == "openclaw-trajectory"
      # 2. 取 session.started 行的 workspaceDir + sessionId + agentId
      # 3. 后续 event 按 type 分发：
      #    - llm.input / llm.output         → user/assistant message
      #    - tool.call / tool.result        → tool 调用
      #    - session.compacted / session.ended → 元事件
      # 4. seq/sourceSeq 保序；ts 提供 timestamp 排序
      # 5. 配套 .trajectory-path.json 提供 sessionId 校对，可忽略
```

### 4.3 KNOWN_ECOSYSTEMS spec 增量

```python
{
    "id": "openclaw",
    "source_kind": "jsonl",
    "source_subpath": ".openclaw/agents",     # 递归扫 <agent>/sessions/*.trajectory.jsonl
    "home_env_override": None,                # 无单一 env；OPENCLAW_TRAJECTORY_DIR 仅换 trajectory 目录
    "bridge": "openclaw_trajectory_jsonl",
    "trajectory_pattern": "*/sessions/*.trajectory.jsonl",  # 显式排除 .jsonl（运行时）和 .trajectory-path.json
}
```

`detect_known_ecosystems` 检测 `~/.openclaw/agents/` 存在即注册。

### 4.4 不需要做的

- **不需要 JSON metadata 转换**：xskill 写最小 `name + description` frontmatter 即可。
- **不需要 cwd 反查**：`workspaceDir` 在每条 trace event 里直接给。
- **不需要碰 SQLite**：OpenClaw 没有主 SQLite trajectory 存储。
- **不必处理 Skill Workshop**：OpenClaw 自己有 `plugins.entries.skill-workshop` 干"从工作中生成 skill"的事，**功能重叠于 xskill** 但默认 disabled。互不冲突；用户可二选一。

## 5. 平台差异

| OS | `~/.openclaw/` | 备注 |
|---|---|---|
| Linux | `$HOME/.openclaw/` | 本机实测 ✅ |
| macOS | `$HOME/.openclaw/` | OpenClaw 主战场（macOS-first），大量 mac-only skill（`apple-notes`/`things-mac`/`peekaboo`） |
| Windows | `%USERPROFILE%\.openclaw\` | 支持但 mac-only skill 不可用（`metadata.openclaw.os: ["darwin"]` gate）；未实测路径细节 |

环境变量：

| 变量 | 影响 |
|---|---|
| `OPENCLAW_TRAJECTORY` | `false` 关掉 trajectory 写入 |
| `OPENCLAW_TRAJECTORY_DIR` | 换 trajectory 目录（独立于 agents 目录） |
| `HERMES_HOME`（无关）| - |

## 6. 已知坑

1. **`.jsonl` vs `.trajectory.jsonl`**：同目录下 `<sid>.jsonl` 是 OpenAI ChatCompletion 格式的 runtime session，`<sid>.trajectory.jsonl` 才是 xskill 要的 trace event 流。**摄取必须匹配 `*.trajectory.jsonl` 而非 `*.jsonl`**，否则误读 runtime 文件。
2. **`.jsonl.reset.<iso>.<ms>Z`**：session reset 时旧 runtime 文件被改名留档。xskill 应当忽略（不在通配里）。
3. **53 bundled skill 中只 13 ready**：本机 `openclaw skills list` 显示大部分 skill 因 `metadata.openclaw.requires.bins` 缺依赖而 "needs setup"。xskill 蒸馏出的 skill 默认无 gating，与 ready bundled skill 同档可见。
4. **Skill snapshot 冷启动**：装完 skill 后必须新开 session（或开 watcher）才生效。xskill canary 切 symlink 需要等下一个 session 才能看到 staging。
5. **macOS-first 倾向**：大量 mac-only skill；Linux 用户能装但实际可用 skill 少。xskill 蒸馏的 skill 默认不带 OS gate，跨平台 ok。
6. **`~/.agents/skills/` 跨生态优先级冲突**：OpenClaw 把 `~/.agents/skills/` 列为 tier 3（personal-agent）；CC 是否也扫这个路径需另行验证。如果 xskill 走"一份 SKILL.md 多生态共享"路线，要确认每家对相同路径的优先级不矛盾。

## 7. 验收方案

按 `CLAUDE.md` 第 2 条 "E2E 集成测试" 要求：

1. 启动 `xskill serve`，确认日志 "detected openclaw at ~/.openclaw/agents"。
2. 在 OpenClaw `main` agent 上完成几轮交互（任意 messageProvider：weixin/cli/discord），让 `~/.openclaw/agents/main/sessions/<sid>.trajectory.jsonl` 出现。
3. 等 xskill watcher 抓到 → 蒸馏 → 写 skill 到 `~/.xskill/skill/<name>/`。
4. 验证 `~/.agents/skills/<name>/` symlink 创建成功并指向正确源。
5. 重启 openclaw（或开 `skills.load.watch: true`）；运行 `openclaw skills list` 看 xskill 写的 skill 是否出现，`openclaw skills info <name>` 显示 `Source` 列应当是 "personal-agent" 或类似。
6. 新开 session 发问触发该 skill；检查 system prompt 里 `<available_skills>` XML 是否包含；模型应当主动读 SKILL.md `<location>`。
7. Canary 路径：staging symlink target → `.canary/<name>/`，重启 openclaw 或触发 watcher 后看到新版。
