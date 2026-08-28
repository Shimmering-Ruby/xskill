# Atom splitting and routing replay

This suite evaluates immutable, recorded algorithm outputs.

It does not call an LLM, an embedding endpoint, Milvus, or a coding-agent CLI during normal tests.

Run the checked-in v1 baseline with `python -m scripts.bench.algorithm_replay.evaluate scripts/bench/algorithm_replay/fixtures/baseline_v1.json`.

Run the candidate-boundary v2 baseline with `python -m scripts.bench.algorithm_replay.evaluate scripts/bench/algorithm_replay/fixtures/baseline_v2.json`.

Use `--format json` for a machine-readable report.

The test suite compares both reports with their checked-in `*.report.json` snapshots, so schema and metric-definition changes are explicit and reviewable.

## Fixture contract

- `schema_version`: versions `1` and `2` are supported, while unknown versions fail loudly.
- `metric_config.routing_recall_k`: the candidate cutoff used by Recall@K.
- `metric_config.atom_alignment_min_iou`: the minimum interval IoU used to align a prediction with a gold Atom for duplicate and routing metrics.
- `run_manifest`: the repository revision, model, harness, prompt fingerprint, seed, generation parameters, token counts, cost, and generation time for the recorded predictions.
- `skill_catalog`: the only valid routing labels in the suite.
- `cases`: line-addressable synthetic trajectories, human-authored gold Atoms, and immutable predicted Atoms whose `line_count` exactly matches `source_lines`.

Atom ranges are 1-based half-open intervals `[start_line, end_line)`.

Gold ranges must not overlap, while predicted ranges may overlap because overlap is a measured failure mode.

`scorable_ranges` identifies the source lines used by coverage and overlap metrics.

The checked-in fixtures are synthetic and privacy-safe.

Their model name is `recorded-fixture`; they validate evaluator contracts and do not claim current online model quality.

The prompt fingerprints hash literal sentinel strings because no model prompt was used.

A real offline run must replace the run manifest and prediction sections while preserving the selected schema.

## Version 2 candidate-boundary contract

Schema v2 retains every v1 field and adds `metric_config.boundary_score_thresholds` plus a `boundary_candidates` list to each case.

Each candidate records an internal scorable `line`, a numeric `boundary_score` in `[0, 1]`, a non-empty `algorithm_version`, a `selected` boolean, and a `predicted_atom_id` when selected.

A v2 suite must contain exactly one `algorithm_version`, and the report copies it to `boundary_algorithm_version` so aggregate scores from different rankers cannot be mixed silently.

Candidate lines are unique within a case and cannot use the forced start of a scorable range.

Every selected candidate must map to a predicted Atom that starts on the same line, and every predicted Atom that starts inside a scorable range must have exactly one selected candidate.

A rejected candidate has `predicted_atom_id: null` because it did not produce an Atom.

`boundary_score` is an uncalibrated ranking signal recorded for offline analysis.

It is not a probability, production confidence field, `ux_score`, or `(atom_id, skill)` `weightscore`.

## Metric definitions

- Boundary precision/recall/F1 compares exact internal Atom start lines and excludes the forced start of each scorable range.
- Pk and WindowDiff reuse the independently tested implementations in `scripts/bench/evaluate.py` to expose near-miss and over/under-segmentation behavior.
- Coverage is the fraction of scorable lines covered by at least one predicted Atom, while overlap rate counts repeated predicted coverage over the same denominator.
- Duplicate rate aligns each prediction to the gold Atom with maximum interval IoU at or above `atom_alignment_min_iou`, then counts additional predictions aligned to an already matched gold Atom.
- Language consistency detects the dominant script of `intent + summary` after removing inline code and path-like tokens, and an output with no detectable natural language is a mismatch.
- Routing micro precision/recall/F1 compares `(gold_atom_id, skill)` relations after interval alignment, while macro precision/recall/F1 is the unweighted mean of per-case scores.
- Recall@K uses each prediction's ordered `candidates` list.
- Multi-Skill relation retention measures gold relations belonging to Atoms with more than one expected Skill so a valid one-to-many relation cannot silently collapse to one label.

When both sides have no internal boundary, boundary precision, recall, and F1 are `1.0`.

Other empty-set behavior follows the existing benchmark: precision, recall, and F1 are `1.0` only when true-positive, false-positive, and false-negative counts are all zero; duplicate and overlap rates are `0.0` without an applicable denominator; other vacuously satisfied ratios are `1.0`.

## Version 2 score analysis

Candidate-boundary AUROC labels a candidate positive when its line is an exact internal gold boundary and computes rank discrimination with half credit for tied scores.

A gold boundary that was never proposed cannot enter candidate AUROC and remains visible as a false negative in the existing boundary recall metric.

AUROC is `null` when a case or aggregate contains only one label class because discrimination is undefined.

Routing-error analysis includes only selected candidates because rejected candidates have no downstream Atom to route.

The selected candidate's predicted Atom is aligned with the existing IoU rule, and routing is erroneous when the Atom is unmatched or its final `skills` set differs from the aligned gold Atom's complete `skills` set.

`low_score_error_auroc` uses `1 - boundary_score` as the ranking signal, so a value above `0.5` means lower boundary scores tend to rank routing errors ahead of correct routes.

For each fixed `boundary_score_thresholds` value, the report includes eligible and retained samples, retained coverage, routing errors, and routing error rate.

Coverage and routing error rate are `null` when there are no eligible or retained samples, rather than claiming vacuous success.

Candidate and routing AUROC each run in `O(n log n)`, and the threshold table runs in `O(n log n + t log n)` for `n` selected candidates and `t` thresholds.

These metrics show association, not causation or probability calibration.

Do not turn a score or metric into a blocking quality threshold until a maintainer has reviewed a representative recorded baseline.

Deterministic schema and metric tests remain blocking regardless of model quality.
