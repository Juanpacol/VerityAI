# ADR-0020: The ceiling breaks — recovery changes outcome, not just cost

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: pilot 7 (ADR-0019) tested a real code-unresolvable ambiguity
  (grace-period boundary inclusivity) but it carried a dominant linguistic
  convention strong enough that every trial guessed the fabricated policy
  correctly, unprompted. Five consecutive ceilings (ADR-0011, 0015, 0016,
  0017, 0019) raised the question of whether any design in this family
  could show recovery changing an outcome, not just its cost.

## Decision: an ambiguity with no linguistic tell, and a hidden test that exposes the laziest wrong fix

`allocation/pick_winner.py`'s bug is a one-line no-op — trivial, not the
point. The real question: on a tie in `score`, which of two candidates
wins? Candidates carry only opaque numeric `id`s; nothing in the naming
suggests an answer, unlike "grace period." The fabricated phase-A decision
states the policy (lower `id` wins) explicitly and warns that `max()`'s
default tie behavior does not reliably match it.

The hidden test (never shown to any trial) uses two tied candidates with
the lower-`id` one listed **second** in the input — deliberately, so that
`max(candidates, key=lambda c: c["score"])`, the most natural-looking
correct fix, returns Python's first-seen maximum on a tie: the wrong
candidate here. This exposes the exact failure mode recovery is supposed
to prevent — a fix that looks entirely correct, passes the shown test, and
is wrong for a reason nothing visible reveals.

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| `visible_pass` (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| `tie_correct` (5 trials) | 0/5 | 5/5 | `likely_real_difference` |

All 5 `naive` trials wrote the idiomatic `max()` one-liner, with no way of
knowing it was wrong on a tie — nothing in the code or the visible test
says ties are even possible. All 5 `verity` trials read the handoff,
encountered the explicit warning about `max()`'s tie behavior, and wrote a
tie-break comparison instead. Zero overlap between the noise floors.

## Consequences

- **This is the finding the series was built to test for.** Five ceilings
  established that recovery reliably lowers cost on tasks a model already
  solves; this pilot shows recovery can also change whether the task is
  solved *correctly*, when the thing being recovered is knowledge — not
  code structure — that no amount of reading the repository can supply.
- **The mechanism was the ambiguity's shape, not its difficulty.** Pilot 7
  failed to show a split with a harder-seeming but linguistically-loaded
  ambiguity; this pilot succeeded with an easier-seeming but genuinely
  unknowable one. The lesson for future pilots in this family: difficulty
  is not what determines whether recovery changes outcomes — the absence
  of any inferable signal is.
- **The design generalizes.** "A lazy, idiomatic fix that passes the
  visible test and fails a hidden edge case nothing hints at" is a
  reusable template for testing recovery in this project going forward,
  distinct from pilots 4-6's "harder to trace" family and pilot 7's
  "linguistically ambiguous" attempt.
- Same discipline as every prior pilot: verified by hand, before spending
  trial budget, that the lazy fix fails the hidden case and the
  tie-break-aware fix passes both, and checked for giveaway text in the
  fixture.
