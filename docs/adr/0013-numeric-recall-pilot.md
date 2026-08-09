# ADR-0013: A Family B design that actually detects a difference

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: correcting the ceiling effect from the first Family B pilot
  (ADR-0011) and testing the financial-figure protection rule (ADR-0012)
  under real pressure.

## Context

ADR-0011's pilot gave both conditions the same code repository and let a
live agent decide whether to use Verity's tools. Result: 20/20 successes
across both conditions, because locating a one-line, test-named bug was
never hard enough to need the tools being tested. The lesson recorded
there: a follow-up pilot needs a task where the difference the tool makes
has room to actually appear.

## Decision 1: the harness prepares context, not the agent

A design flaw would have reproduced ADR-0011's problem in a new shape: if
both conditions simply received the full raw log and one was told to use
`verity context` first, an agent free to read the whole file anyway could
match or beat the "assisted" condition, since pruning can only remove
information, never add it. That comparison cannot mechanically favor
Verity no matter what the tool does.

Instead, the **harness** prepares each condition's input up front, to the
**same fixed token budget** (800), by two different methods:

- `naive`: tail-truncate the raw log to the budget — keep the most recent
  messages, drop the oldest. This is not an arbitrary strawman; it is what
  an unmanaged sliding context window actually does on overflow, and it is
  what a fixture with a figure mentioned once, early, is specifically
  vulnerable to.
- `verity`: run `verity context --budget 800` on the same raw log — the
  real pipeline, including the financial-figure rule from ADR-0012.

Both conditions answer from a static, pre-prepared text block in a single
turn — no live tool use, no exploration. This is deliberately simpler and
cheaper than ADR-0011's multi-tool-call trials, and it isolates the
variable actually being tested: does the *prepared context* contain what's
needed, not how skillfully an agent explores.

## Decision 2: a decoy, not just noise

An earlier draft of this fixture had only the target figure and filler
noise. That makes the task nearly meaningless: if `naive`'s truncated
context contains no financial figure at all, correctly saying
"insufficient information" requires no real judgment. Adding a decoy — a
different, similarly-formatted account number and amount, explicitly
attributed to a closed, unrelated case — creates a genuine failure mode:
a `naive` condition that kept the decoy but not the target could plausibly
report the wrong figure with confidence, not just decline to answer. In
this run all 5 `naive` trials still declined rather than guessed, which is
itself worth recording (see Result) — but the fixture does not make that
outcome the only possible one by construction.

## Result

5 trials per condition, scored by exact match against a fixed ground
truth, verified directly rather than via the trial's own framing:

| Condition | Exact matches | Noise floor |
|---|---|---|
| `naive` | 0/5 | `[0.0, 0.0]` |
| `verity` | 5/5 | `[1.0, 1.0]` |

`compare_to_noise_floor` (`bench/repetition.py`, ADR-0010) reports
`likely_real_difference` in both directions — `naive`'s floor does not
overlap `verity`'s mean, and vice versa. This is the first Family B result
in this project with a real, non-ceiling verdict.

Every `naive` trial answered "INSUFFICIENT INFORMATION" rather than
reporting the decoy — the model in this environment does not guess under
an explicit instruction not to, at least on this fixture. That is a fact
about this fixture and this model, not a general property to rely on; the
decoy did its job by making the failure mode possible, whether or not it
was the failure mode observed this time.

## Consequences

- This result is specific: one fixture, one figure, one budget, one
  truncation strategy. It demonstrates the mechanism ADR-0012 added
  behaves as designed under a real constraint — it is not a general claim
  about numeric recall, and the pilot's own README says so.
- `generate_fixture.py` and `prepare_contexts.py` are committed alongside
  their outputs, so the entire pilot — fixture, both prepared contexts,
  and the comparison — is reproducible by anyone from three commands,
  per `docs/BENCHMARK_PROTOCOL.md`'s publication rule.
- Unlike ADR-0011, no infrastructure correction was needed mid-pilot here
  — the lesson from that pilot (avoid designs where more information
  always helps one side) was applied at design time instead of discovered
  by running mechanism-check trials first.
- Both noise floors are degenerate (`[0,0]` and `[1,1]`) — a ceiling in the
  *informative* direction this time, but still a reminder that this
  single-turn, low-ambiguity task does not exercise real within-condition
  variance. A next pilot testing a genuinely harder recall (multiple
  candidate figures, more ambiguous phrasing, a live multi-turn session)
  would be needed to see whether the effect holds under conditions closer
  to the noise floors seeing real spread.
