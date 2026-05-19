# OpenClaw 接入面

> [`openclaw`](https://www.npmjs.com/package/openclaw)（npm 包，Node 实现）。源码仓库：[`github.com/openclaw/openclaw`](https://github.com/openclaw/openclaw)（MIT）。
>
> **本机版本：`openclaw@2026.5.7 (eeef486)`**，npm 全局安装位置 `~/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/`；部分源码克隆到 `~/openclaw/`（主干 `src/` / `packages/` / `extensions/` 受网络限制未完整拉取，本文事实以 npm install 内 `docs/tools/*.md` 官方文档 + 本机 `~/.openclaw/agents/main/sessions/` 实测轨迹 + 仓库根 `AGENTS.md` / `VISION.md` / `CHANGELOG.md` 为准）。npm dist js 已 minified 不能反推常量。
>
> **数据采集时点：2026-05-19**（核对 trajectory.jsonl 事件类型 / 大小限制 / OPENCLAW_TRAJECTORY 形式）。

## 1. 背景：OpenClaw 的独特定位

OpenClaw 不是 IDE 内 coding agent，而是 **macOS-first 通用 personal agent**（兼容 Linux）：自带 53+ 个 bundled skill 覆盖 Apple Notes / Reminders / Sonos / Hue / Whisper / Spotify / Slack / Discord / Trello / Things 等"日常生活/工作"工具集成。运行模式上有 Gateway daemon + 多 agent workspace + ClawHub 公开 skill 注册中心。

设计哲学跟 OpenCode 类似（XML 注入 + 无 tool）但**目录分层最复杂**：6 个 skill 加载源，每个 plugin 还能注入自己的 skill，加上 ClawHub 远程注册中心，整体最像 Linux desktop 生态的多层级 mount。

## 2. Skill 加载机制

### 2.1 加载目录（官方权威，按优先级 **高→低**）

来源：`<install>/docs/tools/skills.md`（本机 install 路径，"Locations and precedence" 节）

| # | 源 | 路径 |
|---|---|---|
| 1 | **Workspace skills** | `<workspace>/skills/` |
| 2 | **Project agent skills** | `<workspace>/.agents/skills/` |
| 3 | **Personal agent skills** | `~/.agents/skills/` |
| 4 | **Managed/local skills** | `~/.openclaw/skills/` |
| 5 | **Bundled skills** | `<install>/skills/`（npm pkg 自带，本机 53 个：`apple-notes`, `things-mac`, `sonoscli`, ...） |
| 6 | **Extra skill folders** | `skills.load.extraDirs[]`（config，最低优先级） |

`<workspace>` 是当前 agent 的工作目录，多 agent 时每个 agent 独立 workspace（默认 agent `main` 的 workspace 在 `~/.openclaw/workspace/`）。

**分组路径**：所有 skill 根额外支持**一级分组**（`<root>/<group>/<skill>/SKILL.md`），方便把第三方 skill 按主题归类，xskill 装 skill 时仍按 `<root>/<name>/SKILL.md` 平铺即可。

**Codex skills 不共享**：OpenClaw **不**把 Codex CLI 原生 `$CODEX_HOME/skills/` 当作 skill 根（在 Codex harness 模式下用 isolated per-agent Codex home）。要把 Codex skill 引入 OpenClaw 需手动 `openclaw migrate codex` 或 `--skill <name>` 选择性拷贝到 OpenClaw workspace。这点提醒 xskill：**给 Codex 装一份 + 给 OpenClaw 装一份不会自动联动**。

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

跟 OpenCode 类似，启动时把所有 eligible skill 元数据格式化为 XML 注入 system prompt（`docs/tools/skills.md` "Token impact" 节给出**精确字符成本公式**）：

```
total_chars = 195 + Σ (97 + len(name_escaped) + len(description_escaped) + len(location_escaped))
```

字段会做 XML escape（`& < > " '` → `&amp; &lt; &gt; &quot; &#39;`），实际长度会膨胀。按 OpenAI 风格 ~4 chars/token 估，**Per skill 97 chars ≈ 24 tokens** + 字段长度本身的 token。

注入由 `pi-coding-agent` 模块的 `formatSkillsForPrompt` 完成。**没有 activate_skill tool**；模型直接看到 `<location>` 后用普通 file-read 工具拉 SKILL.md。

**特殊情况：`claude-cli` backend** — 如果用户用 Claude Code CLI 做后端（`agents.defaults.backend: "claude-cli"`），OpenClaw 会把当 turn eligible skill **物化为 Claude Code plugin** 并通过 `--plugin-dir` 透传给 CC，让 CC 用原生 skill 解析器（同时 OpenClaw 仍把控优先级 / allowlist / gating）。对 xskill 的含义：**用户同一台机同时装 CC + OpenClaw + 用 claude-cli backend 时，OpenClaw 不会"通过 CC 看到 xskill 装在 `~/.claude/skills/` 的 skill"**——它只把 OpenClaw 自己的 eligible skill 镜像给 CC。所以 xskill 必须独立装一份到 OpenClaw 的 skill 根，不能依赖"CC 那份顺带被 OpenClaw 看到"。

### 2.4 Agent allowlist（独有）

每个 agent 可以单独配 `agents.list[<id>].skills: [...]` 限制可见 skill 子集 —— skill 装在那不代表 agent 能看到。xskill 落 skill 时只管"装到对应目录"，可见性由用户配置。

### 2.5 Snapshot + Watcher

**Skill snapshot** 在 session 开始时冻结（`docs/tools/skills.md` "Snapshots and refresh" 节）；同一 session 内不重读 skill 库。Skill 列表能在 session 中段刷新的两种触发：

1. **Skills watcher**（`skills.load.watch: true`，默认 debounce `watchDebounceMs: 250`）— 监听 `SKILL.md` 变化，下一 turn 起生效。
2. **新的 macOS remote node 上线**（Linux gateway + 远端 mac）— bin 探测发现新机具备 mac-only skill 所需二进制时，自动把这些 skill 标为 eligible。

如果用户启用了 **Skill Workshop 插件**（默认 disabled，`plugins.entries.skill-workshop`），它会在写完新 skill 后**主动 refresh snapshot**，比 watcher 更即时。

对 xskill 的影响：**xskill 改完 SKILL.md 后，正在跑的 session 不立刻看到，需要新 session 或 watcher 触发 hot reload**。Canary 切 symlink target 同理。**xskill 装 skill 时若用户已开 Skill Workshop**，理论上两个系统会争同一个 `<workspace>/skills/` 目录，但 OpenClaw 文档明确 Skill Workshop **只**写 `<workspace>/skills`，xskill 走 `~/.agents/skills/` 或 `~/.openclaw/skills/` 的话不会冲突。

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
command-arg-mode: raw           # tool dispatch 时把 raw args 原样转发给 tool
metadata:
  {
    "openclaw": {
      "always": false,
      "emoji": "🎨",
      "os": ["darwin", "linux"],
      "requires": { "bins": ["uv"], "env": ["GEMINI_API_KEY"], "config": ["browser.enabled"] },
      "primaryEnv": "GEMINI_API_KEY",
      "install": [...]
    }
  }
---
```

**关键**：`metadata` 必须是 **单行 JSON**（"The parser used by the embedded agent supports single-line frontmatter keys only"）。多行 YAML metadata 不识别。xskill 写 SKILL.md 时如果只有 `name + description` 没事；若以后要加 OpenClaw-specific metadata 必须用 inline JSON。

**Legacy 兼容**：旧 skill 用 `metadata.clawdbot` 块在 `metadata.openclaw` 缺席时仍被识别（保留向后兼容，新写不要用）。

**`{baseDir}` 模板变量**：SKILL.md 正文里可以写 `{baseDir}` 引用 skill 自身根目录（用于 install 脚本 / 资源相对路径）。xskill 蒸馏内容时如果包含此字符串需当心被错误替换。

## 3. Trajectory 摄取机制

### 3.1 文件路径（本机实测）

同目录下 OpenClaw 同时维护几类文件，xskill 必须仔细区分：

```
~/.openclaw/agents/<agent-id>/sessions/<sid>.trajectory.jsonl              ← xskill 摄取的"结构化时间线"
~/.openclaw/agents/<agent-id>/sessions/<sid>.trajectory-path.json          ← 指针文件，定位 runtime trajectory 位置
~/.openclaw/agents/<agent-id>/sessions/<sid>.jsonl                          ← runtime session（消息事件流，OpenClaw-internal 协议）
~/.openclaw/agents/<agent-id>/sessions/<sid>.jsonl.reset.<ISO>.<ms>Z       ← session reset 时旧 runtime 备份
~/.openclaw/agents/<agent-id>/sessions/<sid>.jsonl.bak-<pid>-<epoch_ms>   ← runtime jsonl 周期性 mirror 备份（本机几十个）
```

本机 `~/.openclaw/agents/main/sessions/` 含 3 个真实 session（`06401578…`、`3a725ac2…`、`70dffe85…`）+ 大量 `.bak-*` 备份。glob 必须 **精确匹配 `*.trajectory.jsonl`**，不能用 `*.jsonl` —— 否则会扫到几十个 runtime backup。

`.trajectory-path.json` 是指针文件：

```json
{
  "traceSchema": "openclaw-trajectory-pointer",
  "schemaVersion": 1,
  "sessionId": "06401578-04d1-492d-b225-c11d5d4437f9",
  "runtimeFile": "/home/admin/.openclaw/agents/main/sessions/06401578-04d1-492d-b225-c11d5d4437f9.trajectory.jsonl"
}
```

env 覆盖（官方 `docs/tools/trajectory.md`）：
- `OPENCLAW_TRAJECTORY=0`：完全关掉 trajectory 写入（**值是 "0"，不是 "false"**——之前文档写错过）
- `OPENCLAW_TRAJECTORY_DIR`：换 trajectory 目录（runtime sidecar 改装到该目录，每个 session 一个 jsonl）

**bundle 导出**：用户在 OpenClaw 内发 `/export-trajectory` 或 `/trajectory` slash 命令时，OpenClaw 会把该 session 的 trajectory + transcript + 模型设置 redact 后打包到 `<workspace>/.openclaw/trajectory-exports/openclaw-trajectory-<session>-<timestamp>/` 目录，每个 bundle 含 `manifest.json / events.jsonl / session-branch.json / metadata.json / artifacts.json / prompts.json / system-prompt.txt / tools.json`。**xskill 不消费这个 export 路径**——那是用户主动 redact 后的 support bundle，不是 live 轨迹。xskill 摄取 live `<sid>.trajectory.jsonl` 即可。

### 3.2 JSONL schema

每行一条 trace event，自描述（`traceSchema/schemaVersion/traceId/seq/sourceSeq/ts/type/data/...`），schema 版本化容易。本机三个 trajectory 文件（56 / 84 / 175 events）实测**所有出现的 event types 共 7 种**（外加官方 `docs/tools/trajectory.md` 列了 `model.fallback_step` 在 chain 退避时也会出现）：

| seq 区间 | type | data 主要字段 | xskill 价值 |
|---|---|---|---|
| 始 | `session.started` | `trigger / sessionFile / workspaceDir / agentId / messageProvider / toolCount / clientToolCount` | **必读**：取 `workspaceDir`、`agentId`、`messageProvider`、`sessionId`、`provider`、`modelId` |
| 始+ | `trace.metadata` | `capturedAt / harness / model / config / plugins / skills` | 可读：拿到当 session **eligible skill 清单**，用于"该 session 是否真见到 xskill 写的某 skill" |
| 每 turn | `context.compiled` | `systemPrompt / prompt / messages / tools / imagesCount / streamStrategy` | 可读：`messages` 是当前 turn 送给模型的对话（**实测可能为空**——transcript 主载体在下一行）；`tools` 含工具定义 |
| 每 turn | `prompt.submitted` | `prompt / systemPrompt / messages / imagesCount` | 与 `context.compiled` 同形，是真正发出去的版本 |
| 每 turn | `model.completed` | `assistantTexts / messagesSnapshot / finalPromptText / promptCache / compactionCount / aborted / timedOut...` | **核心**：`messagesSnapshot` 是 **OpenClaw-internal 消息历史**（`role: user/assistant/custom` + content blocks 数组），xskill **必须从这里取 transcript** |
| 可选 | `model.fallback_step` | source/next 模型 + chain position + advanced/succeeded/exhausted | 可忽略（基础设施事件，与 skill 触发无关） |
| 末 | `trace.artifacts` | `finalStatus / aborted / usage / promptCache / compactionCount / assistantText / toolMetadata` | 可读：取 `finalStatus`（success/failed/...）做"该 trajectory 是否成功结束"判断 |
| 末 | `session.ended` | `status / aborted / externalAbort / timedOut / ...` | 信号事件，确认 session 已结束 |

**首行实例**（`session.started`）：

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
  "runId": "b30a7490-5cc7-405b-b64b-55eba093be8a",
  "workspaceDir": "/home/admin/.openclaw/workspace",
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
    "clientToolCount": 0
  }
}
```

**对 xskill 适配器的重要更正**（之前文档假设了 `llm.input/output` / `tool.call/result` 事件，**实际不存在**）：

- **没有** `llm.input` / `llm.output` / `tool.call` / `tool.result` 事件类型。
- Transcript 唯一可靠来源是 **最后一条 `model.completed` 的 `data.messagesSnapshot`**（每次都是从 session 开始到当前 turn 的完整快照——拿最后一条就拿到了全部）。
- `assistantTexts` 列表是模型本 turn 的纯文本输出；如果 turn 因为 timeout / abort 提前结束，`messagesSnapshot` 里会留下 `[assistant turn failed before producing content]` 占位。
- 单 turn 工具调用细节：从 `messagesSnapshot[i].content` 中按 Anthropic content-block 风格的 `tool_use`/`tool_result` 块解析；trajectory 文件**不**单独发 tool 事件。

**cwd 替身**：OpenClaw 不是 coding agent，没有传统 cwd。每条 event 顶层带 `workspaceDir`，`session.started.data.workspaceDir` 也是 — xskill 蒸馏时把 `workspaceDir + agentId` 当 "cwd 替身" 分桶。runtime `<sid>.jsonl` 首行也带 `cwd` 字段（可作为额外校对来源）。

### 3.3 写入特性

同步入队 → 队列化文件写，**inotify 友好** ✅。三档大小限制（官方 `docs/tools/trajectory.md` "Privacy and limits"）需要 xskill 注意：

| 限制 | 阈值 | 触发后 |
|---|---|---|
| 单事件行 | 256 KiB | 行被截断，trajectory 内插一条截断标记 |
| 单 trajectory **runtime sidecar** | **10 MiB** live 写满 | 停止追加，文件内记 truncation 事件（**修正**：早前文档写"50 MB"指的是 export 限制，写盘上限其实是 10 MiB） |
| **`/export-trajectory`** 一次性导入上限 | 50 MiB / 文件、200,000 runtime events、250,000 total events | 拒绝导出 |

对 xskill 的影响：

1. 10 MiB 单文件上限意味着长 session 不会无限大，扫盘成本可控；但对真很长的 session（>10 MiB 内容）xskill 可能漏掉后半段——蒸馏前需读到的 `model.completed` 越靠近文件尾越完整。
2. 256 KiB 单行截断意味着 `messagesSnapshot` 在巨长 tool result（含 base64 图片等）下可能被砍——xskill 适配器对 JSON parse 失败要容忍。
3. Session 被 sessions disk budget 淘汰时，配套 trajectory 也会被删，xskill watcher 要处理"文件突然消失"的情况。

### 3.4 与 SQLite 的关系

OpenClaw 不像 OpenCode/Hermes 那样有主 SQLite 存 session 内容。trajectory + transcript 全走 JSONL。

## 4. xskill 接入设计

### 4.1 安装侧

**装到 `~/.agents/skills/<name>/`**（Personal agent skills 层，tier 3，跟 Codex / OpenCode 共用此目录）。

| 备选 | 路径 | 备注 |
|---|---|---|
| `~/.openclaw/skills/<name>/`（managed, tier 4） | OpenClaw 专属 | 优先级比 personal-agent 低一档，没必要装这 |
| `~/.agents/skills/<name>/`（personal-agent, tier 3） | 实际选 | Codex / OpenCode 也扫这里，一份装好仨能用 |

Workspace skill（tier 1）需要写 `<workspace>/skills/`，但 workspace 路径是 per-agent 的，xskill 不掌握具体 agent，**不写 workspace tier**。

**install 方法：copy（不是 symlink）**——这一条跟 CC / Codex / OpenCode 不同。
OpenClaw 的 skill discovery 对 personal-agent / workspace / extra-dir 都做
realpath 检查（参 `docs/tools/skills.md` "Security" 节："discovery only accepts
skill roots and `SKILL.md` files whose resolved realpath stays inside the
configured root"）。xskill 的源仓在 `~/.xskill/skill/<name>/`，跑出
`~/.agents/skills/` root，symlink dest 被 openclaw 跳过，session 启动时
trace.metadata.data.skills 里看不到。

所以 `install_to_openclaw` 用 `shutil.copytree` 直接拷过去，dest 是真目录，
过 realpath 检查。代价是 dest 跟源仓解耦，需要单独走"用户改 dest 后回流到
源仓"的桥——详见 §8。

dest 里会落两个 xskill 自带文件：
- `.xskill-install-meta.json`：记 `{source_sha, side, installed_at}`，给回流
  检测和 canary flip 判定用
- 实际 skill 文件（SKILL.md + 引用资源）原样拷贝

### 4.2 摄取侧

```python
src/xskill/adapters.py
  + _adapt_openclaw_trajectory_jsonl(content, metadata) -> (md, meta)
      # 1. 逐行 json.loads；过滤 traceSchema == "openclaw-trajectory"
      # 2. 顶层数据从 session.started 取：
      #      sessionId / agentId / workspaceDir / provider / modelId
      #      / messageProvider / toolCount / runId
      # 3. 收集 trace.metadata 的 skills 清单（用于 "该 session 是否激活了某 skill"）
      # 4. 找到**最后一条 model.completed**——data.messagesSnapshot 是
      #    完整 transcript（OpenClaw-internal 消息格式，content 多为 Anthropic
      #    style content-block 数组）；data.assistantTexts 是当 turn 文本输出
      # 5. 从 trace.artifacts 取 finalStatus / usage / promptCache
      # 6. 把 messagesSnapshot 转 xskill 标准 markdown timeline：
      #      role=user/assistant/custom 转 ## User / ## Assistant
      #      content 中的 tool_use 块转 ## Tool Call
      #      content 中的 tool_result 块转 ## Tool Output
      # 7. ts 字段是 ISO-8601 UTC，可转 epoch 用于 session_start_t
```

需在 `adapt_trajectory()` 的 `format` 分支加 `"openclaw_trajectory_jsonl"`。

`EcosystemSpec` 字段映射（参 `ecosystems.py` 的 `CC_SPEC` / `CODEX_SPEC`）：

```python
OPENCLAW_SPEC = EcosystemSpec(
    name="openclaw",
    source_kind="jsonl",
    sessions_path=lambda home: home / ".openclaw" / "agents",
    sessions_glob="*/sessions/*.trajectory.jsonl",       # 关键：必须带 .trajectory. 中缀
    session_id_from_path=lambda p: p.name.split(".trajectory.")[0],
    cwd_from_content=_read_workspace_dir_from_openclaw_jsonl,  # 从首行 session.started.workspaceDir 取
    adapter_format="openclaw_trajectory_jsonl",
    traj_id_prefix="traj_oc_",
    skills_install_path=_agents_skills_path,             # ~/.agents/skills（与 codex / opencode 共享）
    label="openclaw",
)
```

### 4.3 KNOWN_ECOSYSTEMS spec 增量

```python
{
    "id": "openclaw",
    "source_subpath": ".openclaw/agents",     # 目录存在即注册
    "bridge_subpath": ".xskill/openclaw_sessions",
    "source_kind": "dir",
},
```

`detect_known_ecosystems` 检测 `~/.openclaw/agents/` 存在即注册（与 CC / Codex 一致用 `source_kind=dir`）。

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
| Windows | `%USERPROFILE%\.openclaw\` | **官方推荐 WSL2**（`README.md` 明示 "macOS, Linux, and Windows via WSL2; strongly recommended"），原生 Windows 路径未在本机实测；mac-only skill 在所有非 darwin 系统都被 `metadata.openclaw.os` gate 过滤 |

**远程 macOS 节点（Linux gateway）**：若 OpenClaw gateway 跑在 Linux，但有 macOS node 连进来 + `system.run` 被允许，OpenClaw 会让 mac-only skill 在 Linux gateway 上"可见"——通过 `system.which` / `system.run` 在远端探 bin。Agent 用 `exec(host=node)` 在 mac 节点上跑 skill。**对 xskill 的含义**：xskill 给 Linux 上的 `~/.openclaw/skills/` 写 skill 时，OS gating 默认为空就 ok；如果用户启用了多 host 模式，本地 bin 检测仍然只对本地 host 生效。

环境变量：

| 变量 | 影响 |
|---|---|
| `OPENCLAW_TRAJECTORY` | `=0` 关掉 trajectory 写入（**修正**：之前写 `=false` 是错的，官方 `docs/tools/trajectory.md` 明确是 `=0`） |
| `OPENCLAW_TRAJECTORY_DIR` | 换 trajectory 目录（独立于 agents 目录） |

## 6. 已知坑

1. **`.jsonl` vs `.trajectory.jsonl` vs `.jsonl.bak-*` vs `.jsonl.reset.*`**：同目录下四类文件混在一起：
   - `<sid>.trajectory.jsonl` — xskill **唯一**要扫的，trace event 流
   - `<sid>.jsonl` — runtime session（OpenClaw-internal 协议，`type: message/custom/...`），含 transcript 但不是 xskill 的标准入口
   - `<sid>.jsonl.bak-<pid>-<epoch_ms>` — runtime jsonl 周期 mirror（本机几十个），**必须排除**
   - `<sid>.jsonl.reset.<iso>.<ms>Z` — session reset 时旧 runtime 改名留档，**必须排除**
   - `<sid>.trajectory-path.json` — 指针 json，xskill 也忽略
   → glob 用 `*/sessions/*.trajectory.jsonl`，靠 `.trajectory.` 中缀精确锁定。
2. **trajectory 事件 schema**：**没有** `llm.input/output` / `tool.call/result` 事件（早前文档假设）。实际只有 `session.started / trace.metadata / context.compiled / prompt.submitted / model.completed / model.fallback_step / trace.artifacts / session.ended` 共 8 种。Transcript 唯一权威源是 **最后一条 `model.completed` 的 `data.messagesSnapshot`**（每条 model.completed 都带从 session 起点到当前 turn 的完整快照）。
3. **53 bundled skill 中只 13 ready**：本机 `openclaw skills list` 显示大部分 skill 因 `metadata.openclaw.requires.bins` 缺依赖而 "needs setup"。xskill 蒸馏出的 skill 默认无 gating，与 ready bundled skill 同档可见。
4. **Skill snapshot 冷启动**：装完 skill 后必须新开 session（或开 watcher）才生效。xskill 灰度换版本（copy-overwrite dest）需要等下一个 session 才看得到新版。**Skill Workshop plugin**（若用户开）会主动 bump snapshot，但只看 `<workspace>/skills/` 这一档。
5. **macOS-first 倾向**：大量 mac-only skill；Linux 用户能装但实际可用 skill 少。xskill 蒸馏的 skill 默认不带 OS gate，跨平台 ok。
6. **`~/.agents/skills/` 跨生态共用**：OpenClaw / Codex / OpenCode 都扫这个目录。但 **CC / Codex / OpenCode 走 symlink 装到这里，OpenClaw 走 copy 装到这里**（同目录混合两种 entry——symlink dirs 给前三家，真目录给 openclaw）。原因：openclaw 拒收 escape-root 的 symlink（见下一条）。CC 走 `~/.claude/skills/`，独立。
7. **openclaw 拒收 symlink-escape**（**关键**）：openclaw 的 skill discovery 对 personal-agent / workspace / extra-dir 都做 realpath 安全检查——resolved 路径必须留在 configured root（`~/.agents/skills/`）里。xskill 默认把源仓放 `~/.xskill/skill/<name>/`，跑出 root，symlink 被 openclaw skip。**xskill 给 openclaw 装时单独用 `shutil.copytree`**，dest 是真目录。代价是 dest 跟源仓解耦，详见 §8 怎么处理用户改和灰度。
8. **OPENCLAW_TRAJECTORY=0 关闭场景**：用户若关掉 trajectory 写入，xskill 无 input。`detect_known_ecosystems` 只看 `~/.openclaw/agents/` 目录存在，不查 trajectory 启停；如果该目录有但 `*.trajectory.jsonl` 持续无新文件，xskill ingester 沉默是预期行为，不要 panic。
9. **claude-cli backend 镜像 skill**：用户在 OpenClaw 里把 backend 设为 claude-cli 时，OpenClaw 会**临时**把 OpenClaw 自己当 session eligible 的 skill 物化为 CC plugin（带 `--plugin-dir`）。xskill 给 `~/.claude/skills/` 写的 skill 在这种 backend 模式下**不会被 OpenClaw 看到**——所以两边都要装。
10. **Skill Workshop vs xskill 边界**：用户启用 Skill Workshop 后，OpenClaw 自己也会从交互中生成 skill 写到 `<workspace>/skills/`；xskill 走 `~/.agents/skills/` 或 `~/.openclaw/skills/` 不冲突。冲突场景：用户同名 skill。OpenClaw 优先级 workspace > personal-agent > managed，所以 xskill 默认装到 personal-agent 这档时**会被 Skill Workshop 同名 skill 盖掉**。

## 7. 验收方案

验收链路必须真起 openclaw 跑一次真 LLM —— 早期"装好 SKILL.md exists 就过"
的伪 e2e 漏掉了 symlink-escape bug，不再用那种检查方式。

### 真 e2e（必须）

`make e2e-openclaw-real` 或 `tests/docker_e2e/openclaw_real_llm/run_host.sh`：

1. 起 xskill daemon（fake LLM 给 daemon 自己用，因为 daemon 这环不需要打真 LLM）
2. 触发 `install_all_to_openclaw` 把 demo-skill 装到 `~/.agents/skills/`
3. 跑真 `openclaw agent --local --agent main --message "..."` 真打 DeepSeek
4. 断言：
   - 产生的 `<sid>.trajectory.jsonl` 里 `trace.metadata.data.skills.entries`
     **含 xskill 装的 skill 名字** —— 证明 openclaw 真的扫到了 dest
   - xskill `~/.xskill/openclaw_sessions/` 桥出 `traj_oc_*.md` —— 证明
     ingester 正常摄取

### 单测（次要）

- `tests/test_openclaw_adapter.py`：adapter 解析 / ingest 桥接 / install 三件
  各自单测。**不**依赖真 LLM。fixture 在 `tests/fixtures/openclaw/`。
- `TestInstallToOpenClaw`：断言 dest 是真目录（不是 symlink），改源仓后 dest
  不会自动变（验证不是 symlink）
- `TestReverseSync`：dest 有用户改 → reverse_sync 后 source 同步、source mtime
  被 touch；dest 没改 → no-op
- `TestCanaryFlipWithPendingDestEdit`：dest 有未回流改 → `install_to_openclaw`
  先回流再 copy，用户改不丢

### 已知 edge case

- `OPENCLAW_TRAJECTORY=0` 关掉 trajectory：xskill `detect_known_ecosystems`
  只看目录在不在，不查 trajectory 启停 → ingester 沉默是预期行为
- `OPENCLAW_TRAJECTORY_DIR=...` 换目录：xskill 当前 `sessions_path` 写死
  `~/.openclaw/agents/`，**不 follow 这个 env override**。已知限制，跟 CC /
  Codex 一致
- session 在 10 MiB 写满后被 trim：adapter 对截断行的 `json.loads` 异常容忍跳过
- claude-cli backend 用户：xskill 装一份到 `~/.claude/skills/` 给 CC，再装一份
  到 `~/.agents/skills/` 给 openclaw（两份是必须的，因为 claude-cli backend 下
  openclaw 不会看 CC 装的那份）

## 8. 怎么接入

### 已 ship 的部分

`src/xskill/ecosystems.py` / `adapters.py` / `server.py` 三处改动已合入：
`OPENCLAW_SPEC` 仿 `CODEX_SPEC`、`install_to_openclaw` /
`ingest_openclaw_sessions` / `install_all_to_openclaw` 三个 wrapper、
`_adapt_openclaw_trajectory_jsonl` adapter（从 `model.completed.data.messagesSnapshot`
取 transcript）、server startup hook 加 `elif eco == "openclaw":` 分支。

单测 23 个全过（`tests/test_openclaw_adapter.py`），fixture e2e scenario 也
全过（`tests/docker_e2e/scenarios/runtime_openclaw_detect/`）。

### 真 e2e 暴露的 bug + 修复方案

**bug**：真起 openclaw + 真打 DeepSeek 跑出来发现，openclaw 看到 xskill
装到 `~/.agents/skills/<name>` 的 symlink，但 **realpath 跑出 root 就拒收**
（symlink-escape 安全检查）。结果是 `trace.metadata.data.skills` 里没有
xskill 装的 skill —— openclaw 实际看不到。

老 e2e 漏掉这个是因为它只校验 `dest/SKILL.md exists`，从来没起真 openclaw
问"你看见了吗"。

**修复方向**：openclaw 单独走 `shutil.copytree` 而不是 symlink。dest 是真
目录，过 realpath 检查。代价是 dest 跟源仓解耦，要加一座"dest 用户改回流
到源仓"的桥来兜住 absorb / push-edit 链路。

**灰度切版本**：
- standalone：`pick_side` 哈希分桶不变，flip 时机从"切 symlink 目标"改成
  "copy-overwrite dest 目录"。在 `JsonlIngester(OPENCLAW_SPEC)` 的 poll 循环
  里发现新 session → 跑 pick_side → 跟 install_history 比对 → 不一样就触发
  `install_to_openclaw(side=new_side)` 重 copy
- team：server 推 manifest 决定 side / sha，client reconcile checkout 后
  调 `_install_to_ecosystems`。把 `install_to_openclaw` 加进 installer 字典
  即可，链路自动通

**用户改 dest 的回流桥**：
- watcher 每轮先扫所有 openclaw 装过的 dest，跟 dest 里
  `.xskill-install-meta.json.installed_at` 比 mtime，发现用户改 + 静默 3 分钟
  → 抢源仓锁 → dest 内容拷回源仓 → touch 源仓 mtime 让 absorb 下一轮看到
- 之后原有 absorb（standalone）/ push_user_edits（team）就当成普通源仓改动
  处理，零额外逻辑
- `install_to_openclaw` 在 copytree 之前也跑一次回流检查，保证灰度切版本
  不会静默吞掉 dest 上没回流的用户改

**冲突边界**：源仓和 dest 同时改 → mtime 仲裁，新的胜，log warning 记一笔。
不做手工冲突 UI（corner case）。

完整设计细节 + 改动清单见 [`openclaw-install-fix.md`](./openclaw-install-fix.md)。

### 要改的三个文件（已 ship，但 install_to_openclaw 即将按上面方案重写）

**`src/xskill/ecosystems.py`** — 仿照 Codex 那一组定义照抄一份 OpenClaw 版：

- 加一个 `OPENCLAW_SPEC`（参考 `CODEX_SPEC`，路径换成 `~/.openclaw/agents`，glob 用 `*/sessions/*.trajectory.jsonl`，skill 装到 `~/.agents/skills/` 跟 Codex 共用一个目录）
- 加 `install_to_openclaw` / `ingest_openclaw_sessions` / `install_all_to_openclaw` 三个一行 wrapper（看 `install_to_codex` 那几个怎么写的就照着写）
- `_KNOWN_ECOSYSTEMS` 列表末尾加一行 `{"id": "openclaw", "source_subpath": ".openclaw/agents", "bridge_subpath": ".xskill/openclaw_sessions", "source_kind": "dir"}`

**`src/xskill/adapters.py`** — 加一个 OpenClaw 的轨迹格式适配器：

- `adapt_trajectory` 里加 `"openclaw_trajectory_jsonl"` 分支
- 实现 `_adapt_openclaw_trajectory_jsonl(content, metadata)`：从 `session.started` 取 sessionId / workspaceDir / agentId / model 这些元信息，从**最后一条 `model.completed.data.messagesSnapshot`** 取对话记录，转成 xskill 的标准 markdown timeline 格式。content block 类型是 `text` / `tool_use` / `tool_result`，按 Anthropic 风格解析就行。

**`src/xskill/server.py`** — 启动 hook 里照着 `elif eco == "codex":` 那段抄一份 `elif eco == "openclaw":`，调用上面新加的 `install_all_to_openclaw` + `JsonlIngester(OPENCLAW_SPEC).start()`。imports 加上 `OPENCLAW_SPEC` 和 `install_all_to_openclaw`。

### E2E docker 测试

`tests/docker_e2e/scenarios/runtime_ecosystem_detect/` 已经有 Claude Code 版的 scenario 作为现成模板。复制一份目录改名 `runtime_openclaw_detect/`，然后把里头三个东西换掉：

- `fixtures/cc_session.jsonl` → 换成 `openclaw_session.trajectory.jsonl`。源数据用本机 `~/.openclaw/agents/main/sessions/06401578-…trajectory.jsonl`，把里面的私人内容（用户消息文本、绝对路径）替换成 `[REDACTED]` 即可。
- `actions.sh` 里 `mkdir -p $TESTHOME/.claude/projects/foo` 改成 `mkdir -p $TESTHOME/.openclaw/agents/main/sessions`，`cp` 的目标文件名后缀加上 `.trajectory.jsonl`。
- `assertions.sh` 里把 `claude_code` 改成 `openclaw`，`traj_cc_*` 改成 `traj_oc_*`。再加一条断言：`pre_state` 里预放一个 fake skill `~/.xskill/skill/demo-skill/SKILL.md`，断言 server 启动后 `$TESTHOME/.agents/skills/demo-skill/SKILL.md` 存在（验证 `install_all_to_openclaw` 也跑通了）。

跑法：`tests/docker_e2e/run.sh runtime_openclaw_detect`，或 `make e2e` 跑全套。

### 单元测试

在 `tests/` 下加两个文件：

- `test_adapters_openclaw.py`：拿同一份脱敏 fixture，断言 adapter 解析后 timeline 里有 user / assistant 消息，session_id / workspaceDir 等元数据出现在 meta。
- `test_ecosystems_openclaw.py`：tmp_path 造一个 fake `.openclaw/agents/main/sessions/xxx.trajectory.jsonl`，验证 `detect_known_ecosystems` 检出 openclaw、`install_to_openclaw` 把 skill 装到了 `.agents/skills/`、`ingest_openclaw_sessions` 桥出了 `traj_oc_*.md`。

### 一个真实风险

本机三份轨迹都是微信纯文本对话，**没有真实工具调用的样本**。adapter 里 `tool_use` / `tool_result` 那段是按 Anthropic 标准格式假设写的，没法在动手前 100% 确认 OpenClaw 真的就这样存。

实现前最好先在本机用 OpenClaw 发一句明确触发工具的话（比如 `用 brave-search 查 xxx`），把那份新的 trajectory.jsonl 当 fixture，然后 adapter 跟着真实数据写。这样不会上线后第一个真工具会话解析出空。

### 同步要改的文档

- `docs/ecosystem/CATALOG.md` 把 OpenClaw 行从"未支持"改成"已支持"
- `README.md` / `README.zh-CN.md` 的支持生态列表加上 OpenClaw
