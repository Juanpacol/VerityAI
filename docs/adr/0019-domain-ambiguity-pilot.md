# ADR-0019: A fifth ceiling, but for a genuinely new reason

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: pilots 4-6 (ADR-0015, 0016, 0017) all ceilinged on success
  because their bugs had one answer derivable from the code, given enough
  reading. The user asked whether recovery changes *outcome*, not just
  cost, on a bug where the answer genuinely is not in the code at all.

## Decision: an ambiguity the code cannot resolve, hidden from the visible test

`billing/late_fee.py`'s bug is a one-line no-op — trivial to spot. The real
question is a business-rule ambiguity: does the day a grace period ends
still count as within grace (`days_overdue > grace_days`) or already
overdue (`days_overdue >= grace_days`)? Nothing in the code states this.
The one visible test uses `days_overdue=20, grace_days=10`, chosen so both
interpretations pass it identically — the divergence only shows up at
`days_overdue == grace_days`, a case never shown to any trial. That case is
scored by the harness afterward, independently, never by the agent's own
report.

This differs from pilots 4-6 in kind, not degree: there, careful-enough
reading always finds the one right answer. Here, no amount of reading the
visible code finds it — only the fabricated phase-A decision (recoverable
via `verity handoff`) states the real policy.

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| `visible_pass` (5 trials) | 5/5 | 5/5 | ceiling, as designed |
| `boundary_correct` (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` |

Every `naive` trial independently wrote the strict `>` comparison with no
access to the decision and no prompting toward the boundary question —
the same choice `verity` trials made after reading the decision explicitly.
None of the naive trials' reasoning mentioned the boundary case at all;
`>` reads as a strong default convention for "grace period" semantics.

## Consequences

- **A fifth ceiling, but a different finding than the first four.** Pilots
  4-6 ceilinged because the answer was derivable and a capable model always
  derived it. This pilot's answer was **not derivable from the code**, and
  the ceiling happened anyway — because the specific ambiguity chosen
  (grace-period inclusivity) has a dominant linguistic convention that
  happens to match the fabricated policy. That is a fact about this
  ambiguity and this model, not a flaw in the mechanism being tested: it
  says something true and useful (models have strong shared priors for
  common billing/contract conventions) but does not yet test what happens
  when no such prior exists.
- **The design itself is validated and worth reusing.** A hidden test that
  the visible test cannot distinguish is a real technique for scoring
  "correct for the right reason" vs. "happened to pass" — the next pilot
  should keep this structure and change only the ambiguity's content, e.g.
  a tie-breaking rule between two conventions with no common default (a
  coin-flip-shaped choice, not a language-shaped one), or a numeric
  threshold with no natural-language framing at all.
- Consistent with this project's standing practice: the fixture was
  verified by hand (both interpretations checked against both the visible
  and hidden test) before spending any trial budget, and the giveaway-text
  mistake from ADR-0016 was checked for and avoided from the start.
