<div align="center">

# xskill

**Distill reusable Skills from your AI Agent's execution trajectories — automatically.**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-SkillNerds%2Fxskill-181717?logo=github)](https://github.com/SkillNerds/xskill)

**English** · [简体中文](./README.zh-CN.md)

</div>

---

> Your agents already know how to do things. They just forget every time.
> **xskill** watches what they do, distills what works into a Skill library, and ships only the patterns that pass A/B grading.

> ⚠️ **v0.4.0a1 — AtomTask refactor (alpha).** The pipeline now operates at the *atom* level (one user-intent unit per agent run) and each Skill is a three-branch state machine (`baby` → `main` → `staging`). API-level surface and `SKILL.md` schema are unchanged; runtime state (DB, on-disk skill repos) is not backward-compatible — wipe `~/.xskill/` if upgrading from `0.3.x`.

## Why xskill

LLM agents repeat the same problem-solving over and over because their experience evaporates the moment a session ends. Hand-curated prompt libraries help, but they age fast and don't capture the *why*.

**xskill** treats every agent run (a `traj_*.md` file) as raw material — but the unit of distillation is *not* the whole trajectory. A trajectory is split into **AtomTasks** (one user-intent unit each), each atom is clustered against the existing skill catalog, and a Skill graduates through three git branches:

```
traj_*.md  ──split──►  AtomTask*  ──cluster──►  candidate buffer  ──edit──►  Skill
                                       │              (per skill)               │
                                       └──reuse / integrate / new               │
                                                                                ▼
                          baby branch  ──promoted──►  main  ──canary──►  staging  ──A/B──►  merge | discard
                          (stub, hidden)              (visible to CC)   (≥5 ux samples)
```

The cluster agent prefers **reuse > integrate > create** so similar atoms collapse into one skill instead of spawning near-duplicates. The edit agent only fires when a skill's candidate buffer has accumulated enough weight (sum of per-atom scores). Canary runs as an independent watcher loop — not bound to the cluster chain — so a single Skill's grading never blocks others.

## Cross-agent compatibility

xskill sits between **whatever produced the trajectory** and **whatever will eventually consume the skill**. Both ends are pluggable.

| Direction | Today | Roadmap |
| --------- | ----- | ------- |
| **Trajectory in** (what your agent writes) | Claude Code (`traj_*.md` with `<!-- xskill: -->` headers) | Codex CLI, OpenCode, Goose, OpenHands, Cursor, Aider — adapter-per-agent |
| **Skill out** (who reads the produced library) | Anthropic-style `SKILL.md` with YAML frontmatter — drop-in for Claude Code's `.claude/skills/<name>/` | Codex (symlink), OpenCode (path normalization), Goose, generic MCP server exposing each skill as a tool |

The output format is the *de facto* `agentskills.io` SKILL.md schema, so anything that already groks Anthropic Skills can read xskill output verbatim. Non-conforming agents get a thin per-agent adapter that translates the same skill into whatever shape they need (system prompt block, tool description, structured JSON, etc.).

## Highlights

- **Zero-touch ingestion** — drop `traj_*.md` into a watched dir, the rest is automatic.
- **Skills as code** — every Skill is a versioned directory with `SKILL.md`, supporting trajs, candidates, and a per-skill git history.
- **Built-in canary** — staging vs. main rollout, sample-size gating, automatic merge/discard.
- **Tiny CLI** — five commands. Filtering and formatting belong to `grep`/`awk`, not flags.
- **OpenAI-compatible** — works with DeepSeek, Qwen, Ark, OpenAI, anything that speaks `/v1/chat/completions` + embeddings.
- **One source of truth** — all state lives under `~/.xskill/`. No env vars, no fallbacks, no dotfiles to chase.

## Quick Start

```bash
pip install xskill

mkdir -p ~/.xskill
curl -fsSL https://raw.githubusercontent.com/SkillNerds/xskill/main/examples/config.yaml.example \
  -o ~/.xskill/config.yaml
# edit llm.api_key + embedding.api_key

xskill registry add /path/to/your/agent/trajectories
xskill serve   # daemon: FastAPI + watcher + Web UI on :8000
```

That's it. Drop a new `traj_*.md` into the registered directory and watch the daemon pick it up, embed it, and update the Skill library.

## CLI

Five commands. No more.

```bash
xskill serve [--host 0.0.0.0] [--port 8000]
xskill registry add    <abs-path> [--label NAME]
xskill registry remove <abs-path>
xskill registry list
xskill search traj  <query> [--top-k 5]
xskill search skill <query> [--top-k 5]
```

`search` returns tab-separated columns — pipe it:

```bash
$ xskill search skill "form validation" | sort -k4 -nr | head -3
0.350  fix-early-return-in-validation-functions   3   7.8(15)  -
0.343  fix-cli-language-validation                2   8.1(12)  staging
0.309  fix-api-method-parameter-validation        0   -        -
# columns: similarity  name  use_count  ux_avg(N)  canary_status
```

## Python SDK

The public surface is **4 classes + 6 dataclasses**.

```python
from xskill import XSkill, Skill, Trajectory, Evaluator

x = XSkill()  # loads ~/.xskill/config.yaml

# Search across every registered directory
for hit in x.search_skills("django form", top_k=5):
    print(f"{hit.similarity:.3f}  {hit.skill.name}  uses={hit.skill.use_count}")

# Browse the repo
for skill in x.skill_repo:
    print(skill.name,
          skill.canary_status(),
          skill.ux_avg(side="main", days=30))

# Register a new watched dir
x.registry.add("/abs/path/to/trajs", label="prod-eng")

# Run the merge gate yourself (CI / unit tests)
ev = Evaluator(x.llm, x.config)
score = ev.evaluate(x.skill_repo["fix-foo"])
if Evaluator.should_merge(score):
    print("ready to merge")

# Or just start the daemon and let it work
x.serve(host="0.0.0.0", port=8000)
```

Advanced (rare): `from xskill import Registry, SkillRepo` for direct subsystem access.

## How It Works

The watcher is a single poll loop (default 30s) that drives five independent stages — each stage scans the world *every round*, so a failure in one path never starves the others.

```
                ┌─────────────────────────────── watcher (poll: 30s) ───────────────────────────────┐
                │                                                                                   │
  traj_*.md ──► │  1. discover  →  2. split (TaskAgent)  →  3. embed  →  4. cluster                │
                │                  (atom by user intent)    (vector)     (TaskClusterAgent)         │
                │                                                              │                    │
                │                                                              ▼                    │
                │                                                  ~/.xskill/skill/<name>/          │
                │                                                  ├── .candidates.yml  ← buffer    │
                │                                                  ├── SKILL.md         ← prompt    │
                │                                                  ├── scripts/, references/        │
                │                                                  └── .git              baby/main/ │
                │                                                                        staging    │
                │                                                                                   │
                │  5. SkillEditAgent  ◄── candidate weight ≥ threshold (independent scan)           │
                │     ├─ writes SKILL.md + arbitrary support files                                  │
                │     ├─ on baby:  promotes baby → main      (visible to Claude Code)               │
                │     └─ on main:  forks staging from main   (enters canary)                        │
                │                                                                                   │
                │  6. AtomCanary       ◄── independent polling, never blocked by cluster failure    │
                │     ├─ traffic split by `canary.probability` (main vs staging)                    │
                │     └─ ≥ `min_samples` per side → compare ux_avg → merge | discard                │
                │                                                                                   │
                │  7. UserEditAbsorb   ◄── detects out-of-band edits in ~/.claude/skills/<name>/    │
                │     └─ stable ≥3 min → commit user changes back to main as ground truth          │
                └───────────────────────────────────────────────────────────────────────────────────┘
```

**Why the three branches.** A skill starts on `baby` (hidden from Claude Code, just a stub). It only graduates to `main` once an edit succeeds — preventing empty/half-baked skills from surfacing. Once on `main`, a new candidate forks `staging` for canary; only the winning side is kept.

**Candidates as a pure buffer.** `.candidates.yml` is gitignored. Each entry is `{atom_id, weightscore, note}`. The cluster agent can overwrite an entry if it changes its mind. SkillEditAgent fires when the sum of weightscores crosses a threshold — *not* when count crosses 10, *not* when N source-trajs accumulate.

**Symlink install.** When a skill is promoted to `main`, xskill creates a symlink at `~/.claude/skills/<name>/` pointing into `~/.xskill/skill/<name>/`. Changes inside the skill repo are immediately visible to Claude Code without a copy step; user hand-edits land inside the same repo and get absorbed back to `main` by `UserEditAbsorb`.

## Configuration

Everything lives at `~/.xskill/config.yaml`. Missing or malformed → hard error, no silent fallbacks.

```yaml
skill_dir: ~/.xskill/skill

llm:
  base_url: https://api.deepseek.com
  model:    deepseek-v4-flash
  api_key:  YOUR_KEY

embedding:
  base_url: https://api.example.com/v1
  model:    your-embedding-model
  api_key:  YOUR_KEY
  dim:      0   # 0 = auto-detect

canary:
  enabled:     true
  probability: 0.2   # share of traffic routed to staging
  min_samples: 5     # ≥5 ux samples on each side before promote/reject

watcher:
  poll_interval: 30   # seconds
```

Full template: [`examples/config.yaml.example`](examples/config.yaml.example).

```
~/.xskill/
├── config.yaml         # the only config file (no env-var fallback)
├── registry.db         # watched dirs + per-trajectory state (sqlite)
├── chat_sessions.db    # chat history
├── logs/               # one log file per trajectory
├── chat_archive/       # auto-registered chat trajectories
└── skill/              # the global skill repo (one git subrepo per skill)
```

## Concepts

| Term         | What it is |
| ------------ | ---------- |
| **Trajectory** | A single agent run, written as `traj_*.md`. Embeds optional `<!-- xskill:skill=... side=... sha=... -->` metadata so the watcher can score it. |
| **AtomTask**   | The minimal user-intent unit, extracted from a trajectory by `TaskAgent`. One traj → 1..N atoms. Clustering happens at the atom level, not traj level. |
| **Skill**      | A reusable, prompt-shaped artifact built from clustered atoms. Lives at `~/.xskill/skill/<name>/`, version-controlled. Each skill is its own git repo. |
| **baby / main / staging** | The three branches that form a skill's state machine. `baby` = hidden stub (just created, not surfaced to CC); `main` = the live skill; `staging` = a canary candidate forked from `main` for A/B grading. |
| **Candidate buffer** | `.candidates.yml` inside each skill — gitignored, overwrite-on-rewrite. The cluster agent appends `{atom_id, weightscore}` entries; SkillEditAgent fires once the **sum** of weightscores crosses threshold. |
| **Canary**     | Per-skill A/B between `main` and `staging`. Runs as an independent watcher loop — promote/reject decided by ≥5 ux samples on each side. |
| **UX score**   | LLM-as-judge rubric on each atom — grades how well the resolved skill served the user from chat-archive feedback. |
| **Registry**   | The list of watched directories. Add a path → the watcher polls it forever. |

## How xskill compares

Before building xskill we surveyed 10 academic / open-source trajectory→skill systems (Hermes, OpenSpace, EvoSkill, AutoSkill, AgentEvolver, MemSkill, EvoAgentX, SE-Agent, SkillRL, GEPA). The full ~270-line cross-cutting matrix lives at [`docs/research/related-work-survey.md`](docs/research/related-work-survey.md) — each cell carries `path:line` evidence.

**What xskill borrows from the field**

- *SKILL.md as the cross-agent unit* — OpenSpace / EvoSkill / AutoSkill all converged here; we follow the same Anthropic frontmatter schema for portability.
- *LLM-as-judge UX scoring* — AutoSkill's per-turn `relevant/used` signal (`autoskill/interactive/usage_tracking.py`) inspires our `ux_score` rubric.
- *per-skill git versioning* — EvoSkill's "git branch = program version" (`src/registry/manager.py:33-95`); we put a `.git` inside every skill directory.
- *full provenance* — OpenSpace records `parent_skill_ids + source_task_id + created_by + change_summary`; xskill keeps the equivalent in each skill's git log.

**What xskill does that none of the 10 surveyed projects do**

> *"真正灰度 / A-B：10 个项目无一实现。"* — survey §10
- **Real canary A/B**: each skill has its own `main` / `staging` branches; chat traffic is split by probability, two-sided UX scores ≥ N samples decide merge or discard. No human in the loop.
- **Symmetric ingestion**: per-turn streaming (drop a file → watcher picks it up) *and* batch backfill (`xskill registry add /path` reindexes a whole archive) are first-class — most surveyed projects pick one.

**Open gaps the survey identifies (our roadmap)**

- usage-stat-driven auto-prune (AutoSkill `retrieved>=40 && used<=0` rule)
- git-style 3-way merge by common ancestor (GEPA's `merge.py:118-207`)
- BM25 → embedding cosine → LLM-judge three-stage retrieval (OpenSpace)
- multi-code-agent adapters — see Roadmap below

## Roadmap

- [ ] **More code-agent adapters** — Codex, OpenCode, Goose, OpenHands, Cursor, Aider on both ends (trajectory ingest + skill emit)
- [ ] usage-stat-driven auto-prune (`retrieved>=N && used<=0` deletion)
- [ ] git-style 3-way merge for multi-source skill consolidation
- [ ] BM25 + embedding + LLM-judge three-stage retrieval reranker
- [ ] Web UI for browsing skills, viewing canary stats, manual merge/discard
- [ ] Skill marketplace: import / export portable skill bundles
- [ ] Multi-tenant skill repos (per-team `skill_dir`)
- [ ] Native MCP server interface (skills as tools)
- [ ] Async embedding backend for large registries

Have an idea? Open an [issue](https://github.com/SkillNerds/xskill/issues).

## Development

```bash
git clone https://github.com/SkillNerds/xskill
cd xskill
pip install -e .[dev]
pytest -q
```

Internal design notes live under [`docs/`](docs/) (English & 中文 mixed).

## Contributing

PRs welcome — please:
1. Open an issue describing the problem first.
2. Add or extend a test (no test, no merge).
3. Keep public API additions in `xskill/__init__.py` minimal — we guard the surface area.

## License

MIT © [370025263](https://github.com/370025263). See [LICENSE](LICENSE).

---

<div align="center">

If xskill saves your agents from repeating themselves, a ⭐ on [GitHub](https://github.com/SkillNerds/xskill) helps others find it.

</div>
