# ADR-0016: A harder bug still ceilings on success -- the cost effect holds and grows

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: pilot 4 (ADR-0015) measured recovery-after-reset and found a
  real cost effect but a success-rate ceiling (10/10 both conditions). The
  user asked to raise task difficulty until the ceiling breaks, to see
  whether recovery ever changes *whether* a task succeeds, not just its
  cost.

## Decision: two structurally identical subsystems, one broken

Pilot 4's fixture had one call chain and one plausible culprit. This
pilot's fixture (`billing/`) adds a second, healthy subsystem (`tax.py` /
`tax_rates.py`) with the exact same shape as the broken one (`late_fee.py`
/ `policy.py`, reading a stale `DEPRECATED_POLICY` instead of
`ACTIVE_POLICY`) — an agent investigating cold has to rule out the healthy
subsystem, not just trace the broken one, and could plausibly "fix" the
wrong constant within the right file's sibling table. No artificial
constraint (tool-call limit, time limit) was added — ADR-0011 already
established that forcing a result that way would be meaningless.

## A bug found and fixed before trusting the result

The first version of `late_fee.py` shipped with an explicit
`# BUG: this should read policy.ACTIVE_POLICY...` comment, an artifact of
writing the fixture. All 10 trials of that run passed, several explicitly
quoting the comment as their source. This was recognized as invalid before
being reported — it measured whether an agent can read an embedded English
bug report, not whether the fixture's design achieved anything — the same
discipline as ADR-0011, 0013, 0014, and 0015 all applied to their own
fixtures. The comment was removed, all 10 trial directories were rebuilt,
and the trials were re-run from a verified-clean fixture (checked for the
literal string `BUG` before spending the second trial budget).

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| tool_uses (5 trials) | mean 8.0, floor `[7,10]` | mean 5.2 | `likely_real_difference` |

No trial, in either condition, edited the healthy decoy subsystem or the
test file — every trial correctly localized the real bug, cold or not. The
cost gap is larger than pilot 4's (naive 8.0 vs. 6.6; verity 5.2 vs. 4.8),
consistent with a harder search space costing more to redo from scratch
while a correct handoff's cost stays roughly constant regardless of how
hard producing that handoff would have been.

## Consequences

- **Third ceiling in the series** (after ADR-0011's original pilot and
  ADR-0015). Across three fixtures of rising difficulty, no design has yet
  made a capable agent fail cold on a wrong-but-plausible-constant bug one
  or two hops from the call site. That is now a pattern, not a fluke, and
  worth stating plainly: this class of bug may simply be within reach of
  current models regardless of recovery aid. A future pilot wanting to see
  a real success-rate effect would need a qualitatively different failure
  mode — one requiring runtime reasoning, not static call-chain tracing.
- **The cost effect is now reproduced twice, and grows with difficulty.**
  Across pilots 4 and 5, recovery consistently makes an already-achievable
  outcome cheaper, and the size of that saving scales with how much
  investigation the reset would otherwise have thrown away. This is the
  most robust quantitative claim the project has made about
  `verity handoff` to date.
- Consistent with this project's standing practice (T6, ADR-0011): a
  synthetic fixture's first run is not trustworthy until checked for
  artifacts that leak the answer. This is now the fourth pilot in the
  series to catch and document exactly that kind of self-inflicted flaw
  before publishing a result.
