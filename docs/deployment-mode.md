## Deployment modes

xskill has two deployment modes: **standalone** (single-user) and **team** (multi-user client-server).

## Standalone mode

Designed for single-user / indie use. Everything runs on one machine.

```
xskill serve
```

### Skill-edit behavior

Identical to team server mode — `SkillEditAgent` is triggered by the watcher when:
1. No `staging` branch exists (not already in grey-test)
2. Candidates weightscore >= 10
3. On `main` branch: at least 1 real `side=main` UX score exists

The agent writes SKILL.md and either commits `baby→main` (first publish) or `main→staging` (create grey candidate).

### Grey-testing (canary) behavior

Time-window based rotation. The watcher periodically calls `_reconcile_skill_sides()` to decide which skill version gets real traffic:

- **Bucketing:** `pick_side(window_id, skill_name, probability)` where `window_id = int(now // rotate_interval)`
- **`rotate_interval`:** default 300s (configurable in `config.yaml`)
- **Probability:** default 0.2 (20% traffic to staging)
- **Materialization:** staging branch content is checked out into `.canary/` subdirectory under the skill root
- **Decision:** after `min_samples` (default 5) UX scores per side, compare averages:
  - `staging_avg >= main_avg` → promote staging to main
  - `staging_avg < main_avg` → discard staging
- **Timeout:** staging max 14 days (`max_days_hold`); discarded if insufficient samples
- **UX scoring:** LLM-as-judge scores each AtomTask 1-10, reads `xskill:skill=X side=Y sha=Z` header from traj

### Skill installation

Skills are symlinked to local agent ecosystems (`~/.claude/skills/`, `~/.agents/skills/`, etc.) upon creation and after each canary flip.

### Limitations

- Only one user. All trajectories are processed on the same machine with the same LLM API key.
- Canary traffic splitting is time-window based, not per-user — if one user keeps the same window for hours, they get the same side.

---

## Team mode

**Client-server architecture.** The server owns all LLM calls and git operations; clients are thin collectors that send trajectories and receive synced skills.

### Server

```
xskill serve --server
```

### Client

```
xskill connect <host:port> --token <token>
```

### Trust model

Clients are **not trusted** in team mode:
- Clients have no access to the server's LLM API keys
- Clients make zero LLM calls of their own
- Clients never write to `main` git branches
- Client user edits are isolated to `user-staging/<client_id>` branches (pushed to server as reference material only)
- The server always has the authoritative skill state

### Client responsibilities

1. **Collect** trajectories from local coding agents (Claude Code, Codex, OpenCode, OpenClaw)
2. **Redact** sensitive content (API keys, tokens, paths) before upload
3. **Upload** to server via `/api/v1/team/upload`
4. **Receive** skill manifest from server (top ≤100 skills: 80 ranked + 20 recommended)
5. **Reconcile** local skill working copy to server-assigned version (SHA checkout)
6. **Install** skills to local agent ecosystems

The client daemon (`team/client/daemon.py`) runs this loop continuously.

### Server responsibilities

1. **Register** clients and issue tokens
2. **Receive** and store client-uploaded trajectories
3. **Run the full agent pipeline** (TaskAgent → TaskClusterAgent → SkillEditAgent) for all incoming trajectories
4. **Compute per-client manifests** — for each sync request, build a ranked skill list with server-assigned side

### Grey-testing (canary) behavior in team mode

Per-client deterministic routing via `pick_side(client_id, skill_name, probability)`:

- **Bucketing:** `client_id` is the bucket key (deterministic; same client always gets the same side for the same skill)
- **Manifest computation:** for each skill with a `staging` branch, the server computes `pick_side(client_id, ...)` and tells the client which SHA to check out
- **UX scoring:** server scores atoms from uploaded trajectories; side attribution is done per-client (`_score_atoms_for_traj_server` in watcher.py)
- **No time-window rotation needed** — the server already decides per-client at manifest time
- **No `_reconcile_skill_sides`** — explicitly skipped in server mode

### Skill installation on client

- Skills are shipped as git bundles from server to client
- Client applies the bundle to local working copy via `apply_repo_bundle()`
- Client calls `reconcile_skill_side()` to checkout the server-assigned SHA
- Client installs to local ecosystems (symlink or copy depending on ecosystem)

### Configuration

```yaml
team:
  server_host: "0.0.0.0"
  server_port: 8412
  server_token: "<secret>"
  max_skills: 100
  upload_interval: 30
  sync_interval: 60

canary:
  probability:   0.2
  min_samples:   5
  max_days_hold: 14
```

### Ecosystem-specific notes

Ecosystems that reject symlinks (e.g. OpenClaw) use `shutil.copytree()` instead. Canary flips are done by copy-overwriting the destination directory. A reverse-sync bridge detects user modifications (mtime > installed_at + 3min) and reverse-copies changes back to the source, enabling the edit-absorb pipeline to pick them up naturally.
