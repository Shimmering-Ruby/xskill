# OpenCode SQLite Fixture

`sample.db` 是一份**最小可用**的 OpenCode 数据库快照，给 xskill 的 OpenCode
adapter (`SqliteIngester`) 单测用。

## Schema 来源

DDL 1:1 复现 `~/learn/opencode/packages/opencode/src/session/session.sql.ts`
里 drizzle ORM 定义的四张表：

- `project (id, directory, time_created)`
- `session (id, project_id, parent_id, slug, directory, title, version,
  time_created, time_updated, …)`
- `message (id, session_id, time_created, time_updated, data TEXT)`
- `part (id, message_id, session_id, time_created, time_updated, data TEXT)`

外加索引 `message_session_time_created_id_idx` / `part_session_idx` /
`part_message_id_id_idx`。`message.data` / `part.data` 都是 JSON-in-text
（drizzle `text({ mode: "json" })`）。

**关键：`message` 表只存信封（role / model / cost / tokens），真实对话内容
在 `part` 表**——一条 message 对应多条 part（type ∈ text / reasoning / tool /
step-start / step-finish / patch）。adapter 读 message + part 还原成
`## User` / `## Assistant` / `## Tool Call` 轨迹。

Schema **不是**手编的——主 agent 已用本机 `~/.local/share/opencode/opencode.db`
（真实跑过 opencode 之后生成）反查 `.schema` 双向确认；项目源头是
opencode drizzle migrations 自己的 SQL 定义，所以即使将来 OpenCode 升级，
重跑 `generate.py` 就能拿到新版 schema。

## 内容

- 1 project（`/tmp/opencode-test-workspace`）
- 1 session（指向 project，agent=build，model=deepseek-v4-flash）
- 2 message（user + assistant）
- 8 part：user 1 条 text；assistant 7 条覆盖全部 type
  （step-start / reasoning / tool[completed] / tool[error] / text / patch /
  step-finish）

ID / timestamp 全 deterministic，让断言可复现。

## 怎么再生成

```bash
cd tests/fixtures/opencode
python3.11 generate.py sample.db
```

产出约 48 KB 的 `sample.db`，可 `sqlite3 sample.db .schema` 自查。

## 为什么不直接 dump 本机 opencode.db？

- 本机 db 含**真实 user cwd / hostname / model API cost**，泄漏隐私
- 本机 db 大小取决于历史，难做"小 fixture"
- drizzle 自己造 schema 的源头是迁移 SQL，本脚本直接重放 DDL **比** dump
  更接近"upstream source of truth"，且天然脱敏
