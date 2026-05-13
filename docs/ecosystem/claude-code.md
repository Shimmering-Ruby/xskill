# Claude Code 接入面

> 这份文档总结 xskill 与 Claude Code（CC）之间两条接口：xskill 写出去的 SKILL.md 怎么被 CC 发现 / 加载；CC 写下的会话轨迹怎么被 xskill 摄取。
>
> 数据来源：xskill 自身代码（已在生产用）、`docs/research/ecosystem-integration-survey.md`、本机 `~/.claude/` 实测。

## 1. 背景：CC 的 Skill 设计哲学

CC 的 Skill 是 **"自包含目录 + YAML frontmatter"** 的开放标准（与 [agentskills.io](https://agentskills.io) 对齐）。模型不持续吃 skill 内容；启动时**只把 `(name, description)` 注入 system prompt**，运行时由模型自己决定何时通过 `Skill` 工具把某个 skill 拉进上下文。这是 *progressive disclosure*，目的是把"专业能力库"做大而不撑爆窗口。

对 xskill 这种"自动蒸馏 skill"的工具来说，关键含义是：**只要把目录放对位置 + `SKILL.md` 头部写对 `name`/`description`，CC 就会自己发现**。xskill 不必做注入或 hook。

## 2. Skill 加载机制

### 2.1 加载目录

| 层级 | 路径 | 备注 |
|---|---|---|
| **用户级** | `~/.claude/skills/<name>/SKILL.md` | xskill 默认走这里 |
| **项目级** | `<repo>/.claude/skills/<name>/SKILL.md` | 跟 repo 走团队共享 |
| **内置 / plugin** | CC 安装目录内 | 不可写 |
| 其他 | `--add-dir` CLI 参数、policy-managed 路径 | 企业部署用，xskill 不涉及 |

Windows 上对应 `%USERPROFILE%\.claude\skills\<name>\`。

### 2.2 加载顺序与冲突

**项目 > 用户 > 内置**。CC 按当前 cwd memoize 一次扫描结果；同名 skill 项目级胜。这意味着 xskill 装到用户级时，开发者可以在自己项目里临时覆盖一个同名 skill 做实验，不影响其它项目。

### 2.3 Skill 阅读工具

CC 内部叫 `Skill` 工具，agent-only，由模型决定何时调用。用户在 chat 里也可以用 `/<skill-name>` 显式触发某些 `user_invocable: true` 的 skill。xskill 写出的 skill 默认不标这个字段，走纯 LLM 自主激活路径。

### 2.4 SKILL.md 最小骨架

```markdown
---
name: my-skill
description: 一句话讲清楚什么时候用，给 LLM 判断用的
---

# Body

模型激活后看到的全部 markdown 内容。可以引用 scripts/、references/。
```

`name` 必须匹配目录名；`description` 是匹配触发的关键，写得越具体越容易被正确激活。

## 3. Trajectory 摄取机制

### 3.1 轨迹文件位置

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl
```

`<encoded-cwd>` 编码规则：把 cwd 的所有 `/` 替换成 `-`。例：

| 原 cwd | encoded |
|---|---|
| `/home/admin/traj2skill` | `-home-admin-traj2skill` |
| `/home/admin/work` | `-home-admin-work` |

本机实测：
```
~/.claude/projects/-home-admin-traj2skill/
~/.claude/projects/-home-admin-claude-code/
~/.claude/projects/-home-admin-dataharness/
...
```

Windows 上路径变成 `%USERPROFILE%\.claude\projects\<encoded-cwd>\<sid>.jsonl`，且 `\` 和 `:` 也会变成 `-`（**未实测**，部署前应在 Win 上验一次）。

### 3.2 JSONL 格式

每行一个 event，常见 `type`：

| type | 含义 |
|---|---|
| `user` | 用户输入 |
| `assistant` | 模型回复（含 tool calls） |
| `attachment` | 附件 |
| `permission-mode` | 权限模式切换 |
| `file-history-snapshot` | 文件历史快照 |
| `custom-title` / `tag` / `mode` / `worktree-state` / `pr-link` / `summary` 等 | 元数据事件 |

每条 user/assistant event 都带 `uuid`、`parentUuid`、`isSidechain`、`timestamp`、`sessionId`、`cwd`、`gitBranch`、`version`、`entrypoint`、`userType`、`permissionMode`。

**关键**：`cwd` 字段直接出现在事件里，xskill 不需要反查目录名 → cwd 的映射；只需在适配器里取 `event.get("cwd")` 即可。

### 3.3 写入特性

异步批量 append（`drainWriteQueue`），mode `0600`。文件单调追加，inotify 友好，xskill 用 `watchdog` recursive watch `~/.claude/projects/` 即可。

## 4. xskill 当前接入

### 4.1 安装侧

`src/xskill/ecosystems.py:102` `install_to_claude_code(skill_path, target_root, side)`：
- 把 `<skill_path>` 整目录 **symlink** 到 `<target_root>/.claude/skills/<name>/`。
- 用 symlink 而非 copy 的两个好处（见函数 docstring）：
  1. xskill 改 SKILL.md 立刻被 CC 看到，不用重启。
  2. 用户直接改 `~/.claude/skills/<name>/SKILL.md` 实际改的是 xskill 源文件，不会被下次 install 覆盖。
- `side='main'` 链到稳定源；`side='staging'` 链到 `.canary/<name>/`，用于 A/B 灰度。
- Windows 上 symlink 要 Developer Mode 或管理员权限（README "Platforms" 章节有说明）。

### 4.2 摄取侧

- `src/xskill/ecosystems.py:329` `ingest_claude_code_sessions(home_root, target_traj_dir, seen_sessions)`：扫 `~/.claude/projects/*/*.jsonl`，每个未见过的 `sessionId` 提交一次桥接任务。
- `src/xskill/adapters.py` `_adapt_claude_code_jsonl(...)`：JSONL → xskill 通用 `traj_*.md`，提取 `session_id`、`cwd`、`git_branch`、`timeline` 等元信息。

### 4.3 自动检测

`src/xskill/ecosystems.py:38` `KNOWN_ECOSYSTEMS` 注册了 claude_code 的 spec：

```python
{
    "id": "claude_code",
    "source_subpath": ".claude/projects",
    "bridge": "claude_code_jsonl",
}
```

`detect_known_ecosystems()` 在 daemon 启动时检查 `<home>/<source_subpath>` 是否存在并自动注册到 registry，无需用户运行 `xskill registry add`。

## 5. 平台差异速查

| OS | 状态 | 差异点 |
|---|---|---|
| Linux | ✅ 已验证 | 主开发 / CI 环境 |
| macOS | ⚠️ 应该可工作 | POSIX 同 Linux，未进 CI |
| Windows | ⚠️ 部分支持 | symlink 需 Developer Mode；cwd 编码规则未实测；trajectory ingest 应当可工作（JSONL 跨平台） |

## 6. 已知坑

1. **CC analysis 目录**：`~/.claude/projects/-home-admin-claude-code-claude-code-analysis/` 这种"项目内套项目"的路径会出现多个独立的 encoded slug，watcher 不会混淆，但人在 grep 时容易看花。
2. **session 复用**：CC 在同一个 sessionId 下可能多次写入（resume 场景）；xskill 用 `(session_id, file_mtime)` 做 dedup，重启不会重复消费 — 见 `seen_sessions` 状态。
3. **subagent**：CC 子代理 trajectory 路径多一层 `<sid>/subagents/agent-<id>.jsonl`，xskill 当前扫描通配 `*/*.jsonl` 是否覆盖了这一层？需要确认 watcher 的 glob 深度（如果不够，xskill 会漏掉 subagent 数据）。
