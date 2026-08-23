# ADR-0035: `_enforce_budget`'s tie-break dropped the newest first, not the oldest

- **Status**: Accepted
- **Date**: 2026-08-22
- **Context**: found in the same self-audit pass as
  [[0034-dropped-critical-was-dead-code]].

## The defect

`ContextPipeline._enforce_budget`'s docstring (`context/prune.py:265`):

> Within each relevance bucket, the lowest-ranked items go first, and ties
> break on original index so the oldest goes before the newest.

The adjacent comment (`:284`) repeats the claim: "Worst-ranked and oldest
dropped first." The sort key one line below it:

```python
candidates.sort(key=lambda i: (i.metadata.get("rank_score", 0.0), -i.original_index))
```

For two items tied on `rank_score`, `-original_index` is *more negative* for
the item with the larger `original_index` (the newer one), so the newer item
sorts first in ascending order and is dropped first in the loop that follows
— the exact opposite of both the docstring and the comment sitting next to
the bug.

## Decision

Drop the negation:

```python
candidates.sort(key=lambda i: (i.metadata.get("rank_score", 0.0), i.original_index))
```

Smaller (older) `original_index` now sorts first and is dropped first,
matching the stated contract.

## Consequences

- Under a tight budget with no ranking signal to break a tie (no `task`
  passed, or several same-relevance items scoring identically), the pipeline
  now actually prefers to keep the more recent turns — which is also the
  behavior `context/classify.py`'s recency rule already protects
  unconditionally one tier up, so this fix makes the budget-drop tier
  consistent with the critical tier instead of contradicting it.
- One existing test's fixture assumed the old (buggy) order:
  `tests/integration/test_cli.py::TestContext::test_output_can_be_written_to_a_file`
  ran `context` with a budget but no `--task`, so the tiebreak was the only
  thing deciding which content survived; under the corrected order the
  content it asserted on was the older item and got dropped. Fixed by adding
  `--task "rate limiting"` to the invocation, matching the sibling test
  immediately above it — the test's actual intent (a smoke test that output
  can be written to a file) did not depend on tiebreak order and is better
  served by giving the ranker a real signal.
- `tests/unit/test_prune.py::TestBudgetDropOrder::test_tied_rank_score_drops_the_oldest_first`
  reproduces the bug's exact shape and is confirmed to fail against the
  pre-fix code.
