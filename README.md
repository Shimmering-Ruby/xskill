<div align="center">

# xskill

**Stop re-teaching your AI agents. xskill turns work they have already done into Skills they reuse.**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

**English** · [简体中文](./README.zh-CN.md)

</div>

---

<p align="center">
  <img src="docs/assets/demo.gif" width="764"
       alt="A coding agent listing the Skills xskill distilled from past sessions">
</p>

## News

- **2026-05** — `v0.5.0` released: Team mode (client-server) lands, line-anchored atom splitting, `detect-secrets` redaction, Python 3.9 supported, runtime no longer needs the system `git` binary. See the [release notes](https://github.com/SkillNerds/xskill/releases/tag/v0.5.0).
- **2026-05** — xskill is open-source under the MIT license, and on PyPI: `pip install xskill`.
- **2026-05** — Claude Code, Codex, and OpenCode are verified end to end; OpenClaw and Cursor are implemented but not well tested.

## What changes for you

Without xskill, a coding agent re-derives the same solution every time it meets
a familiar problem, and you either re-explain it or hand-maintain a prompt
library that drifts out of date.

With xskill running, that maintenance disappears:

- Patterns that worked become Skill files your agent loads automatically.
- The library updates itself as you keep working — no review queue, no curation.
- When you do edit a Skill by hand, xskill keeps the edit and treats it as
  ground truth.
- A new Skill version replaces the old one only if it measurably serves users
  better.

You keep working as before. The Skill library is a byproduct.

## Get started

```bash
pip install xskill          # Python 3.9+
xskill serve                # writes ~/.xskill/config.yaml, then exits
```

Fill in two model endpoints in `~/.xskill/config.yaml`:

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://api.deepseek.com
  model:    deepseek-embedding
  api_key:  YOUR_KEY
  dim:      0
```

Any OpenAI-compatible endpoint works. Run `xskill serve` again — it auto-detects
and watches every supported agent on your machine (Claude Code, Codex, OpenClaw,
Cursor). To also index an archive of older trajectories, register that directory:

```bash
xskill registry add /path/to/trajectories
```

## For teams: one shared Skill library

Team mode is the reason to deploy xskill across an organization. One machine is
the server; everyone else joins as a thin client.

```bash
xskill serve --server                        # prints a join token
xskill connect <host:port> --token <token>
```

- **Shared library.** Every client benefits from Skills distilled across the
  whole team's work, not just their own.
- **Private trajectories.** A client redacts before upload; only the server runs
  the pipeline and holds the full history.
- **Per-user A/B.** Canary buckets by `client_id`, so a Skill change is measured
  per person before it spreads.
- **Safe local edits.** A client's hand-edits go to an isolated
  `user-staging/<client_id>` branch, never straight to shared `main`.

## How it works

A few narrow LLM agents do the work. One splits a trajectory into single-intent
Atoms; one routes each Atom to a Skill; one rewrites the `SKILL.md` once a Skill
has enough material; one A/B-tests new versions on live traffic and keeps the
winner. Every Skill is its own git repository, so changes are versioned and
reversible. Details: [`docs/agent.md`](docs/agent.md).

## Works with your agents

| Agent | Status | Trajectory ingest | Skill install |
| ----- | ------ | ----------------- | ------------- |
| **Claude Code** | ✅ verified | auto-detects `~/.claude/projects/` | symlink → `~/.claude/skills/<name>/` |
| **Codex CLI** | ✅ verified | auto-detects `~/.codex/sessions/` | symlink → `~/.agents/skills/<name>/` |
| **OpenCode** | ✅ verified | SQLite `~/.local/share/opencode/opencode.db` | symlink → `~/.agents/skills/<name>/` |
| **OpenClaw** | 🟡 implemented, not well tested | auto-detects `~/.openclaw/agents/` | copy → `~/.agents/skills/<name>/` |
| **Cursor** | 🟡 implemented, not well tested | auto-detects `~/.cursor/projects/*/agent-transcripts/` | symlink → `~/.cursor/skills/<name>/` |
| **Any other agent** | manual | SDK: `xskill.adapters.submit_trajectory` | copy or symlink the `SKILL.md` directory |

Output follows the Anthropic `SKILL.md` schema, so the library is portable.
Trae is on the [roadmap](#roadmap).

## Concepts

| Term | Meaning |
| ---- | ------- |
| **Trajectory** | One agent run — the transcript of a session. Stored as `traj_*.md`. |
| **Atom** | The smallest single-intent slice of a trajectory. Routing happens at this level. |
| **Skill** | A `SKILL.md` plus optional scripts, in its own versioned git directory. |
| **Canary** | A live-traffic A/B test of a current Skill against a new candidate. |
| **UX score** | How well a Skill served the user on a given Atom, scored 1–10 from the interaction itself. The canary keeps whichever version scores higher. |

## Why not just keep a prompt folder

A hand-kept prompt library has no feedback loop — nothing tells you which entries
still help and which have rotted. xskill closes that loop: every Skill version
is A/B-tested on real traffic, scored on the user experience it produced, and
kept or dropped on that result. The comparison against 10 prior
trajectory-to-skill systems is in
[`docs/research/related-work-survey.md`](docs/research/related-work-survey.md).

## Roadmap

- More agent adapters — Trae, Goose, OpenHands, Aider
- Native MCP server interface (Skills exposed as tools)
- Web UI for browsing the library and viewing canary stats
- Usage-driven auto-prune
- Skill marketplace — import / export portable bundles
- Multi-tenant libraries (per-team `skill_dir`)

## License

MIT © [370025263](https://github.com/370025263). See [LICENSE](LICENSE).
