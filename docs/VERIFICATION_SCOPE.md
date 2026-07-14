# Verification Scope — what "verified" actually means

This is the single reference for what VerityAI's Z3-based verifier can and
cannot check, consolidating [ADR-0001](adr/0001-verifiable-python-subset.md)
(the original scope decision) and [ADR-0002](adr/0002-parameterized-verification.md)
(parameterized functions), with concrete examples validated against the
real verifier (`symbolic/verify.py::verify_python_snippet`) as of this
writing — not reasoned about from memory. If you change
`symbolic/ast_to_smt.py`, re-run the examples below before trusting this
doc again; a prior gap (see "A real gap this scope work found," below) was
found by doing exactly that.

**Why this document exists**: "VerityAI formally verifies code" is a
claim that needs a precise boundary, or it's marketing, not engineering.
The scope is deliberately narrow (ADR-0001) — narrower scope, correctly
enforced, beats broad scope with silent holes.

## What DOES verify

| Construct | Example | Result |
|---|---|---|
| `int`/`bool`/`float` locals | `x = True; assert x == True` | `pass` |
| Arithmetic (`+ - * // %`) | `x = 7; assert x % 2 == 1` | `pass` |
| Comparisons, `and`/`or`/`not` | `assert (x > 0) or (y > 0)` | `pass` |
| `if`/`else`, including phi-merged variables | `if a>b: r=a else: r=b; assert r>=a` | `pass` |
| Bounded `for i in range(...)` | `for i in range(n): assert i < n` | `pass` |
| Function parameters (ADR-0002) | `def f(x): assert x == x` | `pass` (proven for *every* `x`) |
| Docstring `PRE:` as an assumption | `"""PRE: d != 0"""` + `assert d != 0` | `pass` (guard matches its contract) |
| Builtins `len, range, abs, min, max, int, bool` | `n = len(arr); assert n > 0` | `pass` (`len(arr)` is a symbolic length, `arr` itself need not exist) |
| `assert` statements | any of the above | the actual check target |

Verified empirically, in order, by running each snippet through
`verify_python_snippet` directly.

## What does NOT verify (degrades to `NOT_VERIFIED`, never a silent `pass`)

| Construct | Example | Result | Why |
|---|---|---|---|
| Strings | `s = "hello"; return s` | `not_verified` | No string theory in the converter |
| Lists/dicts as real values | `arr = [1, 2, 3]` | `not_verified` | Only `len()`'s symbolic-length trick is supported, not real list construction |
| `while` loops | `while x < 10: x += 1` | `not_verified` | No loop induction (ADR-0001) |
| `for x in <list>` (non-`range`) | `for x in [1,2,3]: ...` | `not_verified` | Only `range(...)` iteration is bound |
| Recursion | `return n * f(n-1)` | `not_verified` | No call-graph resolution or induction |
| `try`/`except` | any | `not_verified` | No exception-flow modeling |
| `raise` | `raise ValueError(...)` | `not_verified` | Same |
| Method calls | `s.append(4)` | `not_verified` | Only bare calls to the builtin allowlist are supported |
| True division `/` | `return a / b` | `not_verified` | Only `//` (`FloorDiv`) is supported |
| Bounded loops: correctness *across* iterations | `for i in range(n): total += i` | processed, but only proves "some one iteration," not the loop's cumulative effect | No invariant inference (ADR-0001) — this is a documented soundness simplification, not a crash |

Every one of these was re-run against the real verifier while writing
this document, not asserted from memory.

## A real gap this scope work found (and fixed)

While validating the table above, `return n * f(n - 1)` (a bare recursive
call with no other assert in the function) reported **`pass`**, not
`not_verified`. Root cause: `_process_statements`' `ast.Return` handling
`continue`d past the statement without ever calling `_convert_expr` on its
value — so a non-verifiable expression hidden inside a bare `return`
(recursion, true division, a method call) was invisible to
`non_verifiable` tracking. With zero constraints and zero non-verifiable
nodes recorded, `verify_python_snippet`'s "no constraints" branch read
that as `pass` — the exact failure mode ADR-0001 says must never happen
("marca como 'no verificado', nunca como 'verificación fallida' — evita
que el sistema mienta sobre su propio alcance"). Fixed by inspecting (not
constraining) the return expression; regression tests in
`tests/unit/test_ast_extended.py::TestASTConverterReturnValueInspection`.
This also retroactively invalidated one existing test fixture
(`tests/integration/test_continuous_learning_e2e.py`) that used `/`
instead of `//` and had been unknowingly relying on the same gap to
report `CONSISTENT` — fixed alongside it.

## Practical implication for benchmark design

Every task in `evaluation/benchmarks/*.json` is constrained to this
scope, which is why they use hardcoded/parameterized `int` arithmetic and
`if`/`else` rather than realistic data structures. Two benchmark tasks
(`security_004_safe_divide_param`, `security_006_check_auth_before_action`)
encode a bug that is *only* observable to the Z3 verifier (a missing
`PRE:` docstring makes a guard unprovable) and not to runtime execution —
see their `note` fields and `docs/PHASE_3_METHODOLOGY.md`'s discussion of
the execution oracle for why those two specifically have no `test_cases`.

## Where this could expand next (not started)

The highest-value expansion is Z3's Array theory for real list/index
operations (today only `len()`'s symbolic trick exists) — this would move
"bounds check on a real list" from `not_verified` to actually checked.
That's a new ADR and a real chunk of work, not a quick add; tracked as
future scope, not attempted in this pass.
