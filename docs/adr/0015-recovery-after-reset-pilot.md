# ADR-0015: Recovery after reset -- a ceiling on success, a real effect on cost

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: measuring `docs/BENCHMARK_PROTOCOL.md`'s "Recovery quality
  after reset" row — named there as the harness's most valuable and, until
  now, entirely unmeasured claim.

## Context

The protocol flags this metric as normally needing to be "judged by a model
or a human" — exactly the uncalibrated, subjective scoring T1 forbids
publishing without calibration (`docs/RESEARCH_FINDINGS_LEGACY.md`). Rather
than build a judge, the task was designed so the project's usual objective
signal — does `pytest` pass, on a genuine fix rather than a patched test —
doubles as the recovery signal. This sidesteps the subjectivity problem
entirely rather than solving it.

## Decision 1: the prior investigation is fabricated, not a second live agent

A two-live-agent design (one investigates, a second recovers its work) would
confound "did the first agent investigate well" with "does recovering its
work help" — two different questions. The "prior investigation" each
`verity` trial's handoff recovers is instead written directly via
`verity task` / `verity remember decision` / `verity remember discovery`
(`setup_phase_a.sh`), with fixed, correct content naming the exact root
cause and file. This isolates the question the pilot means to answer.

## Decision 2: a harder bug than ADR-0011's, but success still ceilings

ADR-0011's pilot used a single-line bug where `pytest`'s own failure message
named the wrong value directly — both conditions hit 100% success because
locating the fix was never hard. This pilot's fixture requires tracing two
function calls (`calculate_total` → `apply_discounts` →
`get_bulk_discount_rate`) to find a bug in a file `pytest`'s failure message
never names. It is harder — but not hard enough: all 10 trials, both
conditions, found and fixed the real bug, verified independently (`pytest`
pass plus a diff confirming `tests/` was untouched). Success rate alone is,
again, `indistinguishable_from_noise` — a ceiling, not a null result.

## Decision 3: report the cost metric that wasn't ceilinged

Rather than treat the ceiling as the end of the story, tool-call count per
trial (an objective, harness-observable count, not a judged score) was
recorded as a secondary metric. Naive trials: 6, 8, 5, 7, 7 (floor `[5, 8]`,
established from naive's own repeats). Verity trials: 5, 5, 5, 4, 5 (mean
4.8) — below naive's floor, `likely_real_difference`. Every `verity` trial
read the handoff first and went nearly straight to the fix; every `naive`
trial spent extra calls re-deriving the same call chain the handoff had
already named.

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| tool_uses (5 trials) | mean 6.6, floor `[5,8]` | mean 4.8 | `likely_real_difference` |

## Consequences

- The headline claim from `BENCHMARK_PROTOCOL.md` — recovery after reset is
  the harness's most valuable capability — gets its first real evidence
  here, but a narrower one than hoped: this pilot shows recovery makes the
  same successful outcome *cheaper*, not that it turns failure into success.
  A harder fixture (more files, more red herrings, a bug requiring runtime
  reasoning rather than static tracing) would be needed to test whether
  recovery also changes the success rate itself.
- Tool-call count is a coarse cost proxy, not tokens or wall-clock time —
  reported because it's objective and judge-free, in keeping with this
  project's stated preference for deterministic metrics, not because it's
  the most precise cost measure available.
- This is the fourth Family B pilot in the series and the second (after
  ADR-0011) to hit a ceiling on its primary metric — a pattern worth naming:
  tasks solvable by a single competent agent turn tend to ceiling regardless
  of what aid is offered, and the interesting differences in this project so
  far have consistently shown up in *recall of specific data* (pilots 2-3)
  or *cost*, not in raw success/fail on a task a capable model can already
  do unaided.
