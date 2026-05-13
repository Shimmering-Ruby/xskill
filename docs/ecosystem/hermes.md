# Hermes 接入面

> Nous Research 的 [`hermes-agent`](https://github.com/collinear-ai)（Python，CLI + Gateway 双形态）。
>
> **本机版本：`Hermes Agent v0.9.0 (2026.4.13)`**，源码 `~/hermes-agent/`（用户自己的工作 fork），二进制 `~/.local/bin/hermes`，数据 `~/.hermes/`。所有事实基于本机源码 + 本机数据双重确认。

## 1. 背景：Hermes 的独特定位

Hermes 不是 IDE coding agent，而是 **多 platform personal agent / RL 训练 harness 二合一**：

- **多入口**：cli / weixin / discord / whatsapp / telegram / acp_adapter 都能拉起 session，统一存到一个数据根。
- **训练侧支持**：仓库自带 `environments/hermes_swe_env/`、`batch_runner.py`、`rl_cli.py`、`trajectory_compressor.py` 等 RL 训练基础设施 —— 这是 Nous 的训练背景使然，xskill 接入只关心普通"用户 session"那部分。

设计上最像 Claude Code（slash command 调用 skill + skill_view tool 懒加载），但**没有 cwd 概念**（不是项目内 agent），**只有 2-tier skill 加载**（本地 + 外部只读），**双写 JSONL + SQLite**（这一条**与 survey 旧数据不一致** —— survey 描述的 "在 cwd 写 `trajectory_samples.jsonl`，opt-in" 是 *训练 batch_runner 的行为*，普通 session 是写 `~/.hermes/sessions/` + `~/.hermes/state.db`）。

## 2. Skill 加载机制

### 2.1 加载目录（源：`hermes_constants.py` + `agent/skill_utils.py:174-235`）

| 层级 | 路径 | 可写 | 备注 |
|---|---|---|---|
| **Local** | `<HERMES_HOME>/skills/`（默认 `~/.hermes/skills/`） | ✅ | 主 skill 目录，agent 自己创建在这里 |
| **External** | `config.yaml` 中 `skills.external_dirs: [...]` 列出的目录 | ❌ 只读 | 多个；同名 local 胜 |
| **Optional** | `<HERMES_HOME>/optional-skills/` 或 `HERMES_OPTIONAL_SKILLS` env | ❌ | 包管理器装的额外 skill bundle |

只有 **2 个事实 tier**（local writable + external read-only）。同名 local 胜（`get_all_skills_dirs()` 把 local 放第一位）。

**支持类别嵌套**：`~/.hermes/skills/<category>/<name>/SKILL.md`。本机实测分类（`ls ~/.hermes/skills/`）：

```
apple, autonomous-ai-agents, creative, data-science, devops, diagramming,
dogfood, domain, email, feeds, ...
```

xskill 写 skill 时可以扁平 `~/.hermes/skills/<name>/` 或归类 `~/.hermes/skills/distilled/<name>/`，都被识别。

### 2.2 Skill 阅读工具

Hermes **有专门工具**，不像 OpenCode/OpenClaw 只走 XML 注入：

| 触发方式 | 入口 |
|---|---|
| Slash 命令 | `/skill-name`（user_invocable=true 时） |
| 模型工具 | `skill_view(name="...", file_path="<path>")` — lazy 读 skill 子文件 |
| Skill 元 | 启动时把 `name + description` 注入 system prompt，body 由模型主动 `skill_view` 拉 |

注入时附带 **per-skill config 块**（`_inject_skill_config()` in `agent/skill_commands.py:82`）：

```
[Skill config: image-lab]
endpoint: ...
model: ...
[Skill has supporting files you can load with the skill_view tool: ...]
```

这是 Hermes 独有的 *动态参数注入* —— `skills.entries.<name>.{env, apiKey, config}` 在 prompt 时刻塞进去。

### 2.3 Frontmatter

Hermes-specific 字段（survey 已总结）：

```yaml
---
name: my-skill
description: ...
version: "1.0"                  # 可选
license: MIT                    # 可选
platforms: ["linux", "darwin"]  # 可选；OS gate
compatibility:                  # 可选；模型/版本兼容性
  hermes: ">=0.9.0"
metadata:
  hermes:
    tags: ["productivity"]
    related_skills: ["other-skill"]
    config:                     # config schema（被 _inject_skill_config 渲染）
      api_key: { type: string, required: true }
---
```

xskill 写最小 `name + description` 就够；hermes-specific 字段不必输出。

### 2.4 Skill 自管（与 xskill 直接重叠）

`tools/skill_manager_tool.py` 提供 `create / edit / patch / delete / write_file / remove_file` 6 个 action，**让 hermes agent 自己写自己的 skill 库**。Hermes 还有内置 `skills_guard.py` 做 security scan。

对 xskill 的意义：**功能重叠**。Hermes 自己就在干"agent 把成功经验沉淀成 skill"的事，但触发是"模型自己决定"，xskill 是"后台扫历史自动蒸馏"。两条路径都写 `~/.hermes/skills/`，**可能互相覆盖**。接入时建议 xskill 用独立子目录（如 `~/.hermes/skills/xskill-distilled/<name>/`）避免命名冲突。

## 3. Trajectory 摄取机制

### 3.1 双写：JSONL + SQLite（v0.9.0 现状）

本机实测两条路径 mtime 同时更新（同时刻 `1778516838`）：

```
~/.hermes/sessions/<YYYYMMDD_HHMMSS>_<xxxxxxxx>.jsonl    ← 文件名后 8 char = sessionId 前缀
~/.hermes/state.db                                         ← SQLite，schema_version=6
~/.hermes/state.db-{shm,wal}                              ← WAL 模式
```

`hermes_state.py` 文件头部注释明示：

> Provides persistent session storage with FTS5 full-text search, **replacing the per-session JSONL file approach**.

—— 即 **SQLite 是新主存，JSONL 是 legacy/导出兼容路径**，但当前版本同时写两份。`message_count` 字段在本机 db 显示 **216 session × 2135 message**，是生产级数据。

### 3.2 SQLite schema（推荐摄取源）

`sessions` 表（关键列）：

```
id TEXT PRIMARY KEY                       ← session id
source TEXT NOT NULL                      ← cli / telegram / discord / weixin / ...
user_id TEXT
model TEXT
model_config TEXT (JSON)
system_prompt TEXT
parent_session_id TEXT                    ← session 树（compaction 分裂）
started_at REAL                           ← epoch seconds, float
ended_at REAL
end_reason TEXT
message_count INTEGER
tool_call_count INTEGER
input_tokens / output_tokens / cache_read_tokens / cache_write_tokens / reasoning_tokens
billing_provider / billing_base_url / billing_mode
estimated_cost_usd / actual_cost_usd / cost_status / cost_source / pricing_version
title TEXT (unique 索引，可空)
```

`messages` 表 + `messages_fts` FTS5 虚表（全文搜索）。

**关键：没有 cwd 字段。** Hermes 不绑定项目；如果 xskill 需要"分桶"，可以按 `source`（platform 入口）分桶。

### 3.3 JSONL schema（兼容路径）

文件首行：

```json
{"role": "session_meta",
 "tools": [/* 完整 tool spec 数组，可数百 KB */],
 "model": "deepseek-v4-pro",
 "platform": "weixin",
 "timestamp": "2026-05-11T20:31:08.015498"}
```

后续行（标准 OpenAI ChatCompletion message 风格）：

```
{role: "user", content, timestamp}
{role: "assistant", content, reasoning, finish_reason, tool_calls?, timestamp}
{role: "tool", content, tool_call_id, timestamp}
```

**特点**：
- **没有 sessionId 字段**：必须从文件名后缀解析（`<YYYYMMDD_HHMMSS>_<8-char>.jsonl` → sessionId 前缀），或者跟 SQLite 的 `sessions.id` join（注意 SQLite 里是完整 id，JSONL 文件名只是前缀）。
- **首行 `tools` 数组巨大**（本机 39KB），里面是模型可见的所有 tool schema。xskill 可忽略 —— trajectory 蒸馏不需要 tool spec 本身。
- 没有 cwd / workspaceDir。

### 3.4 写入特性

JSONL：standard append；inotify 友好（**但 mtime 一致更新两边**，xskill 用 mtime 增量探测时要选一个 source of truth）。

SQLite：WAL 模式（"WAL mode for concurrent readers + one writer"，`hermes_state.py:9`）。

### 3.5 推荐摄取源：SQLite

理由：
- 主存（文件头注释明示）；
- 结构化字段全，token/cost/source 都在；
- 不用从文件名解析 sessionId；
- FTS5 可选做 grep。

xskill 摄取走 SQLite cursor polling（与 OpenCode 同款，`SELECT WHERE ended_at IS NOT NULL AND ended_at > :last_seen` 或 `started_at > :last_seen` 两种策略二选一）。

### 3.6 `request_dump_*.json` 是啥

本机 `~/.hermes/sessions/` 还有 `request_dump_<sid>_<ts>.json` 文件 —— 这是 debug 用的单次 LLM request 完整 dump，**不是 trajectory**。xskill glob 时要排除。

### 3.7 Trajectory 训练专用路径（survey 旧数据来源）

仓库根的 `~/.hermes-agent/` 不是这个 —— 而是 `~/hermes-agent/` 仓库里跑 `batch_runner.py` 训练时，会在 **CWD 下** 写 `trajectory_samples.jsonl` 和 `failed_trajectories.jsonl`，opt-in 通过 `--save_trajectories` 触发。这是 *RL 训练 collector* 行为，与 personal-agent session 写盘**完全不同的两条路径**。survey 描述的是前者，xskill 接入只关心后者（personal-agent session）。

## 4. xskill 接入设计

### 4.1 安装侧

```
target: <HERMES_HOME>/skills/xskill-distilled/<name>/SKILL.md   ← symlink 到 xskill 源
```

走子目录 `xskill-distilled/` 是为了与 hermes 自己的 `skill_manager_tool` 写盘空间隔离，避免命名冲突。Hermes 支持类别嵌套。

不写 `external_dirs` —— 那是用户在 `config.yaml` 手配的只读路径，不归 xskill 管。

env 覆盖：摄取与安装都尊重 `HERMES_HOME`：

```python
hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
```

### 4.2 摄取侧（SQLite 主路径）

```python
src/xskill/adapters.py
  + _adapt_hermes_sqlite(db_path, session_id) -> AdaptedTraj
      # 用 sqlite3 stdlib 只读打开
      # SELECT * FROM sessions WHERE id=?；取 source, model, started_at, end_reason
      # SELECT * FROM messages WHERE session_id=? ORDER BY ... ；按 role/content/tool_call_* 拼 timeline
      # 没有 cwd —— 'cwd' 字段留空或填 'hermes-agent'
      # source 字段作为 'project_root' 替身用，分桶
```

watcher：与 OpenCode 一样 cursor polling，看 `sessions.ended_at` 或 `sessions.started_at` 单调推进。

### 4.3 KNOWN_ECOSYSTEMS spec 增量

```python
{
    "id": "hermes",
    "source_kind": "sqlite",
    "source_path_resolver": lambda home: home / ".hermes" / "state.db",
    "home_env_override": "HERMES_HOME",
    "bridge": "hermes_sqlite",
}
```

`detect_known_ecosystems()` 检测 `~/.hermes/state.db` 存在即注册（**比 JSONL 检测更可靠**，因为 JSONL 是 legacy）。

### 4.4 不需要做的

- **不需要碰 JSONL**：SQLite 是主存；JSONL 是冗余双写。
- **不需要反查 cwd**：Hermes 没有 cwd 概念，xskill 蒸馏侧应当兼容这点（蒸馏算法不应强依赖 cwd 维度）。
- **不需要竞争 skill_manager_tool**：用独立子目录 `~/.hermes/skills/xskill-distilled/` 隔离即可。

## 5. 平台差异

| OS | `HERMES_HOME` 默认 | 备注 |
|---|---|---|
| Linux | `$HOME/.hermes/` | 本机实测 ✅ |
| macOS | `$HOME/.hermes/` | POSIX 同 Linux |
| Windows | `%USERPROFILE%\.hermes\` | `Path.home()` 在 Win 上返回 `%USERPROFILE%`；profile mode 路径分隔符需转换；未实测 |

环境变量：

| 变量 | 影响 |
|---|---|
| `HERMES_HOME` | 整体覆盖 home 根（可指向 Docker `/opt/data`） |
| `HERMES_OPTIONAL_SKILLS` | 覆盖 optional-skills 目录 |

**Profile 模式**：`HERMES_HOME=<root>/profiles/<name>` 允许多 profile 并存（`hermes_constants.py:29-46`），`<root>` 是真正的根。xskill 探测时应当尊重 env，不要硬编 `~/.hermes`。

## 6. 已知坑

1. **JSONL 与 SQLite 双写不一致风险**：写入是分两次的（不在同一事务），极端情况（崩溃）可能一边有数据另一边没有。xskill 摄取 SQLite 不会受影响，但如果有人决策走 JSONL 路径要注意。
2. **`session_meta` 行巨大**：本机首行 39KB（含完整 tool spec 数组）。xskill 用 `json.loads` 单行解析 ok，但用 grep/head/tail bash 工具会被截断。
3. **没有 cwd**：xskill 蒸馏算法若依赖 cwd 做"项目分桶"，需要降级为 `source`（platform 入口）分桶。
4. **`request_dump_*.json` 混在 sessions/ 目录**：glob 不要 `*.json`，必须 `<YYYYMMDD>_<HHMMSS>_<hex>.jsonl`。
5. **`skill_manager_tool` 命名冲突**：Hermes 自己的 agent 可能创建同名 skill。xskill 用子目录隔离。
6. **`schema_version=6` 仍在演进**：`hermes_state.py:273` 有 v3 → v6 migrations 痕迹，将来可能升级。摄取代码用 strict 字段读取，缺字段 raise。
7. **SQLite write lock**：Hermes WAL 模式下并发读写安全；xskill 摄取必须用 `mode=ro&immutable=1` 防误锁。

## 7. 验收方案

按 `CLAUDE.md` 第 2 条 "E2E 集成测试" 要求：

1. 启动 `xskill serve`，确认日志 "detected hermes at ~/.hermes/state.db (schema v6, 216 sessions)"。
2. 通过任意 platform（cli / weixin / discord）发起几轮 hermes 对话，让 SQLite + JSONL 增量更新。
3. 等 xskill cursor poller 抓到 → 蒸馏 → 写 skill 到 `~/.xskill/skill/<name>/`。
4. 验证 `~/.hermes/skills/xskill-distilled/<name>/` symlink 创建成功。
5. 重启 hermes 或触发 `/reload_skills`（如果有），运行 `hermes` 然后 `/<name>` slash command 触发该 skill。
6. 验证 system prompt 注入了该 skill 元数据；模型可用 `skill_view(name=<name>)` 拉到 body。
7. Canary 路径：staging symlink target → `.canary/<name>/` 切换后 hermes 下一个 session 看到新版。
