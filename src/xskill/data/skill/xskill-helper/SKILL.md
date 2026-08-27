---
name: xskill-helper
description: >-
  How to run, connect, generate, upgrade, debug, and search with the xskill
  CLI. Use when a user asks how to join their team server, run
  `xskill generate`, search trajectories with `xskill search traj`,
  read lines with `xskill read traj` / `xskill read atom`,
  upgrade, debug a stuck install, or use xskill from Claude Code, Codex,
  Cursor, or another supported agent via `/xskill-helper`.
---

# xskill-helper

xskill is a thin client + background daemon that (1) mounts your team's shared
Skills into every AI-agent tool you use (Claude Code, Codex, OpenCode, Cursor,
Trae, DeepSeek Harness) and (2) quietly collects your agent trajectories and
syncs them to a team server. You join a server once, then it keeps skills in
sync and auto-updates itself.

`xskill connect` installs this guide into every detected agent skill directory.
Invoke it as `/xskill-helper` from Claude Code, Codex, Cursor, and the other
supported tools.

All state lives under `~/.xskill/`:

| File | What |
|---|---|
| `~/.xskill/team_client.json` | connection identity (server_url, client_id, join_token) — survives restarts |
| `~/.xskill/connect_daemon.json` | current background daemon (pid / host task) — used by `status`/`stop` |
| `~/.xskill/logs/xskill.*.log` | split logs (one file per component) |
| `~/.xskill/skill/` | the local skill repo everything is mounted from |

## Getting started

Join the team's own server. Never send the user to a public hub or any
address you were not given:

```bash
xskill connect <host:port> --token <token> --name <user-id>
```

If they do not have host, token, or name, show this command as the example
and ask their server operator. The operator prints the token with
`xskill serve --server`. Do not invent an address or token, and do not
paste one from the internet.

After a successful handshake, connect installs this guide and starts the
background daemon. Later reconnects can omit the address and token; they
reuse `~/.xskill/team_client.json`.

In the agent, invoke this guide as `/xskill-helper`.

## Generate or rewrite a Skill

`generate` does not invent a Skill from scratch. It reads trajectories the team
server already allows this client to see, and the instruction only describes
what to create or change. The job may wait for a free SkillEdit seat; the CLI
streams queue and run logs. When it finishes, the Skill is committed to main
and pinned on the initiator's recommendation list.

```bash
xskill generate "创建一个排查 Python 内存泄漏的 Skill，包含常用诊断命令"
xskill generate "改写现有的 python-memory-debug Skill，补充 Windows 排查步骤"
xskill generate --name alice,bob "根据这些用户的成功案例生成数据库迁移 Skill"
```

`--name` is a comma-separated list of employee ids the agent should read first.
Omit it and the agent may search every trajectory the server authorizes. If the
CLI says the server is too old, ask the operator to upgrade the team server.

## Import an existing Skill

`import` takes a local Skill folder (or a parent that contains several) into
the team's own repo. This is not `upload`: import becomes a first-party Skill
on main, while upload only lands in the user's SkillHub share.

```bash
xskill import ./my-skill
xskill import ./skills-parent --json
```

## Searching trajectories and atoms

These are two commands. Do not mix them.

`xskill search traj` searches the session index, not the raw `traj_*.md`
files. Upload and the watcher extract `## Initial Query` / `## User`
once and write the sidecar index. The query path only reads that index
and ranks with BM25. It does not wait for the split agent and does not
open trajectory files. Cards show `traj_id`, user, and the first user
query.

`xskill search atom` searches atoms that the split agent already wrote
(intent, summary, line range). Ranking is vector plus BM25. Sessions
that have not been split do not appear. Cards add Atom id, offsets, and
the atom summary.

```bash
xskill search traj 内存泄漏
xskill search traj "alembic 半迁移" -k 8
xskill search traj --name alice,bob 发票核对 --json
xskill search atom 内存泄漏
xskill search atom --name alice,bob 发票核对
```

`--name` only applies in team mode. Neither command downloads files or
returns raw trajectory text. Paste a `traj_id` into `xskill generate`
when you want the instruction to name the evidence.

## Reading a trajectory or atom

These are not search. `read traj` opens one uploaded `traj_*.md` by
line number. `read atom` opens the same markdown, but only inside that
atom's line range. Both take `--offset-start` and `--offset-end`
(1-based, half-open). Each reply prints the current window and the
total window. One call returns at most 200 lines; if truncated, the
next page starts at the current end.

```bash
xskill read traj traj_cc_alice_memleak
xskill read traj traj_cc_alice_memleak --offset-start 12 --offset-end 88
xskill read atom atom_t_0001
xskill read atom atom_t_0001 --offset-start 40 --json
```

`--name` only applies in team mode. Do not print server paths. If the
CLI says the server is too old, ask the operator to upgrade.

## Searching & sharing team skills

```bash
xskill search <query...>       # search team skills; returns metadata only
xskill search traj <query...>  # search session index (first user query, no split agent)
xskill search atom <query...>  # search split atoms (intent / summary / offsets)
xskill read traj <traj_id>     # read trajectory lines; prints current and total range
xskill read atom <atom_id>     # read atom lines; prints current and total range
xskill search <query...> --download  # legacy 10-slot LRU download + auto-install
xskill download <skill-id>     # persist one result; interactively select harnesses
xskill download <skill-id> --agent claude-code --agent codex -y  # for agents/scripts
xskill search auth retry -k 3  # top-3 results (max 10)
xskill upload ./my-skill       # package a SKILL.md folder and share to the team
xskill dashboard               # print a passwordless link into the server dashboard
xskill stats                   # token usage & estimated cost
```

Already connected clients search SkillHub. Standalone machines search the local
library (semantic index, falling back to BM25). `search --download` is the old
rolling-slot path; prefer `download <skill-id>` to keep a Skill permanently.

## Upgrading

```bash
pip install -U xskill        # manual upgrade to the latest PyPI release
xskill update                # check PyPI now, upgrade + restart if newer exists
```

The daemon auto-checks PyPI every hour and upgrades itself when a newer
version is out; disable with `xskill connect --no-auto-update`. Behind a
corporate proxy, add `--use-proxy` (default is direct connection, bypassing the
SWG proxy). Background hosting differs per platform — Windows uses a Scheduled
Task, Linux/WSL uses a systemd user service. See
[references/platforms.md](references/platforms.md).

## Debugging

```bash
xskill connect --foreground          # run the daemon loop in the foreground, live logs
xskill connect --foreground --debug  # + verbose logging
```

| Symptom | Command | Expected |
|---|---|---|
| Is it running / connected? | `xskill status` | prints background task + pid, or "not running" |
| Skills not updating | `xskill connect --foreground` | watch the reconcile loop; look for `copy-mode` / `fell back to copy` warnings |
| Stop it | `xskill stop` | tears down the Scheduled Task / systemd unit |
| Restart clean | `xskill stop` then `xskill connect` | re-handshakes and re-daemonizes |

Logs are at `~/.xskill/logs/xskill.*.log`. See
[references/troubleshooting.md](references/troubleshooting.md) for the
black-window, copy-mode, and can't-connect issues in detail.

## Where skills land per tool

| Tool | Install dir | Mount |
|---|---|---|
| Claude Code | `~/.claude/skills/<name>/` | symlink → junction (Win) → copy |
| Codex | `~/.agents/skills/<name>/` (shared) | same |
| OpenCode | `~/.agents/skills/<name>/` (shared) | same |
| Cursor | `~/.cursor/skills/<name>/` | same |
| Trae | IDE workspace / `~/.trae-cn` (auto-detected) | same |
| DeepSeek Harness | `~/.dsh/skills/<name>/` | same |

symlink/junction installs are live — server updates show up instantly and your
edits round-trip back. A copy-mode install is a snapshot: it logs a warning,
updates do not propagate live, and local edits do not round-trip. Copy mode
only happens when neither symlink nor junction can be made (cross-drive, or
non-NTFS). On Windows, enable Developer Mode so symlinks work — see
[references/platforms.md](references/platforms.md).
