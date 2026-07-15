---
name: xskill
description: >-
  How to run, connect, upgrade, debug, and search with the xskill CLI — the
  team skill-distribution and trajectory-collection daemon. Use when a user
  asks how to install or join an xskill team server, upgrade xskill, fix a
  stuck/black-window/copy-mode install, run xskill in the background on
  Windows/macOS/Linux/WSL, search or share team skills, or read the dashboard.
---

# xskill

xskill is a thin client + background daemon that (1) mounts your team's shared
Skills into every AI-agent tool you use (Claude Code, Codex, OpenCode, Cursor,
Trae) and (2) quietly collects your agent trajectories and syncs them to a team
server. You join a server once, then it keeps skills in sync and auto-updates
itself.

All state lives under `~/.xskill/`:

| File | What |
|---|---|
| `~/.xskill/team_client.json` | connection **identity** (server_url, client_id, join_token) — survives restarts |
| `~/.xskill/connect_daemon.json` | current background daemon (pid / host task) — used by `status`/`stop` |
| `~/.xskill/logs/xskill.*.log` | split logs (one file per component) |
| `~/.xskill/skill/` | the local skill repo everything is mounted from |

## Getting started — `xskill init`

`xskill init` is the interactive onboarding command. It:

1. installs this xskill guide skill into every detected agent tool,
2. asks which server to join, the join token, and your employee id,
3. detects any already-running daemon and asks whether to keep or replace it,
4. starts the background daemon.

```bash
xskill init                 # interactive: prompts for server / token / name
```

Headless / scripted (no prompts):

```bash
xskill init <host:port> --token <join-token> --name <employee-id> --yes
```

`init` is a convenience wrapper around `connect`. If you already know the flow,
`xskill connect <host:port> --token <t> --name <id>` does the connect half
directly. The **join token** is printed by the server operator when they run
`xskill serve --server`.

## Upgrading

```bash
pip install -U xskill        # manual upgrade to the latest PyPI release
xskill update                # check PyPI now, upgrade + restart if newer exists
```

The daemon **auto-checks PyPI every hour** and upgrades itself when a newer
version is out; disable with `xskill connect --no-auto-update`. Behind a
corporate proxy, add `--use-proxy` (default is direct connection, bypassing the
SWG proxy). Background hosting differs per platform — Windows uses a Scheduled
Task, Linux/WSL uses a systemd **user** service. See
[references/platforms.md](references/platforms.md).

## Debugging

The single most useful command when something is wrong:

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

## Searching & sharing team skills

```bash
xskill search <query...>       # search the team skillhub, pull hits into local slots
xskill search auth retry -k 3  # top-3 results (max 10)
xskill upload ./my-skill       # package a SKILL.md folder and share to the team
xskill dashboard               # print a passwordless link into the server dashboard
xskill stats                   # token usage & estimated cost (--watch for live)
```

## Where skills land per tool

| Tool | Install dir | Mount |
|---|---|---|
| Claude Code | `~/.claude/skills/<name>/` | symlink → junction (Win) → copy |
| Codex | `~/.agents/skills/<name>/` (shared) | same |
| OpenCode | `~/.agents/skills/<name>/` (shared) | same |
| Cursor | `~/.cursor/skills/<name>/` | same |
| Trae | IDE workspace / `~/.trae-cn` (auto-detected) | same |

**Live mount vs snapshot:** symlink/junction installs are live — server updates
show up instantly and your edits round-trip back. A **copy**-mode install is a
snapshot: it logs a warning, updates do NOT propagate live, and local edits do
NOT round-trip. Copy mode only happens when neither symlink nor junction can be
made (cross-drive, or non-NTFS). Fix: on Windows enable Developer Mode so
symlinks work — see [references/platforms.md](references/platforms.md).
