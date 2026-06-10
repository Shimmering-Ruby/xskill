# Author Response (Rebuttal) — xskill

We thank all three reviewers for unusually careful, source-aware reviews. The reviews converge (all scored 4) on a coherent message we accept: the **design and survey are solid, but the empirical claims and the statistics were overstated, and several factual/consistency details were wrong**. We have substantially revised the paper accordingly. Below we respond by theme, marking each as **[Conceded + fixed]**, **[Clarified]**, or **[Respectfully push back]**, with the revision location.

---

## 1. "The headline canary result (p=0.038) is fabricated" — R1‑W1, R2, R3‑W6  **[Conceded + fixed]**

The reviewers are right that the worked example presented synthesized numbers in a way that read as measured results. This was never intended as evidence, but the framing invited the misreading.

**Revisions:** We now label the worked example as *explicitly hypothetical* at its head (§6.2), state in the §6 opening that all worked-example, throughput, and convergence numbers are *illustrative/estimated, not measured*, and changed Contribution 5 and the §6 framing from "demonstrating" to "illustrating." We do **not** claim to have *run* the canary; the contribution is the *design* of online behavioral-UX canary, and we now separate "first to design/build" from "first to evaluate-via" throughout.

We accept that without a real deployment this caps the paper's empirical standing; we are transparent that a controlled study is future work (Limitations item 1). We would welcome the chance to add even an N=1‑team pilot for camera‑ready.

## 2. The "13× budget overflow" is mis-modeled (chars vs tokens; CC drops vs shrink-to-fit) — R1‑W2, R3‑W3  **[Conceded + fixed]**

This was a genuine factual error and the reviewers caught it precisely. We re-derived it against Claude Code source:
- The listing budget is `DEFAULT_CHAR_BUDGET = 8000` chars (≈ 2,000 tokens ≈ 1% of a 200K‑token window), **not** 2,000 chars. The 26,448‑char listing is ≈ 6,600 tokens, giving an **≈3.3× overflow, not 13×**.
- Claude Code does **not** "drop least-invoked skills"; it applies a **shrink‑to‑fit** policy (`formatCommandsWithinBudget`) that keeps all skills and uniformly truncates non‑bundled descriptions (per‑entry cap 250 chars), degrading the least‑recently‑loaded to name‑only only in the extreme.

**Revisions:** Table 2 rows, the §6.1 paragraph, and the abstract are corrected to 3.3×, consistent token/char units, and the verified shrink‑to‑fit mechanism. The motivation survives (compressed/name‑only descriptions still degrade selection), but is now correctly stated.

## 3. The statistical protocol ignores pseudo-replication, peeking, and multiple comparisons — R1‑W4/W5, R2‑W3, R3‑W8  **[Conceded + fixed in framing]**

We accept every point: (a) AtomTask UX scores cluster within users/sessions, so the effective n is the number of *developers*, not atoms; (b) collect‑until‑n_min plus a catalog of concurrent tests invites optional‑stopping and multiple‑comparison inflation; (c) bounded discrete scores argue for rank/permutation tests; (d) the low‑traffic Δ_min fallback has no Type‑I guarantee.

**Revisions:** New "Threats to statistical validity" paragraph in §4.4 stating all four explicitly, plus a new Limitations item ("Statistical validity at team scale"). We now frame the Welch test as a *pragmatic first cut* and name the principled replacements (user‑clustered/mixed‑effects, sequential/always‑valid p‑values, FDR control). We **respectfully push back** only on framing: these are limitations of the *test currently bolted on*, not of the git‑branch canary *mechanism*, which is test‑agnostic — so the contribution (online behavioral evaluation of skill versions) stands even as the specific test is upgraded.

## 4. The UX score is arbitrary, unvalidated, and still LLM-mediated — all three  **[Conceded + fixed]**

Correct on all counts. The weights (5/3/2), saturation (3), and θ=10 were defaults, never calibrated; and because the TaskAgent (an LLM) extracts the signals, the "avoids LLM‑as‑judge" claim was too strong.

**Revisions:** §4.2.1 now states the distinction is one of *target* (extracting observable indicators vs. rating output quality directly), explicitly concedes extraction is LLM‑mediated with unmeasured detector error, lists *all* magic constants as unvalidated defaults, and scopes the claim down to "a reasonable, less self‑referential basis for comparison," not "a validated measure of skill quality." Calibration against ground‑truth satisfaction is named as key future work.

## 5. After SkillClaw, residual novelty is one untested axis — R2‑W1/W2/W5  **[Conceded in part + clarified]**

We agree the defensible delta narrows to **online live‑traffic behavioral canary vs. offline scenario A‑B**, and that we had not shown this axis *matters*.

**Revisions:** We added an explicit statement (§2.1) that *whether the two regimes reach materially different promote/freeze decisions is an open empirical question we have not resolved* — and that isolating this is the key experiment the design motivates. We **respectfully push back** on "thin": the contribution is not only the canary axis but also (i) the source‑code‑level **survey of 11 systems** (which R1/R3 both credit as genuinely useful), (ii) the **5‑ecosystem single‑artifact distribution** (no direct competitor; SkillClaw is single‑proxy), and (iii) the **"ceiling‑bounded by authoring LLM"** finding. We take R2's suggestion to foreground (ii) and (iii) rather than betting identity solely on the canary.

## 6. "Orthogonal to the runtime" contradicts the budget-management value prop — R3‑W2  **[Conceded + fixed]**

Fair. **Revision (§5.5):** "orthogonal" now explicitly means *mechanism* (we do not hook the loader), not *effect* — xskill is deliberately co‑designed against the runtime's listing‑budget pressure.

## 7. Cold-start bypasses the central quality gate — R1, R3‑W4  **[Conceded + fixed]**

Correct, and it bites hardest for new teams. **Revision:** Limitations item 3 now states the gate is inactive precisely while the catalog is built, and proposes a *probationary canary* that retroactively evaluates main‑committed cold‑start skills once traffic accrues.

## 8. Internal inconsistencies — R3‑W1, R3 nits, R2‑W7c  **[Conceded + fixed]**

- **11 vs 12 count:** SkillClaw is concurrent work, *not* one of the 11 surveyed systems. Captions of Table 1 and the appendix matrix now say this explicitly (SkillClaw marked †, not numbered among the 11).
- **AutoSkill "three‑layer" vs "six‑layer":** unified to "multi‑stage … six stages in full."
- **`f_UX` vs `s(a)`:** now cross‑referenced as the same function.
- **path:line survey citations:** clarified as released with the open‑source code.

## 9. Suspicious model names / citation — R1‑W7, R2‑detailed, R3‑W10  **[Fixed; one item needs author confirmation]**

- **`Qwen3.6-27B` / `Qwen3-0.6B-Embed`:** generalized to "a 27B‑class open model" in §6.3 and Limitations; the unused embedding‑model dependency (R3‑W10: named but absent from the pipeline) is removed. *Authors to insert the exact released checkpoint used.*
- **SkillClaw arXiv id 2604.08377:** taken from the SkillClaw repository; *authors to confirm the final published identifier before submission.*

## 10. Sanitization threat model — R3‑W7  **[Conceded + fixed]**

**Revision:** New Limitations item on sanitization and the server trust boundary (regex redaction does not remove PII/internal paths/proprietary code; the server operator is an implicit trust boundary; content‑level sanitization needed for sensitive deployments).

---

## Summary of changes
Abstract, §1 (contrib 5), §2 (count captions, survey‑citation note, SkillClaw open question, sandwich wording), §4.2.1 (UX honesty + constants), §4.4 (statistical‑validity paragraph), §5.5 (orthogonality), §6 (framing + corrected 3.3× budget + shrink‑to‑fit + estimated throughput), §7 Limitations (+3 items: statistical validity, cold‑start, sanitization; model‑dependency corrected), Appendix matrix (renumber). Paper compiles to 20 pages.

## What we cannot fix in revision (and acknowledge)
A real measured deployment and a UX‑score validation study. We have reframed every claim to be defensible *without* them and flagged them as the central future work, rather than dressing up illustrative numbers as evidence.
