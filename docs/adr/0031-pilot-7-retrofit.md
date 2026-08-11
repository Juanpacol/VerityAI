# ADR-0031: pilot 7, retrofitted onto retained evidence

- **Status**: accepted
- **Date**: 2026-08-11
- **Context**: `experiments/UNREPRODUCIBLE.md` already named this gap —
  *"pilot 7's headline metric is a deterministic property of committed code
  but has not yet been re-run through the harness."* ADR-0027 built the
  machinery (`bench/evidence.py`, `verity eval`, `verity verify`) that closes
  invariant 7; it had never been applied to a number the project had already
  published. This is that application, on the cheapest and most clear-cut
  candidate, run first and alone so a discrepancy — if there is one — is
  found once, not five times at once.

## Why pilot 7

`family_b_pilot_7_domain_ambiguity`'s fixture (`fixture_repo/`, a small
billing module) is on disk, all ten trials' post-trial code is on disk under
`trials/`, and its headline metric — does `apply_late_fee` treat the exact
boundary day as still within grace — is a deterministic property of the code
a trial leaves behind. That puts it in the same category as pilot 8, the one
prior result with a retained artifact, and in a different category from
pilots 4-6, whose metric (`tool_uses`) is a property of agent *behaviour* no
fixture-and-scorer harness can regenerate (`experiments/UNREPRODUCIBLE.md`).

## What was built

- `experiments/family_b_pilot_7_domain_ambiguity/score_pilot7.py`, modeled
  directly on pilot 8's `score_pilot8.py`: run from `$VERITY_SPEC_DIR`, not
  from inside the fixture, so the hidden check is never visible to whatever
  produced the code being scored; prints `{"visible_pass": ..,
  "boundary_correct": ..}` on stdout; exit code tracks `visible_pass` alone.
  `boundary_correct` calls `calculate_invoice` at `days_overdue ==
  grace_days == 10` and checks for `1000.0` (no fee), per the fabricated
  phase-A policy the original pilot's README states.
- `experiments/family_b_pilot_7_domain_ambiguity/eval_spec.json`, committed.
  `condition_commands` for both `naive` and `verity` reconstruct the same
  file: inspecting all ten trial directories shows all ten independently
  converged on identical (`naive_1`-`naive_5`, `verity_1`-`verity_3`) or
  functionally identical (`verity_4`-`verity_5`, an equivalent arithmetic
  rewrite of the same comparison) code. That convergence *is* the original
  finding — the README calls it a ceiling for a new reason, not a spurious
  detail to reconcile away — so reconstructing one representative version
  per condition is not a simplification of the evidence, it is the evidence.

## Result

```
verity eval experiments/family_b_pilot_7_domain_ambiguity/eval_spec.json
verity verify experiments/family_b_pilot_7_domain_ambiguity/evidence
```

`verify` re-derives all 10 trials clean: `boundary_correct=1`,
`visible_pass=1`, `success=1` on every one, in both conditions — exactly the
5/5-and-5/5 the original hand-typed `naive_results.json` /
`verity_results.json` reported. **The number matches.** No discrepancy to
report on the metric itself.

`eval` nonetheless reports the run **NOT PUBLISHABLE** (exit 1), and that
result is also worth keeping, because it is not a defect in the retrofit —
it is the gate doing exactly what ADR-0010's noise-floor design says it
should. Both metrics show a degenerate noise floor (`[1.0, 1.0]`) on the
baseline condition: every naive trial landed on the identical value, so the
gate cannot rule out that any observed difference is an artifact of zero
variance rather than a real effect. That degeneracy is structural to a
*retrofit* of this shape, not evidence about the original pilot: a live
agent, run five times, produced five real (if identical) outcomes, while a
reconstruction that replays one fixed `condition_commands` script five times
produces five identical outcomes *by construction* — there was never any
variance for the gate to observe. The retrofit can confirm what the retained
code does; it cannot manufacture the statistical property a repeated live
trial has and a scripted replay does not.

## Consequence for how this evidence may be cited

The retained artifact under `experiments/family_b_pilot_7_domain_ambiguity/evidence/`
answers a narrower question than "is this A/B claim publishable" — it answers
"does the code each trial actually left behind produce the metric the pilot
reported," and the answer is yes, re-derivable on demand via `verity verify`.
That is real progress on invariant 7 and is cited as such in
`docs/MEASUREMENTS.md`. It is not, and should not be presented as, a
statistically stronger version of the original ceiling finding — the
original 10-live-trial result and its own stated limits (ADR-0019: one
fixture, one ambiguity, a linguistic prior that happened to match the
fabricated policy) remain the actual evidentiary basis for that claim.

## What this implies for the remaining un-evidenced pilots

- **Pilot 2 (numeric recall)** is the next-best candidate: no live-agent
  fixture to reconstruct, but its fixture is *regenerable* from committed
  generator scripts (`generate_fixture.py`), its ground truth is committed
  (`ground_truth.json`), and its metric is exact-match recall against that
  ground truth — closer to pilot 8's shape (a scorer over static text) than
  to pilot 7's (a scorer over reconstructed code).
- **Pilot 1 and the consistency pilot** have fixtures but their original
  conditions were live-agent arms scored by prose judgement
  (`ground_truth.md`) rather than an exact check; retrofitting either means
  designing a new machine-checkable scorer, not just reconstructing code, and
  is out of scope here.
- **Pilots 4-6 stay permanently unverifiable**, unchanged by this ADR — see
  `experiments/UNREPRODUCIBLE.md`. This retrofit does not reopen that
  question; `tool_uses` is not a property this technique can ever recover.
- **A retrofit of this shape always reports NOT PUBLISHABLE if every trial's
  `condition_commands` is a single fixed script.** Any future retrofit of a
  ceiling result (every trial converging on the same code) will hit the same
  degenerate-noise-floor warning for the same structural reason. That is not
  a bug to fix in `eval.py` — a real live re-run producing real variance is
  the only thing that would change it, and this project does not have live
  re-runs of these pilots to spend.
