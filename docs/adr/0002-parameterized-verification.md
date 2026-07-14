# ADR-0002 — Verifying asserts over function parameters

## Status

Accepted (Phase 3, following user request to close the gap flagged in
`docs/PHASE_2_REVIEW.md` and `docs/PHASE_3_METHODOLOGY.md`).

## Context

The AST→Z3 converter (`ast_to_smt.py`) never bound a function's own
parameters to Z3 variables. An `assert` referencing a bare parameter
always failed with "Variable X not defined," degrading the whole function
to `NOT_VERIFIED`. This blocked verifying realistic functions — every
Phase 2/3 benchmark had to work around it with locally-assigned constants
instead of parameters (see the "Function-parameter binding gap" entries in
both docs above).

The fix is not "just bind the parameter as a free Z3 variable." Binding it
with no constraint and reusing the existing satisfiability check
(`Z3Engine.check_satisfiable` — "does some assignment exist making all
constraints true?") would make **any** assert on an unconstrained
parameter trivially satisfiable: Z3 just picks a convenient witness value,
regardless of whether the function is actually correct for the values a
real caller could pass. That would be worse than today's `NOT_VERIFIED` —
it would report `PASS` on genuinely broken code.

## Decision

An assert that references a function parameter is checked for **validity**
(does it hold for *every* value the parameter could take), not
satisfiability — using `Z3Engine.verify_property`, which already existed
for exactly this purpose but wasn't wired into the main verification path
(`agent/orchestrator.py`'s module docstring flagged this as deferred
follow-up work).

Concretely:
- `ASTtoSMTConverter` binds each parameter as a free (unconstrained) SSA
  variable (`_bind_parameters`), defaulting to type `int` (no annotation
  parsing yet — same default the rest of the converter already uses when
  a type can't otherwise be inferred).
- Every `assert` is *additionally* recorded as a `(property, branch
  assumptions)` pair in `self.assert_properties`, alongside its existing
  contribution to `self.constraints` (unchanged, so every satisfiability-
  based test written before this ADR keeps passing byte-for-byte — see
  "Why the old path is untouched" below).
- A parallel `self.path_constraints` list accumulates only non-assert
  facts (assignments, `if`/`else` branch structure, loop bounds) — the
  legitimate background facts an assert can be checked against. Asserts
  are never included here, so proving assert A never accidentally assumes
  assert B is true.
- `symbolic/verify.py`'s `verify_python_snippet` routes to a new
  `_verify_parameterized` path whenever `converter.parameters` is
  non-empty: each assert is checked via `verify_property(property,
  assumptions=path_constraints + branch_assumptions)`, and the function's
  overall status is the worst result across all of them (`FAIL` >
  `TIMEOUT` > `UNKNOWN` > `PASS`, same precedence used elsewhere in this
  codebase, e.g. `agent/refinement.py`'s `IncrementalVerifier`).
- **Docstring `PRE:` specs are now wired in** (previously parsed but
  discarded — `_process_function`'s original docstring literally said "not
  yet wired into constraint generation"). A `PRE: <expr>` is parsed as a
  Python expression, converted the same way any other expression is, and
  added to `path_constraints` as an assumption. Without this, almost every
  reasonable guard clause (`assert denominator != 0` with no stated
  precondition) would report `FAIL`, since "no precondition" means Z3 must
  prove the assert for literally every integer — which most guard asserts
  don't, by design (that's *why* they're guards). `PRE:` lets generated
  code state the contract it assumes, and the check becomes "does this
  code's guard actually match its own stated contract" — a more useful
  question than "is this true unconditionally."
- `POST:`/`INV:` remain parsed but not wired (unchanged from before this
  ADR) — return-value postconditions need a way to name "the return
  value" in the expression grammar, which is separate follow-up scope, not
  bundled into this change.

## Why the old (no-parameter) path is untouched

For code with no free parameters, every variable is pinned to a concrete
value via an equality constraint from its assignment. In that case,
"does some satisfying assignment exist" and "does this hold for the one
possible value" coincide — the two approaches are mathematically
equivalent, not just "close enough." (Worked through by hand for several
Lote 1 benchmarks before implementing, then confirmed by the full existing
test suite passing unchanged after the change.) So rather than branching
the whole codebase into two verification strategies, `self.constraints`
and the satisfiability-based check stay byte-for-byte as they were; the
new `assert_properties`/`path_constraints`/`verify_property` machinery is
purely additive and only takes over when `converter.parameters` is
non-empty.

## Scope limits (explicit, not silent)

- Parameters always default to type `int`. Bool/float parameters, or
  parameters used in a way that implies a different type, are not
  specially inferred — a known narrowing, consistent with the rest of the
  converter's default-to-int behavior.
- `PRE:` parsing is best-effort: if the spec string isn't valid Python or
  uses a construct outside the verifiable subset, it's silently *not*
  added as an assumption (fails conservatively toward "prove more," not
  toward "silently accept less" — the same direction ADR-0001 already
  commits to).
- `*args`, `**kwargs`, positional-only, and keyword-only parameters are
  not bound (only `node.args.args`). A reference to one of them behaves as
  before this ADR (`NOT_VERIFIED` via "variable not defined").
- This does not attempt loop induction. A parameterized assert inside a
  `for` loop is checked against the loop's bounds constraint as an
  assumption, consistent with the rest of the converter's "one arbitrary
  iteration" abstraction (ADR-0001) — it is not a proof that the assert
  holds across every iteration in sequence, only that it holds for an
  arbitrary iteration satisfying the bounds.

## Related fix folded in: if/else phi-merging

Implementing and validating the above surfaced a second, **pre-existing**
bug, unrelated to parameters: when a variable is assigned in *both*
branches of an `if`/`else`, the converter had no phi-node merge. It let
`self.variables[name]` simply point at whichever branch was processed
*last* (`else`, always, since it's processed after `body`), silently
orphaning the other branch's binding with no constraint linking it to
anything downstream. An `assert` after such an `if`/`else` was checking
the *last-processed* branch's value, not "whichever branch actually ran" —
this happened to give correct answers in every benchmark written before
this ADR only because those benchmarks all happened to have the "true"
branch match the "last-processed" (`else`) branch. A parameterized test
case with the opposite branch true exposed it immediately.

Fixed the same day, in the same change, at the user's request rather than
deferring it: `_process_if` now snapshots `self.variables` before each
branch, restores it between them (so neither branch sees the other's
assignments), and — for every name assigned in either branch — binds a
fresh merged version via `new_var == If(test, then_value, else_value)`.
This is a real phi node, not a workaround. It's unconditionally true by
construction, so it's added directly to the constraint pool (both
`self.constraints` and `self.path_constraints`), not wrapped in `Implies`.

This fix applies regardless of whether the function has parameters — it
corrects the non-parameterized satisfiability path too. All Lote 1
benchmarks (`docs/PHASE_3_METHODOLOGY.md`) were re-run after this change
and produce identical PASS/FAIL verdicts to before, confirming the fix is
not a behavior change for the cases already validated — it only corrects
the previously-untested "true branch matches the branch processed first"
case.

Scope note: a variable assigned in *only one* branch, with no binding
before the `if`, still has no sound merge value for the path that never
defines it (real Python would raise `NameError` if that path were taken
and the name used later). That edge case falls back to "use whichever
branch's binding exists" — the same best-effort behavior as before this
fix, not a regression, just not newly solved either.

## Consequences

- Benchmarks and generated code can now use real function signatures
  (`def divide(a, b):` rather than hardcoded locals) and get a real
  verification verdict instead of automatic `NOT_VERIFIED`.
- Some code that previously silently degraded to `NOT_VERIFIED` will now
  report `FAIL` — specifically, any assert on a parameter with no matching
  `PRE:` that isn't actually a tautology. This is intended: it was always
  either right or wrong, `NOT_VERIFIED` was hiding that. Existing callers
  should expect this shift when they start writing parameterized code.
