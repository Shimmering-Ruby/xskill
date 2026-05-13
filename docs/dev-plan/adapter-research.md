# Codex / OpenCode 适配器调研

> 任务目标:为主 agent 设计 xskill 的 ingester / installer 抽象提供事实依据。
> 数据来源:本机 clone `/home/admin/learn/codex/` (Rust 工作区 `codex-rs/`)、`/home/admin/learn/opencode/` (Bun + Effect-TS) 静态源码;本机 `~/.local/share/opencode/opencode.db` 运行时实测;以及上游官方文档/GitHub。
> 相关已有文档:[`docs/ecosystem/codex.md`](../ecosystem/codex.md)、[`docs/ecosystem/opencode.md`](../ecosystem/opencode.md)、[`docs/ecosystem/CATALOG.md`](../ecosystem/CATALOG.md)。本文是面向 adapter 接口设计的精简提取 + CI 验证可行性新章。

---

## TL;DR

**两个生态都能接入,且 SKILL.md 格式完全兼容**——`name + description` YAML frontmatter 是 CC / Codex / OpenCode 三家公共最小集。Installer 侧只是改目标路径,不改 SKILL.md 产物。**真正的工作量在 ingester 侧**:

| 维度 | Codex | OpenCode | xskill 已有 (CC) |
|---|---|---|---|
| 轨迹形态 | JSONL append-only | **SQLite + WAL** | JSONL append-only |
| 落盘时机 | 流式逐条 (256 槽 mpsc) | SQLite 事务原子 | 流式逐条 |
| cwd 来源 | JSONL 首行 `SessionMeta.cwd` | `session.directory` 列 | 每事件携 `cwd` |
| Watcher 策略 | mtime + offset cursor (复用 CC) | **cursor poll** `SELECT WHERE time_updated > ?` | mtime + offset cursor |
| Skill 安装位置 | `~/.agents/skills/<name>/` (跨生态共享首选) | 同左 (推荐) 或 `<repo>/.opencode/skills/<name>/` | `~/.claude/skills/<name>/` |
| Symlink | std::fs 默认跟随 (待实测) | **明确支持** (`Glob.scan(symlink: true)`) | 已用 |
| 重启加载 | 下个 session 即生效 (启动时扫目录) | 同左 | 同左 |

**最像 CC 的路线**:Codex。JSONL append-only + `SessionMeta` 首行带 `cwd`,几乎可直接复用 `ingest_claude_code_sessions` 的扫描骨架,只换 glob 和适配器。

**最大坑**:OpenCode 用 SQLite,xskill 现有 watcher 假设是 JSONL 文件,**必须新增 `source_kind: sqlite` 维度**到 `_KNOWN_ECOSYSTEMS` spec,并实现 cursor-based poller。这是 ingester 接口抽象时第一优先要解决的设计问题。

---

## Codex CLI

### 轨迹采集

**位置(跨平台)**:`$CODEX_HOME/sessions/{YYYY}/{MM}/{DD}/rollout-{YYYY-MM-DDThh-mm-ss}-{thread-uuid}.jsonl`

- 默认 `$CODEX_HOME = ~/.codex/`,可被 env 整体覆盖。
- macOS / Linux:`~/.codex/sessions/...`
- Windows:`%USERPROFILE%\.codex\sessions\...`;文件名已用 `-` 替换冒号兼容 NTFS,源码 `codex-rs/rollout/src/recorder.rs:1329-1346` `precompute_log_file_info()` 明示。
- Archived:`$CODEX_HOME/archived_sessions/`(默认 ingester ignore)。
- **不是 XDG**——`codex` 走传统 `~/.<app>/` 一锅端,跟 `dirs::home_dir()` 走标准回退。

**格式**:JSONL,每行 = `RolloutLine { timestamp, item: RolloutItem }`。`RolloutItem` 是 tagged union,主要变体:

| `item` 类型 | 含义 |
|---|---|
| `SessionMeta` | **仅首行**,session 元数据(`id`, `cwd`, `timestamp`, `originator`, `cli_version`, `forked_from_id`, ...) |
| `ResponseItem` | 模型返回(message / tool call / function output) |
| `EventMsg` | 事件流(含 `EventMsg::TokenCount`) |
| `TurnContext` | 每 turn 上下文(cwd / approval / sandbox / model) |
| `Compacted` | 上下文压缩事件 |

**样例字段**:`SessionMeta`(`codex-rs/protocol/src/protocol.rs:2701-2732`)

```rust
pub struct SessionMeta {
    pub id: ThreadId,
    pub forked_from_id: Option<ThreadId>,
    pub timestamp: String,
    pub cwd: PathBuf,                          // ← cwd 在首行,无需反查
    pub originator: String,                    // "codex-cli"|"vscode"|"atlas"|"chatgpt"
    pub cli_version: String,
    pub source: SessionSource,
    pub model_provider: Option<String>,
    pub base_instructions: Option<BaseInstructions>,
    // ... 还有十几个可选字段
}
```

**cwd / workspace**:✅ 直接在首行 `SessionMeta.cwd`,与 CC 同款方便。

**turn 边界**:✅ `ResponseItem` 是逐条消息粒度,assistant / tool / user 都是独立行,边界天然。`TurnContext` 行可以作为 turn 切分锚点。

**落盘时机**:**流式逐条 append**(`codex-rs/rollout/src/recorder.rs:1376` `RolloutWriterState`,256 槽 mpsc → 后台 task 写盘),含显式 `Flush` / `Shutdown`。**inotify / mtime tail 友好**,可直接复用 `_scan_seen_sessions` + offset cursor 思路。

**旁路 SQLite**:`codex-rs/rollout/src/state_db.rs:27` 有 `StateDbHandle`,位置 `config.sqlite_home()`(推断 `$CODEX_HOME/state.db` 类),只存 thread index + telemetry,**xskill 不需要触碰**。

### Skill 安装

**Skills 概念**:✅ 有,YAML frontmatter + body 形式,与 CC 兼容。

**加载目录**(`codex-rs/core-skills/src/loader.rs:267-336`),4 层 scope:

| Scope | 路径 | 优先级 (低数值优先) |
|---|---|---|
| Repo | `<repo>/.codex/skills/`、`<repo>/.agents/skills/` | 0 |
| User | `$CODEX_HOME/skills/` (deprecated!)、**`$HOME/.agents/skills/`** | 1 |
| System | `$CODEX_HOME/skills/.system/`(codex 自带 skill,启动时 `include_dir!` 写入) | 2 |
| Admin | `/etc/codex/skills/`(企业 MDM 留位,Unix) | 3 |

**关键**:`$CODEX_HOME/skills/` 已被官方源码标 `/* Deprecated */`(`loader.rs:294`)。**xskill 应只写 `~/.agents/skills/<name>/`**,这是跨 Codex / OpenCode / openclaw 共享的事实标准目录。

**Frontmatter schema**(`codex-rs/core-skills/src/loader.rs:38-46`):

```yaml
---
name: skill-creator          # 必需, ≤ 64 chars
description: ...             # 必需, ≤ 1024 chars
metadata:
  short-description: ...     # 可选, Codex 独有, ≤ 1024 chars
---
```

`name + description` 与 CC / OpenCode 公共,**xskill 不必为 Codex 额外输出**任何字段。`agents/openai.yaml` 是 Codex 独有的同级 metadata 文件(`display_name` / `brand_color` / 等),xskill 也不需要产出。

**Symlink**:源码未发现显式 `follow_links` 开关;Rust `std::fs::read_dir` 默认跟随。**部署前实测一次**(本机未装 codex CLI,无法立刻确认)。

**重启**:不需要;codex 每个新 session 启动时扫加载目录。

### 安装方式 (CI)

- npm 全局:`npm i -g @openai/codex`(官方 README 主推)
- Homebrew (macOS):`brew install --cask codex`
- 启动需要 ChatGPT 账号或 OpenAI API key——**CI 上无法真跑 codex 进程**,但 xskill ingester 是**纯文件扫描**(`ingest_claude_code_sessions` 完全没 spawn `claude` 进程),所以可在 CI 用 **fixture JSONL** 验证整条链路。

---

## OpenCode

### 轨迹采集

**位置(XDG,不是 `~/.<app>/`!)**:

```
data    = $XDG_DATA_HOME/opencode      # Linux 默认 ~/.local/share/opencode/
config  = $XDG_CONFIG_HOME/opencode    # Linux 默认 ~/.config/opencode/
state   = $XDG_STATE_HOME/opencode     # Linux 默认 ~/.local/state/opencode/
cache   = $XDG_CACHE_HOME/opencode     # Linux 默认 ~/.cache/opencode/
```

源码:`packages/core/src/global.ts:9-27`。本机实测 `~/.local/share/opencode/opencode.db` ✅ 存在。

**关键数据库文件**(在 `data` 下):
```
opencode.db          # 主 DB, drizzle ORM
opencode.db-shm      # SQLite shared mem
opencode.db-wal      # WAL log
log/                 # 日志
storage/migration/   # 迁移
storage/session_diff/ # 二进制快照
```

跨平台:
- Linux:✅ 本机实测 `~/.local/share/opencode/`
- macOS:OpenCode 不走 `~/Library/Application Support`,而是 xdg-basedir 在 mac 上 POSIX 回退到 `~/.local/share/opencode/`。**未实测**。
- Windows:xdg-basedir 在 Win 上若 `$XDG_DATA_HOME` 未设可能 undefined;**OpenCode 初始化可能失败**,需要用户手动设 env。**未实测,部署前必验**。

Channel 区分(`packages/opencode/src/storage/db.ts:30-43`):`{latest, beta, prod}` → `opencode.db`;`{dev, nightly}` → `opencode-<channel>.db`。`OPENCODE_DB` env 可整体覆盖。

**格式**:**SQLite + WAL**(不是 JSONL)。关键表:

```sql
-- session.sql.ts:16-53
session(
  id SessionID,
  project_id → project.id,
  slug,                    -- "quiet-harbor" 随机
  directory ABSOLUTE_PATH, -- ← cwd 在这
  title,
  version,
  agent,                   -- "build" / "plan" / 自定义
  model,
  time_created, time_updated, time_compacting, time_archived
)

-- session.sql.ts:55-67
message(
  id, session_id, role, data TEXT(JSON), ...
)
```

`message.data` 是 JSON-in-text(drizzle `text({ mode: "json" })`)。

**样例字段**(本机实测,user message 和 assistant message 各一条):

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

**cwd / workspace**:✅ `session.directory` 直接是绝对 cwd 列。

**turn 边界**:✅ `message` 表按 `time_created` 排序,role 字段区分 `user / assistant`,边界天然。

**落盘时机**:**SQLite 事务,原子提交**。不像 JSONL 可以 inotify tail。

### Skill 安装

**Skills 概念**:✅ 有,扫 5 类源(`packages/opencode/src/skill/index.ts:146-204` `discoverSkills`),按优先级低→高:

```
全局 ~/.claude/skills/  <  全局 ~/.agents/skills/
  <  项目向上 .claude/.agents skills/
  <  <repo>/.opencode/skills/
  <  cfg.skills.paths[]
  <  cfg.skills.urls[]
```

**Frontmatter schema**:同 CC,YAML `name + description`(没找到 OpenCode 独有必填字段)。

**关键路径**:
- 推荐 **`~/.agents/skills/<name>/`**(全局,跨 CC / Codex / openclaw 共享);
- 备选 `<repo>/.opencode/skills/<name>/`(仅 OpenCode)。

**Symlink**:✅ **明确支持**——`Glob.scan(..., symlink: true)` (`index.ts:128`)。xskill 现有 symlink 安装策略可直接复用,**比 Codex 更稳**。

**激活路径**(影响 xskill 但仅在"为什么 skill 没被 invoke"调试时相关):无 `Skill` tool。OpenCode 把所有 skill `(name, description, location)` 注入 system prompt 的 `<available_skills>` XML 块,模型自己用 Read 工具读 `location` 的 SKILL.md。

**Flag**:`OPENCODE_DISABLE_EXTERNAL_SKILLS` 会关掉所有 `.claude/.agents` 扫描;装到 `.opencode/skills/` 或 `~/.agents/skills/` 不受影响。

**重启**:不需要;每个新 session 扫描。

### 安装方式 (CI)

- npm:`npm i -g opencode-ai@latest`
- Bun:`bun install -g opencode-ai`
- 也有 brew / chocolatey / curl one-liner;[`opencode.ai/docs/`](https://opencode.ai/docs/) 列全集。
- 启动需要 LLM provider API key(`anthropic` / `openai` / `deepseek` 任选一);**CI 同样无法跑真进程**。但 ingester 是纯 SQLite 读取(不 spawn `opencode`),CI 用 **fixture .db** 完全可行。

---

## 跨平台路径汇总表

| Agent | macOS | Linux | Windows | 来源 |
|---|---|---|---|---|
| **Codex CLI**(轨迹) | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 同 mac | `%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl`(`:` 已替换为 `-`) | `codex-rs/rollout/src/recorder.rs:1329-1346` |
| Codex CLI(skill 安装首选) | `~/.agents/skills/<name>/` | 同 mac | `%USERPROFILE%\.agents\skills\<name>\` | `codex-rs/core-skills/src/loader.rs:267-336` |
| Codex CLI(deprecated 不要用) | `$CODEX_HOME/skills/` | 同 mac | `%CODEX_HOME%\skills\` | `loader.rs:294` `/* Deprecated */` |
| **OpenCode**(轨迹 DB) | `~/.local/share/opencode/opencode.db`(**非 `~/Library/...`**) | 同 mac;**本机实测 ✅** | `$XDG_DATA_HOME\opencode\opencode.db`,xdg-basedir 在 Win 行为非标 | `packages/core/src/global.ts:9-27` |
| OpenCode(skill 安装首选) | `~/.agents/skills/<name>/` | 同 mac | `%USERPROFILE%\.agents\skills\<name>\` | `packages/opencode/src/skill/index.ts:146-204` |
| OpenCode(项目级备选) | `<repo>/.opencode/skills/<name>/` | 同 | 同 | 同上 |

`home_dir` 取值规则:Linux/macOS 用 `$HOME`,Windows 用 `%USERPROFILE%`(两者底层都是 Rust `dirs::home_dir()` / Node `os.homedir()` 的标准行为)。

### Env 覆盖

| 变量 | 影响 |
|---|---|
| `CODEX_HOME` | 整体覆盖 `~/.codex/` 根(含 sessions / skills / state.db / .system) |
| `XDG_DATA_HOME` | OpenCode 数据根整体迁移 |
| `OPENCODE_DB` | 仅覆盖 OpenCode DB 文件名/路径(相对 `data`;绝对路径或 `:memory:`) |
| `OPENCODE_TEST_HOME` | 覆盖 OpenCode `home`(测试用) |
| `OPENCODE_DISABLE_EXTERNAL_SKILLS` | 关 `.claude/.agents` 扫描 |
| `OPENCODE_DISABLE_CHANNEL_DB` | 不按 channel 区分 db 文件 |

---

## CI 验证可行性

### 关键洞察:xskill ingester 是纯文件读取,不需要 agent 本体运行

`src/xskill/ecosystems.py::ingest_claude_code_sessions` 完全靠扫描磁盘 JSONL(不 spawn `claude` 进程),`install_to_claude_code` 完全靠 symlink(不调 agent API)。**因此 CI 可以只用 fixture 文件验证适配器**,不需要在 runner 上真启动 Codex / OpenCode 进程(那会卡在 API key prompt)。

### Codex CLI 在 GitHub Actions runner

| Runner | 安装命令 | 备注 |
|---|---|---|
| ubuntu-latest | `npm i -g @openai/codex` | npm 全局,无需 sudo (actions runner 默认配好) |
| macos-latest | `brew install --cask codex` 或 `npm i -g @openai/codex` | brew cask 官方支持 |
| windows-latest | `npm i -g @openai/codex` | scoop 支持情况未验证,npm 是最稳路径 |

**真跑 codex 需要 OpenAI API key 或 ChatGPT 登录** —— 不可能在 CI 完成。所以 CI matrix 应当:
- **smoke 层**:`npm i -g @openai/codex && codex --version` 验证安装成功
- **adapter 层**:用 xskill 自己的 `tests/fixtures/codex_rollout_*.jsonl` 跑 `_adapt_codex_rollout_jsonl + install_to_codex(target_root=$tmp)`,完全离线
- **E2E 真跑**:跳过,标 `manual`

### OpenCode 在 GitHub Actions runner

| Runner | 安装命令 | 备注 |
|---|---|---|
| ubuntu-latest | `npm i -g opencode-ai@latest` 或 `curl -fsSL https://opencode.ai/install \| bash` | 二选一 |
| macos-latest | `brew install sst/tap/opencode` 或 `npm i -g opencode-ai@latest` | brew tap |
| windows-latest | `scoop install opencode` 或 `npm i -g opencode-ai@latest` | xdg-basedir 在 Win 上需手动设 `XDG_DATA_HOME`,否则启动失败 |

**真跑 opencode** 同样需要 LLM provider key。但 ingester 是 **SQLite 只读**,xskill 可在 CI 用 fixture `.db`(本机已有 `~/.local/share/opencode/opencode.db` 含 3 session,可脱敏后入仓做 fixture)走 `_adapt_opencode_sqlite + install_to_opencode`,完全离线。

### Fixture 策略 (推荐写进 testing-strategy)

```
tests/fixtures/codex/
  rollout-2026-01-15T10-00-00-deadbeef.jsonl   # 单条小 session, 含 SessionMeta + 2 turn
  rollout-archived-...jsonl                     # 验证 archived 被 ignore

tests/fixtures/opencode/
  opencode.db                                   # 脱敏后的小 DB, 1-2 session
  README.md                                     # 说明这个 db 怎么造的
```

CI matrix 跑:`pytest tests/test_codex_adapter.py tests/test_opencode_adapter.py` 即可全平台验证。**不必装 codex / opencode 本体**。

---

## 未确认 / 需要后续验证

1. **Codex 的 symlink 兼容性**:源码未显式 `follow_links`;Rust `std::fs` 默认跟随,但 `core-skills/src/loader.rs` 的具体 scan 实现没看到显式 follow 配置。需要在装好 codex CLI 的环境(本机当前没有)实测一次 `~/.agents/skills/<name>` 作为 symlink 时 codex 能否正常发现。
2. **Codex `SessionSource` 多源**:同一 user 可能从 `cli` / `vscode` / `atlas` / `chatgpt` 多个入口写入同一 `~/.codex/sessions/`。xskill 蒸馏 skill 是否要按 source 分桶?需要主 agent 设计 ingester 时决定(metadata 字段建议带 `originator` 透传给 adapter)。
3. **Codex `forked_from_id`**:`SessionMeta.forked_from_id` 表示 session 是 fork 出来的。当 fork 链多深?fork 的 trajectory 是否复制 parent 内容还是只 ref?未在源码细读;**对 xskill 短期影响小**(每条 trajectory 仍独立合法),但长期可能导致蒸馏数据重复计数。
4. **OpenCode `event` 表**:本机实测该表 0 行,可能是某种 event sourcing 模式仅在特定 channel / config 启用。如果是 source-of-truth 的话 ingester 要改写。但当前 `message` 表已经够用,未深挖。
5. **OpenCode Windows 行为**:xdg-basedir 在 Windows 上若 `$XDG_DATA_HOME` 未设的回退行为是 npm `xdg-basedir` 包决定的,**未亲验**。最坏情况:OpenCode 在 Win 上根本不能正常工作,xskill 也就不必支持 Win 上的 OpenCode 接入(或要求用户预设 env)。
6. **`~/.agents/skills/` 共享目录的实战兼容**:Codex / OpenCode / openclaw 三家源码都扫这个路径,但**没在同一台机器上同时验证过冲突情况**。例如同一 skill name 在 `~/.agents/skills/foo` 和 `~/.codex/skills/foo` 都存在时各 agent 的去重行为。建议主 agent 设计 `install_*` 时统一只写 `~/.agents/skills/`,避免多写。
7. **Codex npm `@openai/codex` 实际是否对应 codex-rs**:`codex-rs/` 是 Rust 工作区,但 `codex-cli/` 目录也存在(本机 clone 的 `/home/admin/learn/codex/codex-cli`)。npm 上的 `@openai/codex` 究竟是哪一份?如果是 codex-cli (JS) 而非 codex-rs,那 adapter 设计要重做。需要装一次 `npm i -g @openai/codex` 看 `codex --version` + `which codex` + `find ~/.codex -newer ...` 来确认。
8. **OpenCode 在跑时持 WAL 锁**:xskill 读 `opencode.db` 必须用 `?mode=ro&immutable=1` 或类似 connection string,避免触发写锁。需要在 adapter 实现时实测 `sqlite3.connect("file:...?mode=ro", uri=True)` 在 OpenCode 同时跑时是否真不会触发 `database is locked`。
