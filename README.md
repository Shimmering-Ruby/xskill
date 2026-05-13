<div align="center">

# xskill

**Turn the work your AI agents already do into a reusable Skill library — automatically.**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

**English** · [简体中文](./README.zh-CN.md)

</div>

---

> xskill watches the conversations your coding agents have already had, picks out the moves that actually worked, and writes them into Skill files. Next time a similar problem shows up, the agent hits the Skill instead of starting from scratch.

## Why xskill

If you use Claude Code (or Codex, Cursor, Trae…) day-to-day, you have probably noticed the same loop:

- You teach the agent how to fix a class of bug today.
- Tomorrow it forgets, googles around, and reinvents the same fix.
- You either retype the playbook every morning, or maintain a prompt library by hand and watch it rot.

xskill closes that loop. Point it at the directory where your agent stores its session history; it quietly distills the useful patterns into a library of Skill files that the agent loads on its next run. You keep your day job; the Skill library grows on its own.

It is opinionated about two things:

- **You do not curate.** xskill decides whether a new piece of experience fits an existing Skill or deserves a new one.
- **Only Skills that actually help users get shipped.** A new candidate runs side-by-side with the current Skill and the loser is dropped.

## Install

```bash
pip install xskill
```

The PyPI package is [`xskill`](https://pypi.org/project/xskill/); the CLI entry point is also `xskill`. Python 3.11+.

Then drop a config file at `~/.xskill/config.yaml`:

```bash
mkdir -p ~/.xskill
curl -fsSL https://raw.githubusercontent.com/SkillNerds/xskill/main/examples/config.yaml.example \
  -o ~/.xskill/config.yaml
# edit llm.api_key and embedding.api_key
```

Minimal `config.yaml`:

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model:    doubao-embedding-vision-251215
  api_key:  YOUR_KEY
  dim:      0
```

Any OpenAI-compatible endpoint works (DeepSeek, Qwen / Ark, OpenAI, etc.). Missing fields raise — there are no environment-variable fallbacks. Full template: [`examples/config.yaml.example`](examples/config.yaml.example).

Start the daemon:

```bash
xskill serve            # FastAPI + watcher + Web UI on :8000
```

If you use Claude Code, that is it — the daemon detects `~/.claude/projects/` on startup and starts watching your sessions. Otherwise, register the directory your agent writes trajectories to:

```bash
xskill registry add /path/to/your/agent/trajectories
```

## CLI

Five commands. Filtering and formatting belong to `grep` / `awk`, not flags.

```bash
xskill serve [--host 0.0.0.0] [--port 8000]
xskill registry add    <abs-path> [--label NAME]
xskill registry remove <abs-path>
xskill registry list
xskill search traj  <query> [--top-k 5]
xskill search skill <query> [--top-k 5]
```

## Agents inside xskill

A handful of LLM agents do the work, each with a single, narrow job:

| Agent | What it does (one line) |
| ----- | ----------------------- |
| **TaskAgent** | Reads a raw trajectory and splits it into small per-intent units (one Atom = one thing the user actually asked for). |
| **TaskClusterAgent** | For each new Atom, decides: hit an existing Skill, merge into an existing Skill, or open a new Skill. Prefers reuse over creation. |
| **SkillEditAgent** | When a Skill has accumulated enough relevant Atoms, writes / rewrites its `SKILL.md` (and any supporting scripts or references) and commits the update. |
| **UserEditAbsorbAgent** | Watches for hand-edits you make to the installed Skill files and folds those changes back into the Skill library as ground truth. |
| **AtomCanary** | Runs a current Skill and a new candidate side by side on real traffic and decides — based on per-Atom user-experience scores — which one ships. |

## Cross-agent support

The trajectory in / Skill out interfaces are pluggable. Here is the honest current state:

| Coding agent | Trajectory ingest (input) | Skill install (output) |
| ------------ | ------------------------- | ---------------------- |
| **Claude Code** | Native — auto-detects `~/.claude/projects/` on `xskill serve`, bridges each session JSONL into a trajectory and (when a Skill is in evaluation) injects the canary marker. | Native — Skill is symlinked into `~/.claude/skills/<name>/`, so updates land in Claude Code on its next launch with no copy step. |
| **Codex CLI** | Not yet — open an issue / PR. | Not yet. |
| **Cursor** | Not yet. | Not yet. |
| **Trae** | Not yet. | Not yet. |
| **OpenCode** | Not yet. | Not yet. |
| **OpenClaw** | Not yet. | Not yet. |
| **Any other agent** | Manual — submit a trajectory in `markdown`, `json`, or `raw` format via the SDK (`xskill.adapters.submit_trajectory`). | Manual — every Skill is a directory with an Anthropic-style `SKILL.md` + YAML frontmatter; copy or symlink it into whatever discovery path your agent uses. |

The output format is the same `SKILL.md` schema Anthropic uses, so any agent that already reads Anthropic Skills can read xskill's library verbatim. We are adding native adapters one agent at a time — Codex, Cursor, OpenCode and friends are on the [roadmap](#roadmap); PRs welcome.

## Platforms

xskill is pure Python (3.11+) and the daemon, watcher and SDK are OS-agnostic in principle. Coverage we can honestly claim today:

| Platform | Status | Notes |
| -------- | :----: | ----- |
| **Linux** (x86_64) | tested ✅ | Development and CI environment. |
| **macOS** | should work | Same POSIX surface — symlinks, `~/.claude/` path and `git` subprocess all behave the same as Linux. Not part of CI yet — report issues. |
| **Windows 10 / 11** | partial ⚠️ | Trajectory ingest and Skill search work, but installing a Skill creates a directory symlink, which requires **Developer Mode** or running as Administrator on Windows. Without that, the symlink step fails. Not part of CI — community testing welcome. |

If you are on Windows and want to avoid the symlink requirement, set `skill_dir` directly to your agent's discovery folder in `~/.xskill/config.yaml` and skip the auto-install step.

## Concepts

| Term | What it means |
| ---- | ------------- |
| **Trajectory** | One agent run — typically the transcript of a single session. xskill stores it as `traj_*.md`. |
| **Atom** | The smallest "one user intent" slice of a trajectory. One trajectory can produce one or several Atoms. Routing decisions happen at this level. |
| **Skill** | A reusable, prompt-shaped artifact your agent can load: an `SKILL.md` file plus optional scripts and references. Each Skill lives in its own versioned directory under `~/.xskill/skill/`. |
| **Canary** | The mechanism that compares an existing Skill with a new candidate version on real traffic and keeps whichever scores better on user experience. |
| **Registry** | The list of directories xskill is watching. Add a path and the daemon polls it forever. |
| **UX score** | An LLM-as-judge score that grades how well a Skill actually served the user on a given Atom. Used by the canary to pick a winner. |

## How xskill compares

We surveyed 10 academic and open-source trajectory-to-skill systems (Hermes, OpenSpace, EvoSkill, AutoSkill, AgentEvolver, MemSkill, EvoAgentX, SE-Agent, SkillRL, GEPA) before building xskill. The full matrix lives at [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md) with `path:line` evidence per cell.

What xskill takes from prior work:

- **`SKILL.md` as the cross-agent unit** — OpenSpace, EvoSkill and AutoSkill all converged here; xskill follows the same Anthropic frontmatter schema for portability.
- **LLM-as-judge UX scoring** — inspired by AutoSkill's per-turn `relevant / used` signal.
- **Per-Skill versioning** — each Skill is its own git repository, so history, diffs and rollbacks are first-class.

What xskill does that none of the surveyed projects do:

- **Real A/B between Skill versions** — chat traffic is split, two-sided UX scores decide which version ships, no human in the loop.
- **Symmetric ingestion** — both per-session streaming (drop a new transcript, the watcher picks it up) and batch backfill (`xskill registry add /archive` reindexes an entire history) are first-class.

## Roadmap

- [ ] **More coding-agent adapters** — Codex CLI, Cursor, Trae, OpenCode, OpenClaw, Goose, OpenHands, Aider on both ends (trajectory ingest + Skill install)
- [ ] Native MCP server interface (Skills exposed as tools)
- [ ] Web UI for browsing the Skill library, viewing canary stats, manual approve / discard
- [ ] Usage-driven auto-prune (delete Skills that are retrieved often but never actually used)
- [ ] Skill marketplace: import / export portable Skill bundles
- [ ] Multi-tenant Skill libraries (per-team `skill_dir`)

Have an idea? Open an [issue](https://github.com/SkillNerds/xskill/issues).

## Development

```bash
git clone https://github.com/SkillNerds/xskill
cd xskill
pip install -e .[dev]
pytest -q
```

Internal design notes live under [`docs/`](docs/) (English and 中文 mixed).

## Contributing

PRs welcome — please:
1. Open an issue describing the problem first.
2. Add or extend a test (no test, no merge).
3. Keep public-API additions in `xskill/__init__.py` minimal — we guard the surface area.

## License

MIT (c) [370025263](https://github.com/370025263). See [LICENSE](LICENSE).

---

<div align="center">

If xskill saves your agents from solving the same problem twice, a star on [GitHub](https://github.com/SkillNerds/xskill) helps others find it.

</div>
