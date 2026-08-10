# ADR-0022: `verity eval` — a real, retained trial harness

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: Phase 0's truth-repair audit (ADR-0021) found that every
  "N trials per condition" measurement in this project's history before
  this ADR was hand-run and hand-scored. `bench/repetition.py` (the
  statistics — noise floors, `compare_to_noise_floor`) has always been
  solid; nothing fed it real, retained data. The audit's worst finding
  (invariant 7, CLAUDE.md) was concrete: pilots 4, 5, and 6's published
  tool-call numbers rested on hand-typed `results.json` files after a later
  re-run of their own setup script destroyed the post-trial code that would
  have let anyone re-check them — nothing under `experiments/*/trials/` was
  ever git-tracked.

## Decision

Add a trial harness, `bench/trial.py` + `bench/eval.py`, exposed as
`verity eval`. It does three things none of the eight prior Family B
pilots' scaffolding did:

1. **Retains an artifact per trial.** `TrialRecord.artifact_hash` is a
   content hash of the post-trial directory tree. Two trials of the same
   condition hashing identically is now checkable, not assumed; a directory
   that no longer matches its recorded hash is detectable as tampered or
   lost, rather than silently trusted.
2. **Never trusts the agent's own report.** `TrialRecord.scorer_exit_code`
   comes from running `spec.scorer_command` (e.g. `pytest -q`, or a
   committed hidden-test module) as a real subprocess in the trial
   directory — the same discipline every pilot's README already claimed in
   prose, now enforced structurally rather than by convention.
3. **Flags a degenerate noise floor instead of hiding it.** Re-auditing this
   project's own history: 7 of 9 prior pilots had every within-condition
   repeat land on the exact same value for their headline metric (`[0, 0]`
   or `[1, 1]`). Under that condition, `compare_to_noise_floor` reports
   `likely_real_difference` for *any* nonzero between-condition value at
   all — a real effect and an artifact of too little variance look
   identical. `EvalReport.is_publishable` is `False` whenever this happens,
   with the specific metric named in the warning.

`bench/repetition.py` needed **zero changes** — it already accepts
arbitrary `dict[str, float]` metrics per repeat (ADR-0010's generalization),
which is exactly the shape `metrics_by_condition` assembles from real
`TrialRecord`s. The reuse is the point: the statistics were never the
problem.

### What is deliberately not built here

`invoke_agent` is injected (`Callable[[Path, str, int], None]`), mirroring
CLAUDE.md rule 3 ("make the model injectable so tests pass a lambda") and
the same reasoning behind `experiments/lib/setup_phase_a.sh`'s fabricated
phase-A state: a scripted stand-in for what a condition means, not
necessarily a live agent call. `command_invoker` builds one from
`TrialSpec.condition_commands` for CLI use — a shell command per condition,
generalizing the exact shape `setup_phase_a.sh` used by hand. A live agent
invocation is still the caller's responsibility; this harness does not
launch one itself, launch a model, or manage API keys — that would be a
different, larger piece of infrastructure than "retain what a trial
produced and score it honestly."

### Verification

Re-ran pilot 8's fixture (`experiments/family_b_pilot_8_arbitrary_tiebreak/`)
through the new harness with the known naive (`max()`) and verity
(explicit tie-break) fixes as scripted stands-in, rather than live agents —
an honest reproduction of a known, previously-audited result, not a new
claim. Result: `naive` 0/5, `verity` 5/5 on `tie_correct`, both 5/5 on
`visible_pass` — matching the published numbers exactly, with five
identical naive trial directories hashing identically to each other and
five identical verity trial directories hashing identically to each other
(and differently from naive's). The harness correctly reported
`NOT PUBLISHABLE` on this run too, for the same degenerate-floor reason:
confirming a known result is not the same as establishing a fresh noise
floor, and the tool does not pretend otherwise.

## Consequences

- Pilots 4, 5, and 6 can be re-run **through** `verity eval` to convert
  their currently-caveated numbers (Phase 0) into real, retained ones —
  that re-run is future work, not part of this ADR.
- `verity eval` is not exposed over MCP, matching `bench` and
  `noise-floor` — measurement stays human-invoked.
- The failure taxonomy (`FailureMode`) is deliberately closed and small
  (`wrong_constant`, `wrong_boundary`, `plausible_idiom_wrong_on_edge`,
  `decoy_pursued`, `test_modified`, `no_change`), seeded from what this
  project's own pilots actually produced rather than invented in the
  abstract. An open-ended "explain why it failed" classifier would be a
  subjective judge wearing a costume — exactly what T1 forbids.
- **Not solved by this ADR:** cost/token accounting per trial, and any
  measure of "recovery" or "contradictions" as first-class eval metrics.
  Those are reachable via `metric_fn` (contradictions via
  `consistency/check.py::run_consistency_check`, tokens via
  `TokenCounter`) but nothing in `bench/eval.py` computes them today —
  stated here rather than implied.
