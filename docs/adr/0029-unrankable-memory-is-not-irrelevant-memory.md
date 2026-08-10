# ADR-0029: Unrankable memory is not irrelevant memory

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: found while writing an MCP test for `should_recall_memory`.
  A saved hard constraint — "must not add a Redis dependency" — was not
  surfaced for the task "rate limiting", and the report said it had been
  "ranked below the budget cut". The budget was 19,200 tokens and the record
  was 12. It had not lost a budget contest; it had never entered one.

## The defect

`ContextRanker.rank` returns only the candidates it could score. A candidate
sharing no term with the task is **absent from the result**, not present with
score zero:

```
ContextRanker().rank("rate limiting", [redis_constraint, token_bucket_decision])
  -> 1 item:  score=0.0164  "decision: use a token bucket for rate limiting"
     (the Redis constraint is simply not there)
```

`select()` iterated `ranking.items` and applied the budget to that. So any
record with no lexical overlap with the task was dropped **before** the budget
was consulted, and then reported as a budget outcome. Measured on a real
`.verity/`: task "speed up the cache" surfaced **0 of 4** records, all four
silently discarded.

Three things make this worse than a ranking imperfection:

1. **It removes exactly the records worth recalling.** The value of persisted
   memory is highest when the decisive fact is *not* inferable from the task's
   wording. That is [ADR-0020](0020-arbitrary-tiebreak-pilot.md)'s finding —
   the first pilot in eight to produce a success-rate split did so precisely
   because the correct answer had no linguistic convention pointing at it.
   Selecting memory by lexical overlap optimizes against the one case the
   mechanism exists for.
2. **`classify.py` protects MEMORY items as CRITICAL unconditionally**, and
   the prune pipeline honours that. But nothing that never reaches the
   pipeline can be protected by it. The guarantee was real and simply
   unreachable.
3. **The explanation was wrong, not merely missing.** "Ranked below the budget
   cut" tells a reader to raise the budget. Raising it would have changed
   nothing.

## Decision

Unscored candidates are ordered **last**, not dropped:

```python
scored_ids = {scored.item.id for scored in ranking.items}
ordered = [scored.item for scored in ranking.items]
unscored = [item for item in candidates if item.id not in scored_ids]
ordered.extend(unscored)
```

Relevance still orders the budget; it can no longer make a record vanish
before the budget applies. This is invariant 6's principle — *the parts must
sum to the whole* — applied to selection rather than to parsing: every
candidate is now either selected or genuinely did not fit, and
`test_nothing_is_lost_between_candidates_and_the_budget` asserts it.

When any candidate was unrankable, `degraded_reason` says so and says what
was done about it, distinct from the ranker's own degradation:

```
degraded  no embed_fn configured; 4 candidate(s) share no term with the task
          and could not be ranked; they were ordered last rather than dropped,
          so a constraint with no lexical overlap can still be surfaced
```

Keyed on `ContextItem.id`, not `content_hash`: the hash is populated by
whoever built the item and is empty on a plain one, which would make every
unscored candidate look like a duplicate of every other. (Found by a test
failing for that exact reason.)

## Consequences

- On the real `.verity/` used to check this, task "speed up the cache" goes
  from 0 of 4 records surfaced to 4 of 4, with the reason stated.
- Budgets now bind more often, which is the correct pressure: the point of
  `plan_budget`'s conservative 15% default is to bound cost, and it can only
  do that against candidates that actually reach it.
- **Not addressed here:** ordering *among* unscored candidates is their
  original order, which is arbitrary with respect to importance. A hard
  constraint and a stale discovery compete on nothing but position. Ranking
  memory by kind and recency rather than by term overlap is the real fix, and
  it needs a pilot to justify a specific ordering rather than a guess — the
  same reason ADR-0025's thresholds are still placeholders.
- Embeddings would reduce the incidence (a semantic ranker scores everything)
  but not the defect: `rank()` would still be free to return a short list, and
  `select()` must not assume otherwise.

## The pattern, now three for three

This is the third finding in one verification pass with the same shape, and at
this point it is worth naming as a class rather than three coincidences:

| | What was silently returned | What the caller reported |
|---|---|---|
| [0027](0027-retained-trial-evidence.md) | a hash of a tree, not the tree | "retained, re-derivable" |
| [0028](0028-the-mocked-test-that-could-not-fail.md) | zero graph nodes, wrong path form | `low` risk — "needs no scrutiny" |
| 0029 | fewer items than were passed in | "ranked below the budget cut" |

In each case a collaborator returned less than the caller assumed, the caller
had no way to distinguish "nothing found" from "nothing looked at", and the
resulting message was **confidently wrong** rather than absent. The check that
would have caught all three is the same one: *when a function returns fewer
things than it was given, does anything assert the difference is accounted
for?* That question is now a test in all three places.
