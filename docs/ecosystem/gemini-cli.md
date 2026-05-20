# Gemini CLI 接入面

> Google 的 `gemini-cli`（`@google/gemini-cli`）。本文档基于上游仓库源码 + 本机 `~/.gemini/` 实际状态。
>
> 调研基线：gemini-cli `0.44.0-nightly.20260512`（`/home/user/learn/gemini-cli`）；本机数据 `~/.gemini/projects.json`、`~/.gemini/tmp/work/chats/session-2026-04-23T06-25-183b8d5f.jsonl` 等。

## 1. 背景：与 CC 同构的 Skill 模型

Gemini CLI 的 Skill 与 Claude Code 高度同构 —— 都遵循 [agentskills.io](https://agentskills.io) 开放标准：

- 目录即 skill；
- 头部 YAML frontmatter 必须有 `name` / `description`；
- 启动时只把元数据注入 system prompt（progressive disclosure）；
- 模型决定何时通过 `activate_skill` 工具把 body + 文件树拉进上下文；
- 激活时会有 UI 同意提示，并把 skill 目录加入 agent 可读路径。

**xskill 写的 SKILL.md 字节级兼容**：实测 `packages/sdk/test-data/skills/pirate-skill/SKILL.md` 与 xskill 当前格式无差异。**不需要写新的 SKILL.md 生成器**。

## 2. Skill 加载机制

### 2.1 加载目录（4 个 tier）

| Tier | 路径 | 说明 |
|---|---|---|
| 1. Built-in | gemini-cli 安装目录内 | 自带 `skill-creator` 等 |
| 2. Extension | gemini extension 内 | `gemini extensions install` 装入 |
| 3. **User** | `~/.gemini/skills/<name>/` 或 `~/.agents/skills/<name>/` | 跨项目共享 |
| 4. **Workspace** | `<cwd>/.gemini/skills/<name>/` 或 `<cwd>/.agents/skills/<name>/` | 跟 repo 走 |

证据：`packages/core/src/skills/skillManager.ts:60-99`、`packages/core/src/config/storage.ts:101-107`。

### 2.2 加载顺序

低优先级 → 高优先级：**Built-in → Extension → User → Workspace**。同名 skill 高 tier 覆盖低 tier。

**同 tier 内** `.agents/skills` 比 `.gemini/skills` 优先级高（`skills.md:53-56`）。`.agents/skills` 是**跨厂商通用路径**，明确为 "interoperable across different AI tools"。

### 2.3 Skill 阅读工具：`activate_skill`

- 单参数 `name: enum`（只能取已发现的 skill 名）。
- **Agent-only**："You cannot invoke this tool manually"（`docs/tools/activate-skill.md:23`）。
- 行为：弹窗征求用户同意 → 注入 SKILL.md body 到对话历史 → 把 skill 目录加入文件读白名单 → 模型继续按 skill 指引执行。

对 xskill 的影响：和 CC 的 Skill 工具一样是黑盒。xskill 只管把目录放对位置，激活的事 gemini 自理。

### 2.4 管理命令

```bash
gemini skills list --all
gemini skills install <git-url-or-path> [--consent] [--scope user|workspace]
gemini skills uninstall <name> [--scope workspace]
gemini skills link <path>          # 软连本地目录到 user skills
```

xskill 自己装 skill 时不必走 CLI，直接落文件即可（与 CC 同款 symlink 套路）。

## 3. Trajectory 摄取机制

### 3.1 轨迹路径（这是 xskill 接入的最关键发现）

```
~/.gemini/tmp/<slug>/chats/session-<timestamp>-<sid8>.jsonl
~/.gemini/tmp/<slug>/.project_root          ← 内容是原始 cwd
~/.gemini/projects.json                     ← 全局 slug ↔ cwd 注册表
```

**slug 取值**：`path.basename(projectPath)`（`packages/core/src/config/projectRegistry.ts:308`），冲突时附后缀。本机实测：

```
$ cat ~/.gemini/projects.json
{
  "projects": {
    "/home/user/work": "work",
    "/home/user/traj2skill": "traj2skill"
  }
}

$ cat ~/.gemini/tmp/work/.project_root
/home/user/work
```

> **历史背景修正**：早期版本（hash-based）的 storage.ts 用 `sha256(projectRoot)` 做目录名（`paths.ts:318` `getProjectHash` 仍存在但被 `Storage.initialize()` 旁路），新版改为 slug + 注册表 + 每目录 `.project_root` marker（`storage.ts:154-244`），cwd 是**可逆推的**。本文档定稿口径以新版为准。

### 3.2 反查 cwd 的两条路径（任选其一）

| 方式 | 路径 | 优劣 |
|---|---|---|
| 中央注册表 | `~/.gemini/projects.json` | 一处全有；但需要小心读写锁 |
| 每目录 marker | `~/.gemini/tmp/<slug>/.project_root` | 自描述，本机数据残缺时仍能恢复；推荐 |

xskill 接入推荐**读 marker**：摄取每个 trajectory 文件时，从其父目录的 `.project_root` 直接拿 cwd，不依赖全局文件。

### 3.3 JSONL 格式

每行一条 JSON 对象。本机样本 `~/.gemini/tmp/work/chats/session-2026-04-23T06-25-183b8d5f.jsonl` 头几行：

**Header**（首行，包一次 session 元数据）：

```json
{"sessionId":"183b8d5f-1383-4398-8172-a529d8c0ae2b",
 "projectHash":"bdfe2652c106ec...",
 "startTime":"2026-04-23T06:25:03.895Z",
 "lastUpdated":"2026-04-23T06:25:03.895Z",
 "kind":"main"}
```

注：`projectHash` 字段在新版仍写出，但目录名已经用 slug；保留 hash 是为了向后兼容。

**User event**（`type: "user"`，`content` 是 `[{text}]` 数组）：

```json
{"id":"559be20e-...","timestamp":"...","type":"user",
 "content":[{"text":"如何安装playwright MCP？"}]}
```

**Gemini event**（`type: "gemini"`，`content` 是字符串，可能附 `thoughts[]` / `toolCalls[]` / `tokens`）：

```json
{"id":"8f1eb7bf-...","timestamp":"...","type":"gemini",
 "content":"I will search for any documentation...",
 "thoughts":[{"subject":"...","description":"...","timestamp":"..."}],
 "tokens":{"input":10456,"output":37,"cached":0,"thoughts":99,"tool":0,"total":10592},
 "model":"gemini-3-flash-preview",
 "toolCalls":[{"id":"grep_search_...","name":"grep_search","args":{...},"result":[...],"status":"success",...}]}
```

**增量更新**（`$set` 行）：

```json
{"$set":{"lastUpdated":"..."}}
```

**同 id 二次写入**：模型先以"无 toolCall"形态落一条 `gemini` event，随后用**相同 `id`** 重写一条加上 `toolCalls`。摄取器要么后写覆盖前写，要么取 *最后一次* 出现的同 id 记录为准。

### 3.4 写入特性

同步 append（`chatRecordingService.ts:411`），每事件即刻落盘 → inotify 友好，可 tail。

但 watcher 需要 **recursive**：根是 `~/.gemini/tmp/`，深度 = `tmp/<slug>/chats/<file>`。

### 3.5 与 CC trajectory 的关键差异

| 维度 | Claude Code | Gemini CLI |
|---|---|---|
| cwd 携带 | 每个 event 自带 `cwd` 字段 | event 内**没有** cwd；需从 `.project_root` 或 `projects.json` 反查 |
| 目录扁平度 | `projects/<encoded-cwd>/<sid>.jsonl`（两层） | `tmp/<slug>/chats/<file>`（三层） |
| 同 id 重写 | 罕见 | 常见（先无 toolCalls，再补 toolCalls） |
| 增量 marker | 无 | `{"$set":...}` 行 |
| event type | 富集（`user/assistant/attachment/permission-mode/...`） | 二元（`user/gemini`） + 少量元事件 |

## 4. xskill 接入设计

### 4.1 整体改动（对称 CC）

```
src/xskill/ecosystems.py
  + KNOWN_ECOSYSTEMS 加 gemini_cli 条目
  + install_to_gemini_cli(skill_path, target_root, side)        # 对称 install_to_claude_code
  + install_all_to_gemini_cli(...)
  + ingest_gemini_cli_sessions(home_root, target_traj_dir, seen_sessions)
  + detect_known_ecosystems() 加 gemini 探测

src/xskill/adapters.py
  + _adapt_gemini_chat_jsonl(...)        # JSONL → traj_*.md
```

### 4.2 KNOWN_ECOSYSTEMS spec 增量

```python
{
    "id": "gemini_cli",
    "source_subpath": ".gemini/tmp",        # 递归扫 <slug>/chats/*.jsonl
    "bridge": "gemini_chat_jsonl",
}
```

### 4.3 install_to_gemini_cli 与 CC 版的唯一差异

目标路径换：

| side | 路径 |
|---|---|
| main | `<target_root>/.gemini/skills/<name>/`（或可选 `<target_root>/.agents/skills/<name>/`） |
| staging | symlink target → `<skill_path>/../.canary/<name>/` |

其余 symlink no-op 检测、备份旧目录、Win Developer Mode 警告等逻辑直接复用。

**关于 `.agents/skills` 标准路径**：考虑提供一个 `xskill ecosystems install --interop-agents` 开关，把 skill 装到 `~/.agents/skills/<name>` 单点，Gemini / OpenCode / Codex / openclaw 同时识别。但要先确认 Claude Code 当前版本是否扫 `~/.agents/skills`；保守做法是每个生态一条独立 symlink，与现有 CC 行为对齐。

### 4.4 _adapt_gemini_chat_jsonl 关键点

```python
def _adapt_gemini_chat_jsonl(jsonl_path: Path) -> AdaptedTraj:
    # 1. 头行：sessionId, startTime
    # 2. cwd：父目录的 ../.project_root 文件（单行文本）
    # 3. timeline：遍历后续行
    #    - {"$set":...}        → 跳过（增量元数据）
    #    - {"type":"user","content":[{"text":...}]}      → user message
    #    - {"type":"gemini","content":<str>,"toolCalls":...} → assistant message
    #      其中 toolCalls 包含 result，等价于 CC 的 tool_use + tool_result 合并
    # 4. 同 id 去重：dict.setdefault(id, ...) 不行（后写要赢），改 dict[id] = record（最后赢）
    # 5. 缺关键字段直接 raise（CLAUDE.md 第 1 条：不写 fallback）
```

### 4.5 Watcher 路径

- 注册根：`~/.gemini/tmp/`
- glob：`*/chats/*.jsonl`（深度恒定 2）
- `.project_root` 不算 trajectory；遇到非 JSONL / 目录跳过即可。

## 5. 平台差异

| OS | `home_dir` | 备注 |
|---|---|---|
| Linux | `$HOME` | 本机验证 |
| macOS | `$HOME` | 应当同 Linux |
| Windows | `%USERPROFILE%` | `projectRegistry.ts:97` 把路径小写化做 key — slug 取 basename 不受影响；symlink 装 skill 同样需 Developer Mode |

环境变量 `GEMINI_CLI_HOME` 可覆盖 `home_dir`（`paths.ts:18-27`）。xskill 的探测器应当尊重它：

```python
home_root = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home())
```

## 6. 已知坑

1. **同 id 重写**：上面 4.4 已述，必须按"后写覆盖前写"消费。
2. **schema 漂移风险**：0.44-nightly 仍在快速迭代，`ChatRecord` 字段可能变。适配器写 strict（缺字段抛错），别 fallback。
3. **`.agents/skills` 跨生态共享**：表面诱人，实际要逐个确认每个生态对 symlink + 优先级的处理。建议先各生态独立路径，**联通方案以后单独立项**。
4. **slug 冲突**：两个目录都叫 `work` 时 gemini-cli 会怎么处理？`projectRegistry.ts:308` 加后缀但具体规则未细读。xskill 端无所谓，因为只用 `.project_root` 反查。
5. **subagent**：Gemini 也支持 subagent，文件名是 `<sessionId>.jsonl`（`chatRecordingService.ts:404`），与主 session 同级 `chats/` 目录混放。摄取器对 main / subagent 分别建 `kind` 字段即可（首行 header 的 `kind: "main"` vs `"subagent"` 字段给出）。

## 7. 验收方案（接入完成后跑）

按 `CLAUDE.md` 第 2 条 "E2E 集成测试" 要求：

1. 启动 `xskill serve`，确认 daemon 日志打印 "detected gemini_cli at ~/.gemini/tmp"。
2. 本地起一个 `gemini` 交互会话，问几个能蒸馏 skill 的问题，让其落 session 文件。
3. 等 xskill watcher 扫到 → 蒸馏 → 写 skill 到 `~/.xskill/skill/<name>/`。
4. 验证 `~/.gemini/skills/<name>/` symlink 创建成功且指向正确源。
5. 新开一个 `gemini` 会话，问能命中该 skill 的问题 → 应当看到 `activate_skill` 弹窗（如果交互模式开启 consent）；非交互 `--consent` 模式下应直接激活。
6. canary 路径：构造 staging 候选，验证 symlink target 切到 `.canary/<name>/` 后 gemini 看到的是新版 SKILL.md。
