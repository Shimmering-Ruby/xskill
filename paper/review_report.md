# xskill Paper Review Report

**Paper**: "xskill: Team-Level Skill Distillation, Sharing, and Evolution for Coding Agents"
**Reviewer**: Automated Academic Review (7-Dimension Framework)
**Date**: 2026-05-27
**Venue Format**: NeurIPS 2025 preprint (arXiv cs.SE / cs.AI)

---

## Overall Assessment

**Total Score: 22 / 35**

The paper presents a well-motivated system (xskill) with a genuinely novel contribution in canary-based skill evaluation. The 10-system survey is a valuable contribution to the field. However, the paper suffers from the complete absence of empirical evaluation, insufficient engagement with the underlying research literature beyond the surveyed systems, and several sections that read more like product documentation than academic writing. The gap between the rich research data available (as seen in the SYNTHESIS survey) and what actually appears in the paper is significant -- many nuanced findings and design insights from the survey are left on the cutting room floor.

---

## Dimension 1: Originality (3 / 5)

### Strengths
- The canary A/B evaluation protocol (Section 3.3) is genuinely novel. The SYNTHESIS survey confirms that none of the 10 surveyed systems implements this, making it a real contribution.
- The "management layer above runtimes" framing (Section 1) is an interesting architectural insight -- separating the skill lifecycle from the skill runtime is a clean conceptual contribution.
- Cross-agent trajectory normalization across 5 ecosystems is a practical engineering contribution not seen elsewhere.

### Issues Found
1. **Cumulative evidence thresholds are not as novel as claimed.** The paper presents the weightscore accumulation (Section 3.2.3) as distinctive, but the SYNTHESIS data shows that multiple systems already implement forms of accumulated evidence: OpenSpace uses 4 atomic counters + LLM judgment cascades, AutoSkill tracks per-turn relevant/used judgments, and MemSkill uses stage_avg_reward over windows. The paper should acknowledge these as related mechanisms and clarify what specifically distinguishes xskill's approach.

2. **The "first to combine" framing (abstract, contributions) is a checklist novelty argument.** While technically accurate, the combination claim is weak if each individual component is not deeply developed. The canary protocol is the only truly unique element; the other three contributions (cross-agent collection, three-agent pipeline, survey) are valuable but incremental.

3. **The SKILL.md observation is a finding, not a contribution.** The paper claims the SKILL.md standard as a key finding, but this is an empirical observation from the survey, not a design contribution of xskill.

### Improvement Suggestions
- Reframe contributions to emphasize the canary protocol as the primary novel contribution, with the survey as the secondary contribution that contextualizes it.
- Add a paragraph in Section 3.3 explicitly contrasting xskill's behavioral UX scoring with AutoSkill's LLM-as-judge per-turn evaluation (the closest related mechanism), discussing why behavioral signals are preferable with specific examples.
- Acknowledge in Section 3.2 that cumulative evidence is a spectrum, citing OpenSpace and AutoSkill's approaches, and clarify where xskill's weightscore threshold sits on this spectrum.

---

## Dimension 2: Argumentation (2.5 / 5)

### Strengths
- The paper maintains a clear narrative thread: problem (skill silos) -> survey (nobody does A/B) -> solution (xskill with canary).
- The design trade-offs discussion (Section 5.1) is honest about pull-vs-push and cumulative-evidence tradeoffs.

### Issues Found
1. **No empirical evidence supports any claim.** The paper asserts that canary evaluation is better than LLM-as-judge (Section 5.1) and that cumulative evidence reduces noise (Section 5.1), but provides zero data. Not even a toy case study or anecdotal deployment report. The Limitations section (5.2) acknowledges this but frames it as intentional ("engineering system, not controlled experiment") -- this does not excuse the lack of any evidence whatsoever.

2. **The survey claims are under-supported.** Table 1 and Figure 2 assert binary feature presence/absence for 10 systems, but the paper provides no methodology for how these were assessed. The SYNTHESIS document shows that these assessments required deep source-code reading with path:line citations. The paper should at least describe the survey methodology (code review? documentation? running systems?).

3. **The UX score derivation is hand-waved.** Section 3.2.1 says UX scores come from "signals such as: whether the user accepted the agent's output, whether corrections were needed, and whether the session segment ended naturally or was abandoned." But it never specifies how these signals are concretely measured, what the scoring function is, or how robust it is. This is the paper's central evaluation mechanism and it is described in two sentences.

4. **Logical gap in the canary protocol.** Section 3.3 says staging is promoted if mean score exceeds main's, but does not discuss sample size requirements, statistical significance testing, or how to handle variance. The canary guards (Appendix C) are useful but incomplete -- guard #3 requires one real UX score for main, which is far too few for meaningful comparison.

### Improvement Suggestions
- Add at minimum a case study section (even 0.5 pages) showing one real skill being distilled, entering canary, and being promoted or frozen, with actual UX scores. Even n=1 with real data would dramatically strengthen the paper.
- Add a paragraph in Section 2 describing the survey methodology: "We conducted source-code-level analysis of 10 open-source trajectory-to-skill systems, reading implementation files and tracing execution paths to determine feature coverage. All assessments were verified against source code rather than documentation claims."
- Expand Section 3.3 with the statistical protocol: minimum sample size, significance threshold, handling ties, and the queuing mechanism for concurrent candidates (mentioned in Appendix C but not in the main text).
- Expand the UX scoring description in Section 3.2.1 to at least a full paragraph with a concrete formula or algorithm.

---

## Dimension 3: Literature (2.5 / 5)

### Strengths
- The 10-system survey (Section 2, Table 1) is comprehensive for the specific trajectory-to-skill niche.
- Direct comparisons with OpenSpace, EvoSkill, AutoSkill, and Trace2Skill are well-drawn and specific.

### Issues Found
1. **No engagement with the broader A/B testing and online experimentation literature.** The canary protocol is the paper's primary novelty, yet there are zero citations to the extensive literature on online controlled experiments (Kohavi et al.), bandit algorithms for A/B testing, or canary deployment in software engineering. This makes the canary protocol look ad hoc rather than grounded.

2. **No engagement with the skill/knowledge management literature.** The concept of organizational knowledge capture and sharing has a long history in CSCW, knowledge management, and organizational learning (Nonaka & Takeuchi, etc.). The paper treats this as if it were entirely new to coding agents.

3. **No engagement with LLM evaluation methodology.** The paper critiques LLM-as-judge (citing only Zheng et al. 2024) but doesn't engage with the broader literature on human preference evaluation, reward modeling, or behavioral evaluation metrics.

4. **Missing related work on agent memory systems.** Systems like MemGPT/Letta, Reflexion, and Voyager (Minecraft skill library) are conceptually related but unmentioned. The skill evolution concept connects to the broader agent self-improvement literature.

5. **All 14 references are either tool URLs or the single Zheng et al. citation.** An academic paper needs to engage with prior academic work, not just enumerate GitHub repositories.

### Improvement Suggestions
- Add 8-12 academic citations covering: (a) online controlled experiments (Kohavi et al., "Trustworthy Online Controlled Experiments"), (b) knowledge management in software teams, (c) LLM self-improvement and agent memory (Reflexion, Voyager, MemGPT), (d) program synthesis and code generation evaluation, (e) multi-agent systems and prompt optimization (DSPy, OPRO).
- Add a paragraph in Section 2.3 connecting the canary protocol to the online experimentation literature, explaining how xskill adapts these principles to skill evaluation.
- Add a paragraph in Section 1 connecting the team knowledge-sharing problem to established CSCW/KM concepts.

---

## Dimension 4: Methodology (2.5 / 5)

### Strengths
- The three-agent pipeline decomposition (TaskAgent -> TaskClusterAgent -> SkillEditAgent) is a clean architecture with well-defined interfaces.
- The canary protocol has a sound conceptual design (single active canary, queuing, branch-based versioning).
- The 5-dimension survey framework (trigger, storage, production, evaluation, usage) from the SYNTHESIS is well-designed, though the paper only partially uses it.

### Issues Found
1. **Complete absence of evaluation.** No benchmark results, no user study, no deployment metrics, no ablation study. The paper cannot claim its design decisions are sound without any evidence. Even for a systems paper, some validation is expected -- throughput numbers, latency measurements, skill quality assessments, or at minimum a deployment report.

2. **The survey methodology is not described.** How were the 10 systems selected? Are they exhaustive or a sample? What criteria were used? The SYNTHESIS document reveals these were selected based on GitHub search and known systems, but the paper should state this.

3. **Key design parameters are unjustified.** Why is the default weightscore threshold 10? Why is the polling interval 30 seconds? Why are there exactly three agents and not two or four? The paper states these values without motivation.

4. **The UX scoring methodology is insufficiently specified.** As noted above, the central evaluation mechanism (behavioral UX scoring) is described in two sentences. The SYNTHESIS data (Section 4, C4) reveals this is one of the most important gaps in the field -- xskill claims to address it but doesn't show how convincingly.

5. **The five-dimension survey framework from the SYNTHESIS is underutilized.** The SYNTHESIS organized findings into trigger, trajectory storage, production, evaluation, and usage. The paper uses a subset of these dimensions (Section 4) but drops the rich trajectory-storage comparison entirely, and compresses the production and evaluation dimensions.

### Improvement Suggestions
- Add a Section 5 (Preliminary Evaluation) with at least: (a) throughput measurements of the pipeline (trajectories/hour, atoms/hour, skills produced per N trajectories), (b) one worked example showing a skill lifecycle from trajectory to canary promotion, (c) qualitative assessment of 5-10 distilled skills by the authors or team members.
- Add a paragraph in Section 4 header explaining the survey methodology and selection criteria.
- Justify key parameters in Section 3.2.3: cite related threshold selection approaches, or describe a tuning process.
- Expand the UX scoring description into a proper algorithm (Algorithm 2) with concrete signal definitions and scoring function.

---

## Dimension 5: Clarity (4 / 5)

### Strengths
- The TikZ figures (Figures 1-4) are well-designed and informative. The system overview (Figure 1) effectively communicates the architecture.
- The tables (especially Table 1, Table 5) provide clear comparative information.
- The paper's structure follows a logical flow: motivation -> survey -> system -> comparative analysis -> discussion.
- Technical details are precise where given (file paths, schema fields, branch semantics).

### Issues Found
1. **Section 3 reads like product documentation rather than an academic paper.** The level of detail about file paths (Section 3.1), YAML frontmatter (Section 3.4), and configuration details is appropriate for a README, not a research paper. This space could be better used for evaluation or deeper analysis.

2. **The paper is too short for its ambitions.** At ~4,700 words (including LaTeX markup), the main body is well under the NeurIPS 10-page limit. The outline targeted ~8,000 words. Sections that should be expanded: UX scoring methodology, survey methodology, and evaluation.

3. **Figure 2 (feature coverage TikZ) duplicates Table 1.** One of these should be cut to make room for more substantive content.

4. **The abstract is too long and reads like a feature list.** It should be tightened to emphasize the key insight (lifecycle management is orthogonal to runtime) and the primary novelty (canary evaluation).

### Improvement Suggestions
- Move detailed file paths and YAML examples to an appendix. Use the freed space for evaluation content.
- Remove either Figure 2 or Table 1 (they convey overlapping information). Keep the table as it is more precise.
- Shorten the abstract to ~200 words, focusing on the problem, the key insight, and the canary evaluation novelty.
- Add more "so what" analysis throughout Section 4 -- currently the comparative analysis states facts but rarely draws implications for system design.

---

## Dimension 6: Impact (4 / 5)

### Strengths
- The problem is real and timely. The proliferation of coding agents (Claude Code, Codex, OpenCode) with skill systems creates genuine need for management infrastructure.
- The open-source release increases potential impact.
- The survey (Table 1, Section 4) provides the first structured comparison of this nascent field, which is independently valuable to researchers and practitioners.
- The SKILL.md standardization finding is practically useful for the ecosystem.

### Issues Found
1. **The lack of evaluation limits the paper's influence.** Reviewers and practitioners will ask "does it work?" and the paper provides no answer.
2. **The hardware requirement (40GB VRAM for Qwen3.6-27B) limits accessibility.** This is acknowledged in limitations but alternatives are not discussed.
3. **No discussion of how the canary protocol scales.** With many skills and limited user traffic, the canary evaluation could take very long to converge. This practical concern is unaddressed.

### Improvement Suggestions
- Even minimal deployment data (e.g., "we deployed xskill for 2 weeks with a team of 5 developers; the system distilled N skills, M entered canary, K were promoted") would significantly boost impact.
- Discuss canary convergence time estimates and strategies for low-traffic scenarios.
- Mention model-agnostic pipeline design -- if smaller models can be substituted, say so explicitly.

---

## Dimension 7: Technical (3.5 / 5)

### Strengths
- The architecture is technically sound and well-specified (three-agent pipeline, git branching model, canary guards).
- The cross-agent trajectory collection design (Section 3.1) demonstrates deep knowledge of each ecosystem's internals, verified by the ecosystem survey data.
- The canary guard conditions (Appendix C) show careful engineering thinking (single active canary, queued candidates, baseline requirement).

### Issues Found
1. **Several citations are incomplete or informal.** References 8-12 cite GitHub URLs with team/project names but no author lists, paper titles, or publication venues. If these have associated papers (many do -- EvoAgentX, SE-Agent, SkillRL likely have arXiv preprints), those should be cited instead.

2. **The GEPA citation (reference 13) has an arXiv ID (2507.19457) but is listed as "GEPA Team" without authors.** The actual paper should be cited properly.

3. **Algorithm presentation is missing.** The three-agent pipeline and canary protocol are described in prose but would benefit from formal algorithmic presentation (pseudocode in Algorithm environment). The outline mentions Algorithm 1 for the pipeline but it was not implemented.

4. **The TrajectoryEvent schema (Section 3.1) lacks formal definition.** The paper mentions fields in prose but never gives a complete schema or type definition. The appendix has AtomTask schema but not TrajectoryEvent.

5. **Inconsistency in the survey coverage.** Table 1 lists 11 systems (10 surveyed + xskill), but the text mentions "10 surveyed systems." However, the table shows 11 rows including xskill. The SYNTHESIS data shows Hermes is included in the 10, but in the paper Hermes is treated both as a surveyed system and as a supported ecosystem, which could confuse readers.

### Improvement Suggestions
- Update all GitHub-only references to include arXiv papers where available. Search for: EvoAgentX (likely has a paper), SE-Agent (JARVIS group publishes regularly), SkillRL (Aiming Lab likely has a preprint), MemSkill, AgentEvolver.
- Add Algorithm 1 (Three-Agent Pipeline) and Algorithm 2 (Canary Evaluation Protocol) in pseudocode.
- Add the TrajectoryEvent schema to Appendix B alongside the AtomTask schema.
- Clarify in Section 2 that Hermes is both a surveyed system and a supported ecosystem, and why.

---

## TOP 5 Most Impactful Improvements

These are ranked by expected improvement to overall paper quality if implemented:

### 1. Add an Evaluation Section (Sections 3-5, new Section)

**Current state**: Zero empirical evidence for any claim.

**What to add**: Insert a new Section 5 "Preliminary Evaluation" (before current Discussion) containing:
- **Deployment report**: Deploy xskill with a small team (even 3-5 developers) for 1-2 weeks. Report: number of trajectories collected, atoms extracted, skills created, canary evaluations completed, skills promoted vs. frozen.
- **Worked example**: Walk through one complete skill lifecycle with real data -- show the raw trajectory, the atoms produced, the clustering decision, the SKILL.md produced, and the canary UX scores.
- **Skill quality assessment**: Have team members rate 10 distilled skills on a 1-5 scale for accuracy, usefulness, and completeness. Compare against manually-written skills if available.
- **Pipeline throughput**: Measure latency from trajectory append to skill candidate creation. Report on Qwen3.6-27B inference costs per trajectory.

**Expected impact**: This single addition would raise the Argumentation score from 2.5 to 4 and the Methodology score from 2.5 to 3.5, adding approximately 3 points to the total.

### 2. Expand UX Scoring Methodology (Section 3.2.1 and Section 3.3)

**Current state**: The paper's central evaluation mechanism is described in two sentences. The UX score is asserted to be "derived from observable behavioral signals" but no formula, algorithm, or concrete definition is given.

**What to add**:
- In Section 3.2.1, add a formal definition of the UX scoring function. Specify each behavioral signal, how it is detected, and how signals are combined:
  ```
  Signal 1: task_completion (binary) -- did the atom's intent get fulfilled? Detected by: presence of natural topic transition vs. session abandonment.
  Signal 2: correction_count (integer) -- how many user corrections followed? Detected by: user messages containing negation/redirection after assistant output.
  Signal 3: skill_attribution (binary) -- was a skill self-reported as used? From used_skills field.
  Scoring: s = w1*completion + w2*(1 - min(corrections/3, 1)) + w3*attribution
  ```
- In Section 3.3, add the statistical protocol: minimum sample size per branch (e.g., n >= 20), significance threshold (e.g., one-sided t-test at alpha = 0.05), and what happens when traffic is too low.

**Expected impact**: Raises Methodology and Technical scores. This is the credibility bottleneck of the paper's central claim.

### 3. Strengthen the Literature Section (Section 2, Introduction)

**Current state**: 14 references, 13 of which are GitHub URLs. No engagement with online experimentation, knowledge management, agent self-improvement, or LLM evaluation literatures.

**What to add**:
- Section 1, paragraph 2: Add 1-2 sentences connecting team knowledge sharing to CSCW/knowledge management literature. Cite Nonaka & Takeuchi (knowledge creation), and a recent survey on developer knowledge sharing.
- Section 2.3, new paragraph: "The canary evaluation protocol draws on the established methodology of online controlled experiments [cite Kohavi et al. 2013, 2020]. Key principles adapted for the skill evaluation setting include: maintaining a control group (main branch users), random assignment (configurable routing fraction), and a primary evaluation metric (UX score). However, the small sample sizes typical of team-level deployment require relaxed statistical criteria compared to web-scale A/B testing."
- Section 2.2, expand: Connect to Voyager (Wang et al. 2023, skill library for Minecraft agents), Reflexion (Shinn et al. 2023, agent self-improvement from trajectories), and MemGPT/Letta (memory-augmented agents). These represent the academic lineage of trajectory-to-skill thinking.
- Add at least 10 new references from peer-reviewed venues.

**Expected impact**: Raises Literature from 2.5 to 4 and improves Originality framing.

### 4. Formalize Key Algorithms and Move Implementation Details to Appendix (Sections 3-4)

**Current state**: Detailed file paths and YAML examples consume main-body space that could be used for algorithmic rigor. No pseudocode algorithms despite the outline planning for them.

**What to add**:
- Add Algorithm 1 (Three-Agent Pipeline): pseudocode showing the flow from trajectory delta detection through atom extraction, routing, evidence accumulation, and skill production. Include the threshold check and branching logic.
- Add Algorithm 2 (Canary Evaluation Protocol): pseudocode showing branch creation, user routing, score collection, statistical comparison, and promotion/freeze decision.
- Move to Appendix: the 5-agent file path listing (Section 3.1 bullet points), the YAML frontmatter example (Section 3.4), and Table 4 (distribution paths). These are important for practitioners but not for understanding the contribution.
- Use the freed space (~0.5 pages) for evaluation content.

**Expected impact**: Improves Technical rigor and Clarity. Makes the paper read as academic work rather than documentation.

### 5. Add Richer Comparative Analysis from the SYNTHESIS Data (Section 4)

**Current state**: Section 4 covers 5 dimensions briefly but misses many of the richest findings from the 10-project SYNTHESIS survey. The survey is the paper's second-strongest contribution but is underexploited.

**What to add**:
- Section 4.2 (Skill Production): Add the finding from SYNTHESIS C2 that all 10 systems use LLM-as-author, with the implication that "skill quality is ceiling-bounded by author LLM capability." Discuss how xskill's cumulative evidence partially mitigates this by providing richer context to the SkillEditAgent.
- Section 4.3 (Deduplication): Add a concrete comparison using the SYNTHESIS's deduplication spectrum: from zero (Hermes, SE-Agent) through LLM self-decision (EvoSkill, OpenSpace) to AutoSkill's six-layer sandwich. Position xskill explicitly on this spectrum with a rationale for the chosen complexity level.
- Section 4.4 (Evaluation): Add the SYNTHESIS finding C4 that "most projects evaluate the SKILL artifact itself, not user experience." Draw the explicit contrast: OpenSpace evaluates success/failure counts, AutoSkill evaluates relevant/used per turn, but only xskill attempts to measure downstream user benefit through behavioral UX scores.
- New subsection 4.6 (Version Control): Add the version control comparison from SYNTHESIS D5. This is architecturally important -- EvoSkill uses git branches (closest to xskill), OpenSpace uses SQLite DAG, AutoSkill uses custom semver, and 5 systems have no version control at all. xskill's git-based approach is a real design contribution worth highlighting.
- New subsection 4.7 (Cold Start): Briefly address the cold-start strategies from SYNTHESIS D6, noting that only 3/10 systems support batch trajectory import and xskill is among them.

**Expected impact**: Raises the survey from a feature-checklist exercise to a genuine analytical contribution. Adds approximately 1 page of substantive content to the thinnest section.

---

## Summary Score Table

| Dimension | Score | Key Issue |
|-----------|-------|-----------|
| 1. Originality | 3.0 | Canary protocol is novel; other contributions are incremental |
| 2. Argumentation | 2.5 | No empirical evidence; UX scoring underspecified |
| 3. Literature | 2.5 | Only GitHub URLs; no academic engagement beyond Zheng et al. |
| 4. Methodology | 2.5 | No evaluation; survey methodology unstated; parameters unjustified |
| 5. Clarity | 4.0 | Good figures and structure; too much implementation detail in main body |
| 6. Impact | 4.0 | Timely problem; useful survey; limited by no evaluation |
| 7. Technical | 3.5 | Sound architecture; incomplete citations; no pseudocode algorithms |
| **Total** | **22.0 / 35** | |

---

## Verdict

The paper has a solid core: the management-layer insight, the canary protocol, and the 10-system survey are all valuable. However, in its current form it reads as a well-written technical report rather than a publishable academic paper. The three most critical gaps are: (1) complete absence of evaluation, (2) inadequate engagement with broader academic literature, and (3) insufficient specification of the UX scoring mechanism. Addressing these three issues would likely raise the score to 28-30/35, placing it in competitive range for a systems-track venue like NeurIPS, ICSE-SEIP, or FSE Industry Track.
