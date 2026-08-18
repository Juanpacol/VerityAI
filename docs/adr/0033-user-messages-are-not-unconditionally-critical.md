# ADR-0033: User messages are not unconditionally critical

- **Status**: Accepted
- **Date**: 2026-08-18
- **Context**: found while running a synthetic Family-B-style pilot outside
  this repo (a fabricated ~9.8K-token session log, with one real fact stated
  once mid-log and a decoy stated near the end). `verity context --budget
  2500` reported "the remaining items are all critical and were not
  dropped" while the one fact the task actually needed was absent from the
  output entirely.

## The defect

`classify.py`'s rule cascade had:

```python
# 6. User messages carry intent, which is expensive to reconstruct.
if item.kind is ItemKind.USER_MESSAGE:
    return Relevance.CRITICAL, "user instruction"
```

Every user-role message, regardless of content, was CRITICAL — part of the
un-droppable floor `PruneResult.budget_met` guards. In the pilot fixture, 142
of ~150 user turns were one-line pointers ("Also take a look at
src/foo.py while we're at it"). All 142 were CRITICAL. The single
assistant reply carrying the actual root cause had no kind-based protection
at all — plain relevance rules apply to assistant content same as anything
else. The critical floor, built almost entirely out of these pointers, was
measured at 4,171 tokens; at any budget at or below that, ranking never got
a turn, because the floor itself already consumed the space, and the
informative reply — never part of the floor, never reached by ranking either
— was silently absent.

The over-budget message, "the remaining items are all critical and were not
dropped," was true of what remained and false about what mattered: nothing
in that sentence distinguishes "the important things are protected" from
"a pile of one-line pointers is protected." Same shape as
[ADR-0029](0029-unrankable-memory-is-not-irrelevant-memory.md)'s finding —
a collaborator returned less than the caller needed, and the message was
confidently wrong rather than absent.

## Decision

Remove the kind-based rule. A user message is now protected only by an
earlier, more precise rule already in the same cascade:

- rule 1 — an explicit marker (`decision:`, `must not`, `never`, ...)
- rule 1b — a financial figure
- rule 5 — recency (the most recent ~10% of turns, min 3, regardless of kind)

Anything else falls through to `RELEVANT` and is ranked like any other item
— the same treatment assistant content already received. No new tier, no
keyword list: the two rules already covering "this specific user turn
matters" (explicit markers, recency) are left to do that job; the blanket
kind rule that covered *every* user turn, mattering or not, is gone.

This was checked against `ContextRanker` (`rank.py`), which is entirely
kind-agnostic — it scores by lexical/semantic overlap with the task
regardless of `ItemKind`. Demoting user messages from CRITICAL to RELEVANT
does not remove them from the pipeline; it makes them compete on the same
terms as everything else, which is what should decide whether a short,
on-task question survives a tight budget — not its role.

## Consequences

- On the pilot fixture that surfaced this, `verity context --budget 2500`
  goes from reporting a false "nothing critical was dropped" while omitting
  the one needed fact, to actually meeting the 2,500-token budget (2,483
  tokens used) with the fact present in the output.
- `tests/unit/test_classify.py::test_old_generic_user_messages_are_not_automatically_critical`
  replaces the old "every user message is critical" contract.
  `test_recent_user_messages_stay_critical_via_recency` confirms protection
  is narrowed, not removed — an active, recent user turn is still pinned.
  `tests/unit/test_prune.py::test_generic_user_pointers_do_not_starve_the_real_answer`
  reproduces the bug's exact shape (many pointers, one substantive reply,
  a tight budget) and is confirmed to fail against the pre-fix rule.
- **Named trade-off, not hidden**: an old user message with genuinely
  irreplaceable intent, not recent and not carrying an explicit marker or a
  financial figure, is now only as protected as its rank score against the
  task — same as an assistant message in the same position. A user who
  wants an old instruction pinned regardless of phrasing should say so with
  an explicit marker (`decision:`, `constraint:`, ...), which this project
  already treats as authoritative over inference.
- This is the fourth finding sharing ADR-0029's shape: a collaborator (here,
  the classifier) returned a protected set that looked complete and wasn't,
  and the report describing it ("all critical, none dropped") was
  confidently wrong rather than visibly incomplete.
