# ADR-0036: `supersede()` never actually deactivated the superseded decision

- **Status**: Accepted
- **Date**: 2026-08-22
- **Context**: found in the same self-audit pass as
  [[0034-dropped-critical-was-dead-code]] and
  [[0035-enforce-budget-tiebreak-was-inverted]]. This one is the most
  consequential of the three — it spans two engines
  (`memory/` and `consistency/`) and silently defeated half of the
  Consistency Engine's purpose.

## The defect

`MemoryStore.supersede()`'s docstring (`memory/store.py:199-206`):

> Appends rather than rewriting: the old record keeps its `ACTIVE` status in
> the log and is reclassified on read by following the `supersedes` chain.

The implementation only ever did the first half:

```python
def supersede(self, decision_id: UUID, replacement: Decision) -> Decision:
    replacement.supersedes = decision_id
    return self.append(replacement)
```

No code anywhere read `supersedes` back. `decisions()` did a flat filter —
`[d for d in all_decisions if d.status is DecisionStatus.ACTIVE]` — with no
chain-walking at all. Repo-wide, `supersedes` was written in exactly one
place and read in none.

Consequences, traced through two consumers:
- `decisions()` kept returning **both** the original and its replacement as
  active, indefinitely — `summary()`'s decision count double-counted every
  superseded chain.
- `consistency/check.py::check_decision_resurfacing` filters
  `store.decisions(include_inactive=True)` for `status in (REJECTED,
  SUPERSEDED)` to catch an agent re-proposing something already settled.
  Since `status` never actually became `SUPERSEDED`, this check **silently
  never fired** for anything superseded via `supersede()` — only the
  `REJECTED` half of the check's stated purpose ever worked. An agent could
  re-propose a superseded decision and the harness built to catch exactly
  that would say nothing.

This is the shape [[0029-unrankable-memory-is-not-irrelevant-memory|ADR-0029]]
and [[0033-user-messages-are-not-unconditionally-critical|ADR-0033]] both
found before it: a component reports success ("decision recorded",
"resurfacing checked") while quietly doing less than its own docstring
promises, and nothing downstream had a reason to notice.

## Decision

Make `decisions()` honor its own docstring by walking the chain on read,
without rewriting the append-only log:

```python
all_decisions = self.read(Decision)
superseded_ids = {d.supersedes for d in all_decisions if d.supersedes is not None}
reclassified = [
    d.model_copy(update={"status": DecisionStatus.SUPERSEDED})
    if d.id in superseded_ids and d.status is DecisionStatus.ACTIVE
    else d
    for d in all_decisions
]
```

`supersede()` itself is unchanged — it was already doing the correct
append-only half of its job. The reclassification lives entirely in the read
path, where the docstring always said it would.

## Consequences

- `check_decision_resurfacing` now genuinely catches a re-proposal of a
  superseded decision, not just a rejected one — this was previously
  untested, because the only existing resurfacing tests used `REJECTED`
  fixtures.
- `summary()` and `handoff.py` now report a superseded decision's original
  as inactive, matching what a reader of either document would assume from
  the word "superseded."
- Two tests reproduce the bug's exact shape and are confirmed to fail
  against the pre-fix code:
  `tests/unit/test_memory.py::TestAppendOnly::test_superseded_decision_is_excluded_from_active`
  and
  `tests/unit/test_consistency_check.py::TestDecisionResurfacing::test_superseded_decision_is_flagged_like_a_rejected_one`
  — deliberately placed in both files, since the defect only shows its full
  effect at the point where `memory/` and `consistency/` meet.
