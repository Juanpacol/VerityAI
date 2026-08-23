# ADR-0034: `dropped_critical` was dead code

- **Status**: Accepted
- **Date**: 2026-08-22
- **Context**: found by a self-audit pilot — VerityAI investigating its own
  `context/` and `memory/` engines for the same class of defect its own
  pilots had been probing for in other repositories (see
  [[project_pilot_findings_context_vs_speed]]).

## The defect

`PruneResult.dropped_critical` (`core/models.py:353`, `list[UUID]`) is named
directly in this project's own invariant 5 ("every degraded path says why")
as the field a caller reads when `budget_met` is `False`. But
`ContextPipeline.run` (`context/prune.py`) initialized it to `[]` and, in the
one branch meant to populate it, re-assigned the same empty list:

```python
if not budget_met:
    # ...the caller has to be told, in a field they must look at,
    # rather than discovering it from a number.
    dropped_critical = []
```

The comment describes the intended behavior; the code next to it does not
implement it. The field was permanently empty regardless of what actually
overflowed the budget — a caller reading only `dropped_critical`, as the
field's own name and invariant 5 instruct, saw no evidence anything was
over budget.

## Decision

Populate `dropped_critical` with the protected items still present in the
final item set when the budget is not met:

```python
dropped_critical = [item.id for item in current if item.is_protected]
```

This is the minimal fix: no new field, no new stage, no change to the
budget-enforcement algorithm itself (critical items were never eligible for
dropping, correctly — the bug was purely in what got reported afterward).

## Consequences

- `verity context --budget N` on a critical set larger than `N` now reports
  which items are responsible, not just that the budget was missed.
- `tests/unit/test_prune.py::TestCriticalItemsSurvive::test_dropped_critical_names_the_overflowing_items`
  reproduces the bug's exact shape and is confirmed to fail against the
  pre-fix code.
- Found alongside [[0035-enforce-budget-tiebreak-was-inverted]] and
  [[0036-supersede-never-deactivated-the-original]] in the same audit pass —
  none of the three were caught by the existing 668-test suite, because each
  is a "the code says one thing, the docstring/field name says another"
  defect rather than a missing-feature gap.
