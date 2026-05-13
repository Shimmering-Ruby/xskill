# OpenCode 接入面

> [`opencode`](https://github.com/sst/opencode)（`sst/opencode`，Bun + Effect-TS）。本文档基于本机 clone `/home/admin/learn/opencode/`（commit 时间 2025-Q4） + 本机实际 `~/.local/share/opencode/opencode.db` 实测。
>
> OpenCode 在三大维度都跟 Claude Code / Gemini CLI 不一样：**路径走 XDG 而非 `~/.<app>/`、轨迹存 SQLite 而非 JSONL、skill 加载入口多源合并**。是 xskill 接入难度最高的生态。

## 1. 背景：与 CC/Gemini 的设计差异

OpenCode 不走 "`~/.<app>/` 一锅端" 的传统约定，转用 **XDG Base Directory** 规范（数据/配置/缓存/状态各自独立）。这是 Linux 桌面生态主流做法，但对 xskill 来说意味着：

- 不能假定 `~/.opencode/`；要分别走 `Path.data`、`Path.cache` 等。
- `XDG_DATA_HOME` 环境变量能整体把数据根挪走，xskill 探测要看 env。
- Windows 上 `xdg-basedir` lib 的回退行为非标，**部署前必须实测**。

Skill 设计哲学倒是和 CC/Gemini 接近：目录即 skill、YAML frontmatter、`name + description`。**但激活机制完全不同**：OpenCode **不用 tool call**，所有 skill 元数据直接预注入 system prompt 的 `<available_skills>` XML 里，模型自己决定是否 Read 那个 location 的 SKILL.md。

## 2. Skill 加载机制

### 2.1 全局根：`Global.Path`

```
home    = OPENCODE_TEST_HOME ?? os.homedir()
data    = $XDG_DATA_HOME/opencode      # Linux 默认 ~/.local/share/opencode
cache   = $XDG_CACHE_HOME/opencode     # Linux 默认 ~/.cache/opencode
config  = $XDG_CONFIG_HOME/opencode    # Linux 默认 ~/.config/opencode
state   = $XDG_STATE_HOME/opencode     # Linux 默认 ~/.local/state/opencode
tmp     = os.tmpdir()/opencode
```

证据：`packages/core/src/global.ts:9-27`。

### 2.2 加载目录（按代码顺序扫，5 类源）

代码：`packages/opencode/src/skill/index.ts:146-204` `discoverSkills(...)`，按顺序：

1. **全局 external**（受 `OPENCODE_DISABLE_EXTERNAL_SKILLS` flag 控制）：
   - `<home>/.claude/skills/**/SKILL.md`（可单独被 `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` 关）
   - `<home>/.agents/skills/**/SKILL.md`
2. **项目级 external**：从 `directory` 一路向上找到 `worktree` 根，每层扫 `.claude/skills/`、`.agents/skills/`（用 `fsys.up()` 实现）。
3. **OpenCode 项目级**：config 里 `directories()` 返回的目录扫 `{skill,skills}/**/SKILL.md` —— **这就是 `<repo>/.opencode/skills/` 入口**（仓库自带 `.opencode/skills/effect/SKILL.md` 是例子）。
4. **配置显式路径**：`cfg.skills.paths[]`，支持 `~/` 展开、相对 `directory` 拼接。
5. **配置远程拉**：`cfg.skills.urls[]` → `Discovery.pull(url)` → 下载到 `<cache>/skills/<name>/` 后再扫。

### 2.3 加载顺序与冲突

代码：`packages/opencode/src/skill/index.ts:99-113` `add(...)`。

**后写覆盖前写**（`state.skills[name] = info`）+ 重复时打 warn。结合 2.2 的扫描顺序，**优先级低→高**：

```
全局 .claude  <  全局 .agents  <  项目向上 .claude/.agents  <  .opencode/skills  <  config.paths  <  config.urls
```

即 *本地项目的 `.opencode/skills/`* 比 *全局 `~/.agents/skills/`* 优先级高；*显式 config 配置* 比目录扫描更高。

### 2.4 Skill 阅读工具：**无专门 tool**

OpenCode 不像 Gemini 那样有 `activate_skill` tool，也不像 CC 那样有 `Skill` 工具。代码：`packages/opencode/src/skill/index.ts:271-294` `fmt(...)` —— 把所有 skill 的 `(name, description, location)` 注入 system prompt 的 XML：

```xml
<available_skills>
  <skill>
    <name>effect</name>
    <description>Work with Effect v4 / effect-smol TypeScript code in this repo</description>
    <location>file:///path/to/SKILL.md</location>
  </skill>
  ...
</available_skills>
```

**激活路径**：模型看到这段 XML 后，需要某个 skill 时**自己用 Read 工具读 `location`**。这意味着：

- 不需要用户同意弹窗（没有 activate 步骤）；
- xskill 的 SKILL.md 只要进了扫描目录就立刻"可用"；
- 但模型读 SKILL.md 是一次 Read 工具调用，会消耗一次 tool budget。

### 2.5 Symlink 兼容

`Glob.scan(..., symlink: true)`（`index.ts:128`）—— **明确支持 symlink**。xskill 的 symlink 安装策略可以直接复用。

### 2.6 Flag 控制

| Flag | 行为 |
|---|---|
| `OPENCODE_DISABLE_EXTERNAL_SKILLS` | 关掉所有 `.claude/.agents` 扫描 |
| `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` | 只关 `.claude/skills/`，保留 `.agents/skills/` |

xskill 若装到 `.opencode/skills/`（项目级）或 `.agents/skills/`（全局）不受影响。

## 3. Trajectory 摄取机制（最棘手）

### 3.1 存储形态：SQLite + WAL

```
<Global.Path.data>/opencode.db          # 主 DB，drizzle ORM
<Global.Path.data>/opencode.db-shm      # SQLite shared mem
<Global.Path.data>/opencode.db-wal      # WAL log
<Global.Path.data>/log/                 # 日志目录
<Global.Path.data>/storage/migration/   # 迁移文件
<Global.Path.data>/storage/session_diff/ # 二进制快照
```

**Channel 区分**：`packages/opencode/src/storage/db.ts:30-43`。`InstallationChannel ∈ {latest, beta, prod}` → 用 `opencode.db`；其它（dev/nightly）→ `opencode-<channel>.db`。`OPENCODE_DB` env 可整体覆盖（相对路径相对 `data`；绝对路径或 `:memory:` 直接用）。

### 3.2 关键表

```
session         # 一个会话一行
message         # 一条消息一行（JSON in data column）
part            # 消息分块（streaming 用）
session_message # 关联表
todo            # plan-mode TODO
permission      # 权限规则
project         # 项目（包含 worktree 路径）
workspace       # workspace 隔离
event           # event sourcing（本机 0 行，可能仅特定模式启用）
account / account_state / control_account  # 远程账户
```

`session` 表关键列（`packages/opencode/src/session/session.sql.ts:16-53`）：

```
id              SessionID
project_id      → project.id
slug            "quiet-harbor"（adjective-noun，随机）
directory       绝对 cwd                    ← xskill 摄取的 cwd 来源
path            ?
title           人读 session 标题
version         opencode 版本
agent / model   agent 类型 / 模型描述
time_created / time_updated / time_compacting / time_archived
```

**`directory` 字段直接是绝对 cwd**，不像 Gemini 要反查 `.project_root`。

`message` 表（同文件 :55-67）：`data` 列是 JSON 字符串，role/agent/model/cost/tokens/path 都在里头。本机样本：

```json
{"role":"user","time":{"created":1777530036589},"agent":"build",
 "model":{"providerID":"deepseek","modelID":"deepseek-v4-flash"},
 "summary":{"diffs":[]}}
```

```json
{"parentID":"msg_...","role":"assistant","mode":"build","agent":"build",
 "path":{"cwd":"/tmp/dh-e2e-opencode","root":"/"},
 "cost":0.00165858,"tokens":{"total":11710,...}}
```

### 3.3 写入特性

**SQLite 事务，原子写入** —— 不像 JSONL 可以 inotify tail。要么 *轮询 DB*，要么 *挂 plugin hook*。

## 4. xskill 接入设计

### 4.1 安装侧

直接对称 `install_to_claude_code`，目标路径换成两选一：

| 选项 | 路径 | 适用 |
|---|---|---|
| A. 全局 | `<home>/.agents/skills/<name>/`（symlink） | 一份装到位，CC/Codex/openclaw 也能读 |
| B. 项目 | `<repo>/.opencode/skills/<name>/`（symlink） | 只给 OpenCode；和现有 `.opencode/skills/effect/` 同位 |

**推荐 A**（`.agents/skills/`），跨生态收益最大；不过要先在 CC 上验是否也扫这条路径，否则保留两条独立 symlink。Symlink 兼容性已确认（`Glob.scan` 开 `symlink: true`）。

`packages/opencode/src/skill/index.ts:24` 的 `EXTERNAL_SKILL_PATTERN = "skills/**/SKILL.md"` 决定文件夹结构：`<root>/skills/<name>/SKILL.md`。xskill 现成结构一致。

### 4.2 摄取侧：必须改

CC / Gemini 都是 JSONL inotify-tail，xskill 当前 `_adapt_*_jsonl` 桥接器假设这种形态。OpenCode 是 SQLite，**桥接器要换写法**：

```
src/xskill/adapters.py
  + _adapt_opencode_sqlite(db_path, session_id) -> traj_*.md
      # 用 sqlite3 stdlib 读 session + message + part 三表
      # SELECT * FROM session WHERE id=?；directory 即 cwd
      # SELECT data FROM message WHERE session_id=? ORDER BY time_created；逐条 JSON.loads
      # 拼成 timeline 写 traj_*.md
```

**watcher 改 polling**：不能再用 inotify。两个可选策略：

1. **Cursor-based polling**：定期 `SELECT id, time_updated FROM session WHERE time_updated > :last_seen ORDER BY time_updated`，扫到的逐个桥接。`last_seen` 持久化到 xskill 自己的 state 文件。
2. **Plugin hook**：写一个 opencode plugin（`@opencode-ai/plugin`）订阅 `chat.message` 事件，让 OpenCode 主动通知 xskill。`docs/research/ecosystem-integration-survey.md` 提到 15+ hook 点。**侵入式，但实时性好**。

**推荐 1**（cursor polling），无侵入；OpenCode 用 WAL mode（看到的 `opencode.db-wal` 文件），并发读取安全。轮询间隔可以宽（如 30s），因为 skill 蒸馏不需要秒级实时。

### 4.3 KNOWN_ECOSYSTEMS spec 增量

```python
{
    "id": "opencode",
    "source_kind": "sqlite",                       # 新维度：区分 jsonl / sqlite
    "source_path_resolver": _resolve_opencode_db,  # 解析 XDG + env override
    "bridge": "opencode_sqlite",
}
```

`_resolve_opencode_db` 实现：

```python
def _resolve_opencode_db(home_root: Path) -> Path:
    if env := os.environ.get("OPENCODE_DB"):
        if env == ":memory:":
            raise RuntimeError("opencode using :memory: db, no trajectory to ingest")
        return Path(env) if Path(env).is_absolute() else _xdg_data(home_root) / "opencode" / env
    data = _xdg_data(home_root)
    # 多 channel 时 prefer opencode.db；列出所有 opencode*.db 让用户在 registry 配置
    return data / "opencode" / "opencode.db"

def _xdg_data(home: Path) -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
```

### 4.4 不需要做的

- **不需要新 SKILL.md 格式**：YAML frontmatter 完全兼容。
- **不需要处理 cwd 反查**：`session.directory` 直接是绝对路径。
- **不需要考虑 activate_skill / Skill tool 的差异**：OpenCode 走 XML 注入，xskill 落对位置即可。

## 5. 平台差异

| OS | `Path.data` 默认 | 备注 |
|---|---|---|
| Linux | `$XDG_DATA_HOME/opencode` → `~/.local/share/opencode/` | 本机实测 ✅ |
| macOS | 同 Linux（xdg-basedir 在 macOS 上仍走 POSIX 回退） | OpenCode 不用 macOS 标准 `~/Library/Application Support`；未实测 |
| Windows | xdg-basedir 在 Win 上若 `$XDG_DATA_HOME` 未设可能返回 undefined → opencode 初始化会失败 | **未实测，部署前必验**；可能要手动设 `$XDG_DATA_HOME` |

环境变量：

| 变量 | 影响 |
|---|---|
| `XDG_DATA_HOME` | 整体迁移数据根 |
| `OPENCODE_DB` | 单独换 DB 文件名/路径 |
| `OPENCODE_TEST_HOME` | 覆盖 `home`（仅测试用） |
| `OPENCODE_DISABLE_EXTERNAL_SKILLS` | 关 `.claude/.agents` 扫描 |
| `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` | 只关 `.claude` |
| `OPENCODE_DISABLE_CHANNEL_DB` | 不按 channel 区分 db 文件 |

## 6. 已知坑

1. **SQLite 并发**：OpenCode 在跑时持有 WAL，xskill 读应当用 SQLite `mode=ro` 或 `immutable=1` 防止误写；并且要处理 `database is locked` 重试。
2. **Channel 分裂**：用户装了 `latest` + `dev` 两个 channel，会有 `opencode.db` 和 `opencode-dev.db` 两个文件。xskill 应当 *扫描所有 opencode*.db*，不要硬编 `opencode.db`。
3. **deleted session**：OpenCode 有 `time_archived`，archived session 不一定真删。摄取器应当 ignore `time_archived IS NOT NULL` 的 session，避免重复消费陈旧数据。
4. **message.data 是 JSON-in-text**：drizzle 用 `text({ mode: "json" })` 序列化，读出来还是要 `json.loads`；这个 mode 在 SQLite 层就是普通 TEXT 列。
5. **agent 字段**：`session.agent` 可能是 `build` / `plan` / 自定义 agent，xskill 蒸馏时可能想分桶（不同 agent 出的 skill 性质不同）。

## 7. 验收方案（接入完成后跑）

按 `CLAUDE.md` 第 2 条 "E2E 集成测试" 要求：

1. 启动 `xskill serve`，确认 daemon 日志打印 "detected opencode at ~/.local/share/opencode/opencode.db"。
2. 启动 `opencode` 交互会话（`bun --cwd packages/opencode src/index.ts`），完成几轮可蒸馏的对话。
3. 等 xskill cursor poller 抓到 → 走蒸馏 → 写 skill 到 `~/.xskill/skill/<name>/`。
4. 验证 `<repo>/.opencode/skills/<name>/` 或 `~/.agents/skills/<name>/` symlink 创建成功。
5. 新开 opencode 会话，发问触发 skill；模型应当在系统提示词的 `<available_skills>` 看到该 skill，并主动 Read SKILL.md。可用 `OPENCODE_LOG=...` 查 system prompt 落盘验证。
6. Canary 路径：切 staging → symlink target 切到 `.canary/<name>/` → 新会话看到的是 staging 版 SKILL.md。
