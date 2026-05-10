<div align="center">

# xskill

**Distill reusable Skills from your AI Agent's execution trajectories — automatically.**

[![PyPI version](https://img.shields.io/pypi/v/xskill.svg?color=blue)](https://pypi.org/project/xskill/)
[![Python](https://img.shields.io/pypi/pyversions/xskill.svg)](https://pypi.org/project/xskill/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/github-370025263%2Fxskill-181717?logo=github)](https://github.com/370025263/xskill)

</div>

---

> Your agents already know how to do things. They just forget every time.
> **xskill** watches what they do, distills what works into a Skill library, and ships only the patterns that pass A/B grading.

## Why xskill

LLM agents repeat the same problem-solving over and over because their experience evaporates the moment a session ends. Hand-curated prompt libraries help, but they age fast and don't capture the *why*.

**xskill** treats every agent run (a `traj_*.md` file) as raw material:

```
traj_*.md  ──►  meta ──►  embed ──►  distill ──►  Skill (main)
                                          │
                                          └─►  Skill (staging) ──A/B──►  merge | discard
```

A daemon watches your trajectory directories. New trajectories get embedded, clustered, and turned into named **Skills**. Each Skill is its own tiny git repo with `main` and `staging` branches; new candidates are gated through canary traffic, scored by an LLM-as-judge UX rubric, and merged only when they win.

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
curl -fsSL https://raw.githubusercontent.com/370025263/xskill/main/examples/config.yaml.example \
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

```
                       ┌──────────────────────────────────────┐
   traj_*.md  ────►    │  watcher (background thread)         │
   (any registered     │     ├─ meta extraction               │
    directory)         │     ├─ embedding + index             │
                       │     ├─ distill / update Skill        │
                       │     └─ ux_score (LLM-as-judge)       │
                       └──────────────┬───────────────────────┘
                                      ▼
                       ~/.xskill/skill/<name>/
                          ├── SKILL.md              ← the prompt-shaped artifact
                          ├── candidates/           ← unpromoted patterns
                          ├── source_trajs/         ← evidence
                          └── .git/                 ← per-skill versioning
                                main  ⇄  staging   (canary A/B)
```

When a chat agent retrieves a Skill, traffic is split: `p` of requests get `staging`, the rest get `main`. After ≥ N samples on each side, xskill compares average UX scores and either merges staging into main or discards it. No human intervention required.

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
  probability: 0.2
  min_samples: 5
  max_days_hold: 14

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
| **Skill**      | A reusable, prompt-shaped artifact distilled from ≥ N supporting trajectories. Lives at `~/.xskill/skill/<name>/`, version-controlled. |
| **Candidate**  | An unpromoted pattern inside a Skill. Becomes `SKILL.md` content once enough trajs reinforce it. |
| **Canary**     | Per-skill A/B between `main` and `staging` branches. Merge or discard is decided by UX score, not by hand. |
| **UX score**   | LLM-as-judge rubric that grades how well a skill served the user, from chat archive feedback. |
| **Registry**   | The list of watched directories. Add a path → the watcher polls it forever. |

## Roadmap

- [ ] Web UI for browsing skills, viewing canary stats, manual merge/discard
- [ ] Skill marketplace: import / export portable skill bundles
- [ ] Multi-tenant skill repos (per-team `skill_dir`)
- [ ] Native MCP server interface (skills as tools)
- [ ] Async embedding backend for large registries

Have an idea? Open an [issue](https://github.com/370025263/xskill/issues).

## Development

```bash
git clone https://github.com/370025263/xskill
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

If xskill saves your agents from repeating themselves, a ⭐ on [GitHub](https://github.com/370025263/xskill) helps others find it.

</div>
