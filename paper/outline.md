# xskill: Team-Level Skill Distillation, Sharing, and Evolution for Coding Agents

## Target Platform: arXiv (cs.SE / cs.AI)
## Style: NeurIPS 2025 format, 10-page main body + appendix
## Total Target: ~8000 words main body

## Abstract (250 words)
- Problem: Coding agents rely on skills but no system combines auto-distillation + team sharing + data-driven evolution
- Method: xskill = management layer above agent runtimes; three-agent pipeline + canary evaluation
- Survey: 10 trajectory-to-skill systems compared; xskill is first with canary A/B
- Key finding: SKILL.md is de facto cross-agent standard; 5 ecosystems supported

## 1. Introduction (1200 words)
### 1.1 The Rise of Coding Agent Skills (400 words)
- Claude Code, Codex, OpenCode all use SKILL.md
- Skills encode procedural knowledge for recurring tasks
- The team knowledge-sharing gap: Developer A's workflow trapped in session history

### 1.2 Three Unsolved Challenges (400 words)
- Automatic distillation from trajectories
- Team-level sharing with sanitization
- Data-driven evolution (not just LLM self-judgment)

### 1.3 Contributions (400 words)
- Cross-agent trajectory collection (5 ecosystems)
- Three-agent pipeline with cumulative evidence thresholds
- Canary evaluation protocol (first in literature)
- Comprehensive 10-system survey

## 2. Related Work (1500 words)
### 2.1 Skill-as-SKILL.md Systems (600 words)
- OpenSpace [HKU]: MCP subagent approach, 3x3 evolution matrix, but harness isolation problem
- EvoSkill [Sentient]: git branches as versions, Pareto frontiers, offline only
- AutoSkill [ECNU]: per-turn async extraction, 6-layer dedup sandwich, standalone framework
- Trace2Skill [Alibaba]: MapReduce patch merging, cross-model transfer viable

### 2.2 Non-SKILL.md Systems (500 words)
- MemSkill: PPO controller over operation banks
- EvoAgentX: workflow code evolution
- SE-Agent: per-instance system prompt YAML
- SkillRL: JSON skill records in RL training
- AgentEvolver: external ReMe memory service
- GEPA: three-way merge by common ancestor (git-style)

### 2.3 The Missing Piece: No System Does A/B Evaluation (400 words)
- Table: all 10 systems lack canary/A-B
- Most use offline benchmarks or LLM-as-judge
- Only AutoSkill tracks per-turn relevant/used but not UX
- Key gap xskill addresses

## 3. System Architecture (2500 words)
### 3.1 Cross-Agent Trajectory Collection (600 words)
- Pull-based (not push): daemon watches known filesystem paths
- 5 agents: Claude Code (JSONL), Codex (JSONL), OpenClaw (JSONL), OpenCode (SQLite), Hermes (JSONL)
- Unified TrajectoryEvent schema: source, session_id, ts, kind, data
- 30s polling watcher + secret-pattern redaction
- Figure: 5-source → normalizer → unified traj

### 3.2 Three-Agent Pipeline (1200 words)
#### 3.2.1 TaskAgent: Trajectory Decomposition (400 words)
- Segments multi-topic conversations into AtomTasks
- Fields: atom_id, intent, summary, tags, used_skills, ux_score, offsets
- UX score from behavioral signals (not LLM self-eval)

#### 3.2.2 TaskClusterAgent: Skill Routing (400 words)
- Per-atom invocation with existing skill catalog
- Three decisions: route to existing, create new (baby), reclassify
- Weighted evidence in .candidates.yml (weightscore 0-10)

#### 3.2.3 SkillEditAgent: Skill Production (400 words)
- Fires when cumulative weightscore >= threshold (default 10)
- Reads candidate atoms + optional raw trajectory
- Branch-dependent commit: baby→main or main→staging
- Never writes directly to main without canary

### 3.3 Canary Evaluation Protocol (400 words)
- Single active canary per skill; new candidates queue
- Configurable user fraction routed to staging
- UX scores collected per branch (side=main vs side=staging)
- Promotion decision: mean staging > mean main
- UX score is NOT LLM self-eval; derived from behavioral signals
- Figure: main/staging branches → user routing → score comparison → promote/freeze

### 3.4 Cross-Agent Skill Distribution (300 words)
- SKILL.md + YAML frontmatter = cross-agent standard
- Two paths cover 4/5 ecosystems: ~/.claude/skills/ and ~/.agents/skills/
- Hermes requires dedicated ~/.hermes/skills/ path
- Compatible frontmatter superset (private fields silently ignored)
- Table: output paths per ecosystem

## 4. Design Dimensions: Comparative Analysis (1500 words)
### 4.1 Trigger Mechanism (300 words)
- 8/10 offline batch; only OpenSpace + AutoSkill per-turn
- xskill: 30s watcher + per-atom async
- Table: trigger comparison

### 4.2 Skill Production (200 words)
- All 10 use LLM-as-author
- xskill adds cumulative evidence requirement

### 4.3 Deduplication and Merging (300 words)
- Range: zero (Hermes) to 6-layer sandwich (AutoSkill)
- xskill: catalog-aware LLM routing (moderate complexity)

### 4.4 Evaluation and Retirement (300 words)
- 7/10 never delete; only 3 have real retirement
- xskill: canary + freezing
- Key insight: no existing system does A/B

### 4.5 Skill Injection (400 words)
- 5 strategies across 10 systems
- xskill deliberately avoids injection: publish to disk, runtime-agnostic
- Preserves native caching/trimming/compaction
- Table: injection strategies

## 5. Discussion (1200 words)
### 5.1 Design Trade-offs (600 words)
- Pull vs push trajectory collection (latency vs zero-config)
- Cumulative evidence vs single-trajectory (quality vs speed)
- UX scoring vs LLM judging (bias vs noise)

### 5.2 Limitations (400 words)
- No formal benchmark evaluation (engineering system, not controlled experiment)
- Model dependency (Qwen3.6-27B + Qwen3-0.6B-Embed, ~40GB VRAM)
- Cold start (empty repo, needs traffic for canary)
- OpenCode adapter cost (SQLite polling)

### 5.3 Broader Impact (200 words)
- IP and knowledge attribution concerns
- Sanitization strips secrets but not implicit IP

## 6. Conclusion (300 words)
- First system combining: async cross-agent collection + cumulative-evidence production + canary A/B + multi-ecosystem distribution
- SKILL.md consensus + predictable filesystem = management layer viable
- Open source at github.com/SkillNerds/xskill

## References (14 entries)
## Appendix A: Full Comparison Matrix (skill definitions)
## Appendix B: AtomTask Schema (JSON example)
## Appendix C: Canary Guard Conditions (3 guards)

---

## Key Citations to Include
- Claude Code [Anthropic 2025]
- Codex [OpenAI 2025]
- OpenCode [SST 2025]
- OpenSpace [HKU, Chen et al. 2025]
- EvoSkill [Sentient, Wang et al. 2025]
- AutoSkill [ECNU, Li et al. 2025]
- Trace2Skill [Qwen Applications 2025]
- AgentEvolver [ModelScope 2025]
- MemSkill [Axelsen 2025]
- EvoAgentX [EvoAgentX Team 2025]
- SE-Agent [Xu et al. 2025]
- SkillRL [Aiming Lab 2025]
- GEPA [GEPA Team 2025, arXiv:2507.19457]
- Judging LLM-as-Judge [Zheng et al., NeurIPS 2024]
