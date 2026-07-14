# Case Study: What testing against a real model actually found

This is the short version of a longer pattern that repeated across this
project's later sessions: a green test suite is necessary, but it only
proves the wiring is correct — it says nothing about whether the system
behaves correctly against something it wasn't scripted to expect. Every
bug below was found by actually running the system, not by writing more
unit tests against a fake.

## The setup

By the end of Phase 4, VerityAI had 421 passing tests (unit + integration),
100% offline — every LLM call was a scripted `FakeLLMClient`, every Neo4j
call a fake driver. That's a deliberate, defensible engineering choice
(deterministic, fast, no external dependencies for CI). It is also, by
construction, blind to anything a *real* model or a *real* database does
differently from the script.

So the natural next step was to actually run it: bring up the Docker
Compose stack (Neo4j, Postgres, Redis, Ollama), pull a real model, load
the seed data for real, and run the evaluation harness against live output.

## Finding 1 — a crash the fake could never trigger

First real run, task 10 of 28: `Orchestrator.run()` crashed outright with
`SyntaxError: expected an indented block`, instead of returning the
graceful `status="failed"` response every other failure path returns.

Root cause: `SymbolicDebugger.__init__` called `ast.parse()` with no
exception handling, and `Orchestrator._build_response` always constructs
a `SymbolicDebugger` for the explanation text — even for a failed
attempt. `FakeLLMClient` never produces syntactically invalid Python, so
this path had 421 passing tests around it and had simply never been
exercised. A smaller, less reliable real model does produce malformed
code sometimes. Fixed by catching `SyntaxError` in the constructor and
degrading to "no line mapping available" — the same explicit-degradation
principle (ADR-0001) the rest of the verifier already follows, just
missed in one constructor.

## Finding 2 — the ground-truth mechanism didn't survive contact with a real model

The Phase 3 evaluation harness classified generated code by comparing it
*verbatim* against a fixed reference string. Against a live model, this
hit **100% "novel"** (matched neither known string) — not most of the
time, all of the time. No model reproduces a reference solution's exact
text, variable names, or formatting. Every accuracy/precision/recall
number computed from that run was mathematically well-defined and
completely uninformative.

The fix (an execution-based oracle: run the candidate function against
concrete inputs in an isolated subprocess, compare behavior instead of
text) surfaced two *more* real bugs the instant it touched a live model:

- **Stdout corruption**: LLM output commonly includes a demonstrative
  `print(...)`. Since the candidate ran via `exec()` inside the same
  subprocess reporting its own JSON result on stdout, that print
  corrupted the single-line JSON the parent expected — silently
  re-creating the "novel" problem from a different angle. Fixed by
  redirecting the candidate's stdout during execution.
- **Ambiguous function naming**: benchmark prompts never told the model
  what to name the function it wrote. A live model is free to choose,
  and often chose something reasonable but different from what the
  oracle's exact-name lookup expected. Fixed by making every prompt state
  the required name explicitly.

Three bugs in one mechanism, found in the time it took to run it once
against a real model, none of them visible in 400+ passing offline tests.

## Finding 3 — a verification gap that would have quietly lied

Writing the verification-scope reference doc (`docs/VERIFICATION_SCOPE.md`)
meant re-running every documented example against the real verifier
instead of describing it from memory. One example — a bare recursive
`return n * f(n-1)` with no other assert — reported **`pass`**, not
`NOT_VERIFIED`.

Root cause: the AST→Z3 converter's `Return` handling never inspected the
returned expression at all — it just skipped past it. A non-verifiable
construct hidden inside a bare `return` (recursion, unsupported operators)
was invisible to the "mark this unverifiable" bookkeeping. With nothing
marked and nothing constrained, the verifier's "no constraints recorded"
branch read that as a clean pass — the exact failure mode the project's
own founding design decision (ADR-0001) explicitly rules out: *"marca
como no verificado, nunca como verificación fallida — evita que el
sistema mienta sobre su propio alcance."* This was the system quietly
lying about its own scope, in the one place nobody had thought to check
by hand.

Fixed by inspecting (not constraining) the return expression. This also
retroactively exposed a test fixture elsewhere in the suite that had been
using true division (`/`, never supported — only `//` is) inside a bare
return and had been unknowingly relying on the same gap to report a
false "consistent."

## The pattern

None of these three findings came from writing more tests against the
existing fakes — they came from running the actual thing and checking
what happened, then fixing the root cause instead of the symptom. That's
the habit this case study is meant to name: a comprehensive offline test
suite is a floor, not a ceiling. It proves the system does what the
scripts told it to expect. It cannot prove the system does the right
thing when something *unscripted* happens — and something unscripted is
exactly what a real model, a real database, or a real user will always
eventually do.
