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

## Finding 4 — the more sophisticated retrieval strategy lost, clearly

Building a hybrid retriever (BM25 lexical ranking + cosine semantic
similarity, fused with Reciprocal Rank Fusion) was the most architecturally
involved piece of this phase: a new module, a degradation ladder for when
embeddings aren't available, provenance tracking so every retrieved rule
carries its ranking method and score, a real embedding model
(`nomic-embed-text`) wired in after discovering `llama3.2` itself can't
serve embeddings on the Ollama version available (`/api/embed` returns
HTTP 501 for it, unconditionally — see `docs/adr/0003-hybrid-retrieval.md`
for the corrected story). All of that shipped, tested, and — confirmed by
a live spot-check — genuinely engages semantic scoring, not a silent
lexical-only fallback dressed up as hybrid.

Then the real three-arm A/B (`no_kg` vs. `legacy_kg` fetch-all vs.
`hybrid_kg`, 28 tasks against live `llama3.2`) ran, and the dumb arm won.
`legacy_kg` — no ranking, no embeddings, just "fetch every rule in two
hardcoded categories" — beat `hybrid_kg` on accuracy (83.3% vs. 63.2%),
recall (80.0% vs. 12.5%), and F1 (80.0% vs. 22.2%). `hybrid_kg`'s only win
was precision (100%, vs. legacy's 80%) — and it earned that by barely ever
committing to "this code has a bug" (1 correct flag out of 8 actual bugs),
not by being more discerning. Full numbers and reading in
`docs/PHASE_3_METHODOLOGY.md`'s "Real run #3".

The instinct after seeing that result was to look for what went wrong in
the *implementation* — a bug in the RRF fusion, a bad rule-corpus
selection, something to fix so hybrid "should have won." Nothing like that
turned up on inspection; the retrieval provenance for individual queries
looks correct, the rankings look sensible. The honest reading is simpler
and less comfortable: on this run, giving a 3B model more *semantically
relevant* context made it converge faster and commit to "pass" more
readily, without that confidence being earned on the cases that actually
had bugs — the same shape of trade-off Real run #2 found in the retry loop
itself (fewer abstentions, not obviously better). A more sophisticated
mechanism producing a worse outcome isn't a contradiction to explain away;
it's a real result, and it's now a tracked research question (not a
shipped default — `VERITYAI_RETRIEVAL_STRATEGY` stays `legacy`) instead of
an assumption.

## Finding 5 — a claim we'd already published turned out to be noise

Real run #2 reported, with a fairly confident tone, that the full
generate-verify-retry loop "shifts the error profile" relative to
single-shot verification — fewer abstentions, higher precision, lower
recall, lower accuracy. That framing survived unquestioned until a later
analysis pass (Fase 1 of a research roadmap, done purely by re-reading
already-collected JSON, no new generation) asked a sharper question: two
baselines each independently re-generate code at `temperature=0.7`, so
`ground_truth` is a property of *what got generated*, not a fixed label
per task. A task where two baselines happen to land on the same ground
truth isolates what the retry mechanism actually did with equivalently-
good-or-bad code from noise in what code got written in the first place.

That check surfaced a same-configuration control nobody had run before:
`verityai_full` from Real run #2 and `no_kg` from Real run #3 are the
*literal same configuration* — full retry loop, zero KG context — just
run on two different days. If the retry loop's effect were real and the
runs were reasonably stable, two runs of the same config should mostly
agree. They didn't: task-level status disagreement between the two
same-config runs (50%, ground-truth-controlled) was statistically
indistinguishable from the disagreement between single-shot and full-
retry (55%). The "trade-off" Real run #2 described with confidence
cannot currently be told apart from ordinary sampling variance across
independent runs — at n=28 with zero repetition, which is exactly what
was run.

This is a stronger case-study point than fixing a crash or a ground-truth
bug: those were mechanical defects with a definite fix. This is a
*conclusion*, already committed to a methodology doc, that was more
confident than the evidence behind it — and the fix isn't a code change,
it's a documented retraction plus a standing rule (also added to
`docs/PHASE_3_METHODOLOGY.md`): no future A/B or cross-model comparison
in this project attributes an accuracy/precision/recall difference to a
mechanism without first checking it against a same-configuration repeat.
Interestingly, the same method found one piece of the original story that
*does* survive: pairwise divergence between the three retrieval arms
(`no_kg`/`legacy_kg`/`hybrid_kg`, same day) is consistently *lower* than
the cross-day noise floor — meaning the KG-context effect, unlike the
retry-loop trade-off, is not just noise. Both the retraction and the
survival came from applying the identical check; neither was assumed.

## Finding 6 — a rule engine that had been sitting there, quietly unable to fail

Two KG rules — `SQL Injection Prevention` and `No Check-Then-Act Race` —
existed in the seed data since the project's earliest commits, each with
a `PRE`/`POST` formal spec describing exactly the pattern it should
catch. Neither had ever been wired to anything that reads real code:
they were retrieved into the LLM's prompt as guidance and never checked
independently. Building that check (Fase 5 of the research roadmap, T6)
meant writing two small AST-based fact extractors and feeding their
output into `RuleEngine.apply_rule_to_code` — the method that already
existed for exactly this purpose.

It returned `PASS` on the vulnerable sample. Not a crash, not an
exception — a clean, confident, wrong answer on code containing the
exact unguarded check-then-act race the rule's own precondition names.

The cause wasn't the new fact extractors; they were behaving correctly.
`apply_rule_to_code` is a forward-chaining *derivation* checker (the IBM
NSTK pattern this module cites in its own module docstring): precondition
met → derive the postcondition → report `PASS`. That's the right
behavior for deriving new facts from established ones. It is exactly the
wrong behavior for "does this code violate a rule," because the method
has no branch that returns `FAIL` — not a missing case that happens not
to trigger here, but structurally absent from the function for any
input. Feed it a rule whose precondition *is* the dangerous pattern, and
meeting that precondition is treated as license to derive the "safe"
postcondition and call it done. The second rule didn't even reach that
bug: its formal spec was prose (`PRE: user_input is untrusted`) rather
than a fact string, so it silently never fired at all — `UNKNOWN`,
matching nothing, for a different reason than intended but at least not
a false `PASS`.

The fix was small and additive on purpose: a new `check_for_violation`
method with the inverse framing (precondition present + postcondition
absent → `FAIL`; postcondition present → mitigated `PASS`; precondition
absent → `UNKNOWN`, never an affirmative "proven safe"), plus correcting
the SQL rule's formal spec to real fact-string syntax. `apply_rule_to_code`
itself was left untouched — nothing else in the codebase calls it, so
there was no reason to risk its behavior for a legitimate forward-chaining
caller that might exist later. The bug had been shippable-looking code
for as long as those two rules existed; it just had never been asked a
question it could get wrong until real facts from real code were run
through it.

## The pattern

None of these six findings came from writing more tests against the
existing fakes — they came from running the actual thing (or, in Finding
5's case, re-checking a real run's own numbers harder, and in Finding 6's,
finally connecting two pieces that had each looked fine in isolation) and
dealing honestly with what happened, including retracting a prior claim,
instead of defending it. That's the habit this case study is meant to
name: a comprehensive offline test suite is a floor, not a ceiling. It
proves the system does what the scripts told it to expect. It cannot
prove the system does the right thing when something *unscripted*
happens — and something unscripted is exactly what a real model, a real
database, or a real user (or a second run of the same experiment, or
finally wiring up a rule that had sat dormant since the beginning) will
always eventually do.
