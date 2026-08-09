# ADR-0017: Changing the bug's shape doesn't break the ceiling either

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: pilots 4 and 5 (ADR-0015, ADR-0016) both used a
  "wrong-but-plausible constant" bug and both ceilinged on success. ADR-0016
  concluded this bug *class* might simply be within a capable model's
  reach regardless of recovery aid, and suggested a bug requiring
  "runtime reasoning, not static call-chain tracing" as the next test.

## Decision: a cache keyed on the wrong thing, not a wrong value

`fixture_repo/catalog/cache.py`'s `get_price(item, tier)` memoizes by
`item` alone. Read in isolation, the function looks like ordinary,
correct memoization — there is no wrong constant to spot. The bug is only
visible by tracing the actual sequence of calls `build_quote()` makes:
requesting the same item at two different tiers returns the first call's
cached value for the second. This is a genuinely different bug shape from
pilots 4-5's config swaps — it requires reasoning about call order and
state, not locating a mismatched value.

A true race condition (multi-threaded, genuinely non-deterministic) was
considered and rejected: it would introduce noise into `pytest` itself,
contaminating the very noise floor this project's Family B procedure exists
to establish (`docs/BENCHMARK_PROTOCOL.md`). The bug here is fully
deterministic and reproducible — "runtime" means "requires tracing
execution," not "non-deterministic."

## Result

| Metric | naive | verity | Verdict |
|---|---|---|---|
| success (5 trials) | 5/5 | 5/5 | `indistinguishable_from_noise` (ceiling) |
| tool_uses (5 trials) | mean 8.0, floor `[7,9]` | mean 4.2 | `likely_real_difference` |

Every trial, in both conditions, correctly diagnosed the cache-key bug —
none guessed, none patched the test, none touched an unrelated file. The
cost effect reproduced a third time, in the same range as pilots 4-5.

## Consequences

- **Fourth ceiling in the series** (ADR-0011, 0015, 0016, now this one).
  Changing the bug's *shape* — from a config swap to a call-sequence
  dependency — did not move the needle on success either. Combined with
  the prior three pilots, the pattern is now strong enough to state
  plainly: for a single-agent-turn, statically-traceable-with-effort bug in
  a small repo, current models do not appear to fail cold often enough for
  this project's trial budgets (n=5) to observe it. Breaking this ceiling
  would likely require either a fundamentally different kind of difficulty
  (a bug that needs information beyond what one failing test and a
  reasonable amount of exploration can surface — e.g. requiring domain
  knowledge not in the repo, or a much larger codebase where exploration
  cost itself becomes prohibitive) or a much larger trial budget to catch
  a low-but-nonzero cold-failure rate.
- **The cost effect is now reproduced three times** (pilots 4, 5, 6), the
  most consistent quantitative result in this project's Family B work.
  Recovery after reset reliably makes an already-achievable outcome
  cheaper; whether it ever changes an outcome from failure to success
  remains unmeasured, and this project should stop trying to force that
  question via harder single-turn code-fix tasks and instead consider it
  either open (needing much larger N to detect a rare effect) or better
  tested by a different kind of task altogether (e.g., a longer, more
  ambiguous multi-turn session where genuine confusion compounds, closer
  to pilot 3's shape than pilots 4-6's).
- Consistent with this project's standing practice: the fixture was
  checked for giveaway text before spending trial budget, learning directly
  from ADR-0016's finding in the immediately preceding pilot.
