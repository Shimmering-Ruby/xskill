# xskill — How It Works

## The distillation pipeline

The watcher in `xskill serve` scans every registered coding-agent dir **every 30s**. New
trajectories (or appended deltas) flow through three agents in order:

```
raw trajectory (.md)
   └─ TaskAgent          → splits into AtomTasks (single-intent slices)
        └─ TaskClusterAgent (one per atom)
                          → routes the atom into an existing skill's .candidates.yml,
                            or creates a new baby skill folder
              └─ SkillEditAgent (fires when a skill's candidates weightscore ≥ 10)
                          → writes/updates SKILL.md + aux files, git-commits the version
```

### TaskAgent
Reads a trajectory and agentically splits it into **AtomTask**s at semantic boundaries
(topic change, task completion, intent shift). Atoms — not whole trajectories — are the
minimal unit of skill construction.

Output: `~/.xskill/<eco>_sessions/<traj_id>/tasks/atom_<traj_id>_NNNN.json`. Key fields:
`intent`, `summary`, `tags`, `used_skills`, `ux_score`, `offset_start/end`,
`pre_atom_id`/`post_atom_id` (linked-list neighbors).

### TaskClusterAgent (one invocation per atom)
Sees the full skill catalog (name + truncated description, like Claude Code's listing) as
routing context, then either appends the atom to a chosen skill's `.candidates.yml` (with
a `weightscore` 0–10) or calls `new_skill_folder` to start a new **baby** skill. It may
also rename a baby skill or move an atom to a better-fitting skill.

### SkillEditAgent
Fires when a skill's accumulated `.candidates.yml` weightscore reaches the threshold
(default 10). It reads the atoms (and optionally the raw trajectory), writes `SKILL.md`
plus any scripts/references, then commits one of:

- **baby branch** → `commit_baby_to_main()` — the first public version
- **main branch** → `commit_to_staging()` — a grey candidate for canary comparison

Guards: no `staging` branch already exists, weightscore ≥ threshold, and (if on `main`)
at least one real `side=main` UX score exists (proves main is actually in use).

## Canary (grey-test) & UX scoring

New skill versions don't win by fiat — they win on real traffic. A version lives on a
git branch; a fraction of traffic is routed to `staging` and compared to `main` by
**UX score** (an LLM-as-judge scores each served atom 1–10).

- **Standalone:** time-window bucketing. `pick_side(window_id, skill_name, probability)`
  with `window_id = now // rotate_interval` (default 300s), probability 0.2. Staging is
  materialized into a `.canary/` subdir. After `min_samples` (default 5) per side:
  `staging_avg ≥ main_avg` → promote, else discard. Staging max hold 14 days.
- **Team:** per-client deterministic routing. `pick_side(client_id, skill_name, prob)` —
  the same client always gets the same side for a skill. The server decides each client's
  side at manifest time, so there is no time-window rotation and no `_reconcile_skill_sides`.

UX attribution reads a `xskill:skill=X side=Y sha=Z` header from the trajectory so each
score is credited to the exact version that served it.

## Deployment modes

| | Standalone | Team |
|---|---|---|
| Start | `xskill serve` | server `xskill serve --server`, client `xskill connect` |
| LLM calls | local machine | **server only** (clients make none) |
| Git authority | local | server owns `main`; clients limited to `user-staging/<id>` |
| Canary routing | time-window | per-client deterministic |
| Skill install | symlink/copy into local agent dirs | server ships git bundles → client applies → installs |

Team config lives under `team:` and `canary:` in `config.yaml` (server host/port/token,
upload/sync intervals, canary probability/min_samples/max_days_hold).
