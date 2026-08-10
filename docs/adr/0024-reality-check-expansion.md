# ADR-0024: Agent Reality Check — closing four extraction/checking gaps

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: A survey of `consistency/claims.py` and `consistency/check.py`,
  done to scope the "Agent Reality Check" proposal, found the proposal's own
  motivating example — "AuthService handles the tokens" — was
  unextractable: claim extraction requires backticks, and the closed
  relation set was exactly three phrases mapped to two edge kinds (`calls`,
  `inherits from`, `extends`). Four specific, bounded gaps were worth
  closing without repeating ADR-0021's mistake (a checker asserting more
  than the graph can actually support).

## Decision

Four additions, each deliberately narrow:

1. **`imports` as a checkable relation.** Added to `claims.py`'s `_RELATIONS`
   and given its own checker, `check_file_imports`. Unlike `calls` on a file
   target (ADR-0021's finding), an `IMPORTS` edge is both necessary *and*
   sufficient evidence for an `imports` claim — there is no inference gap,
   because `imports` *is* the claim, not a proxy standing in for a
   different one. Restricted to file-to-file claims; a non-file subject
   ("`apply_tax` imports `X`") returns `UNVERIFIABLE` rather than a guess
   about what that would even mean.
2. **Negation.** `"`X` does not call `Y`"` previously matched nothing (the
   regex had no polarity) or worse, could silently match the substring
   `"calls"` inside a negated sentence. A closed, explicit
   `_NEGATED_RELATIONS` map (`"does not call"`, `"never calls"`, etc. — one
   entry per relation, not a generic prefix bolted onto the affirmative
   list, since English negation does not transform mechanically) is tried
   first, and `Claim.negated` flows through to a single `_negate()` wrapper
   in `check.py` that inverts `SUPPORTED`/`CONTRADICTED` and leaves
   `UNVERIFIABLE` alone — an edge the ingester could not resolve (ADR-0006)
   is exactly as ambiguous under negation as under the affirmative claim.
3. **Multi-target relations.** `"`X` calls `Y` and `Z`"` previously bound
   only `Y`. `_extra_targets` now collects `and \`target\`` continuations
   after a relation match and emits one claim per target, so both are
   checked.
4. **Constraints as evidence.** `consistency -> memory` was already a
   declared dependency (`DEFAULT_POLICY`) and already exercised — but only
   for `store.decisions()`. `Constraint` records, built specifically to say
   "the solution must respect this, whatever else changes," were never
   consulted. `check_constraint_violations` reuses the exact heuristic
   shape and honesty discipline `check_decision_resurfacing` already
   established (BM25 lexical overlap, normalized against the checked
   text's own best-possible score — the same fix ADR-0018 made for
   decisions — confidence capped below 1.0 always). Only `hard` constraints
   are checked; a soft constraint's violation is a quality judgment, not a
   contradiction this mechanism is positioned to flag.

## What was deliberately not added

Responsibility-attribution relations — "handles," "owns," "manages,"
"is responsible for" — are still unrecognized, on purpose. There is no
graph edge that adjudicates "does `AuthService` handle the tokens" the way
an `IMPORTS` or `CALLS` edge adjudicates a call or import claim. Mapping
these to the nearest existing edge kind would repeat ADR-0021's mistake at
larger scale: a checker asserting a verdict the graph cannot actually
support. The proposal's own motivating example is not extractable under
this ADR either — closing that gap needs a real edge kind for
responsibility, which is future work, not a regex addition.

## Consequences

- `check_symbol_relation`'s dispatch now has three branches (imports, a
  file-targeted `calls`, and the original symbol-to-symbol lookup, factored
  into `_check_defined_relation`), with negation applied uniformly across
  all three via one wrapper rather than duplicated per branch.
- `Claim` gains a `negated: bool = False` field (`core/models.py`) — no
  other model changed.
- New tests: `TestFileImportsRelation`, `TestNegatedRelation`,
  `TestMultiTargetRelation`, `TestConstraintViolations` in
  `tests/unit/test_consistency_check.py`, plus extraction-level coverage in
  the negation/multi-target tests. 551 tests pass total.
- Consistent with `claims.py`'s stated philosophy throughout: a claim shape
  not covered here is simply not extracted, never forced into the nearest
  recognized shape and checked wrongly.
