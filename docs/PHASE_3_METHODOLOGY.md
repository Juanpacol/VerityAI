# Phase 3 Methodology — Evaluation Framework

## Overview

Phase 3 adapts TruthfulQA's paired-example methodology to code verification:
instead of paired truthful/untruthful answers, each benchmark task pairs a
`reference_solution` (correct) with a `known_buggy_variant` (an intentional,
specific bug). Three configurations are compared on the same task set:

1. **`raw_llm`** — LLM output trusted as-is, no verification at all.
2. **`single_shot_z3`** — LLM generates once, Z3 checks once, no retry.
3. **`verityai_full`** — the full Phase 2 generate→verify→retry loop.

`research/truthfulqa/` holds the original repo (gitignored, reference-only)
for anyone adapting its dataset-construction conventions further.

## Ground truth: how it's actually decided

Z3 can't judge whether arbitrary LLM output is "correct" against some
unstated intent — it can only check whether code's own embedded `assert`
statements are internally satisfiable (this is the same MVP scope
documented in `agent/orchestrator.py` and ADR-0001). So every benchmark
task is built so that:

- `reference_solution` verifies **PASS** (its asserts are satisfiable).
- `known_buggy_variant` verifies **FAIL** (its asserts are self-contradictory,
  because the bug is a wrong operator/constant/comparison that breaks the
  assert, not a semantic bug invisible to a satisfiability check).

Every pair in `evaluation/benchmarks/*.json` was validated against
`symbolic.verify.verify_python_snippet` directly (bypassing the LLM
entirely) before being committed to the dataset — this confirms the
ground-truth labels are correct independent of any model's behavior.

**Ground truth for a *baseline outcome*** is not pre-assigned — it's
derived after generation by comparing the baseline's final code, verbatim,
against `reference_solution` / `known_buggy_variant`
(`baselines.classify_ground_truth`). A real LLM won't reliably reproduce
either fixed string, so a third bucket, `"novel"`, captures output that
matches neither — it's excluded from the confusion matrix (see
`metrics.compute_classification_metrics`) rather than silently miscounted
as right or wrong.

## What "verifiable" constrains the benchmark to

Per ADR-0001, the AST→Z3 converter only handles a subset of Python: linear
code, `if`/`else`, bounded `for` loops (as "some iteration", not full
induction — see `ast_to_smt.py`'s `_process_for` docstring), `int`/`bool`/
`float`, and a small builtin allowlist (`len`, `range`, `abs`, `min`, `max`,
`int`, `bool`). Two things that shaped every benchmark snippet as a result:

- **Function parameters — fixed after Lote 1 was written.** The converter
  originally never bound a function's own parameters to Z3 variables, so
  every Lote 1 snippet above uses locally-assigned variables instead of
  real parameters as a workaround. This is now fixed (see
  `docs/adr/0002-parameterized-verification.md`): parameters bind as free
  Z3 variables and asserts referencing them are checked for validity, with
  docstring `PRE:` specs wired in as assumptions. Lote 1's existing
  snippets were re-validated against the fix and are unchanged; Lote 2/3
  can now use real function signatures.
- **No real array/list construction.** List literals (`ast.List`) aren't
  supported; the `security_002_bounds_check` task represents an array only
  through `len(arr)`'s symbolic-length trick (see the `note` field on that
  task) — it is a verification target, not standalone-executable Python.

## Lote 1 + Lote 2 — what's in scope vs. deferred

Lote 1 (12 correctness + 3 security pairs) used locally-assigned variables
as a workaround for the parameter-binding gap that existed at the time.
Lote 2 (10 correctness + 3 security pairs, added after ADR-0002) uses real
parameterized function signatures — `max_of_two(a, b)`, `safe_divide(numerator,
denominator)`, etc. — now that asserts over parameters verify correctly.
Every task's `reference_solution`/`known_buggy_variant` pair is checked
against the real verifier on every test run, not just re-validated by hand
when written (see `tests/integration/test_benchmark_ground_truth.py`).

| | Delivered (Lote 1 + 2) | Plan target | Deferred |
|---|---|---|---|
| Correctness benchmarks | 22 pairs (44 snippets): 12 Lote 1 (local vars) + 10 Lote 2 (real parameters, `if`/`else` phi-merge, docstring `PRE:`) | 50+ (LeetCode-style) | Lote 3: loops with parameterized bounds, recursion-adjacent patterns (recursion itself stays out of ADR-0001's verifiable subset) |
| Security benchmarks | 6 pairs: division-by-zero guard, bounds check, lock-guard proxy (Lote 1); parameterized division guard, parameterized bounds check, authorization guard (Lote 2) | SQL injection, buffer overflow, null deref, race conditions | SQL injection and true race conditions are **out of scope for this Z3-based verifier** — it has no SQL/string semantics and no concurrency model. Catching those needs pattern-based `rule_engine` checks (deductive matching, not Z3 satisfiability), tracked as follow-up, not attempted here to avoid mislabeling ground truth for properties the system can't actually check. |

This mirrors Phase 1's "walking skeleton then scale in lotes with
regression testing" approach rather than writing all 100+100/50+ snippets
before the harness that consumes them was proven to work.

## Real run #1 (2026-07-13, against llama3.2, not llama2:13b)

A live Neo4j + Ollama stack was brought up (`docker compose up`, reusing
containers from a prior session) and the full Lote 1+2 set (22
correctness + 6 security = 28 tasks) was run for real against all 3
baselines. **Model substitution**: `llama2:13b` was never pulled —
the sandboxed environment has no outbound network access from within
Docker's network namespace (DNS resolution to `registry.ollama.ai` fails),
so the model can't be downloaded. `llama3.2` (3B parameters) was already
present in the `ollama_data` volume from earlier host-side work and was
used instead. Every number below is about `llama3.2`, not `llama2:13b` —
treat this as a first real data point on the harness, not a claim about
the model the plan named.

Total wall time: 39.8 minutes for 28 × 3 = 84 generate/verify calls.

**Finding 1 — the ground-truth mechanism doesn't work against a live model.**
`classify_ground_truth` compares generated code *verbatim* against
`reference_solution`/`known_buggy_variant`. Across all 84 calls, the
result was `"novel"` **100% of the time** — a live model never reproduces
either fixed string exactly (different variable choices, formatting,
comments). This was anticipated in this doc's design section, but seeing
it hit 100% rather than "most of the time" is worth stating plainly:
**accuracy/precision/recall are computed over zero judged cases and are
meaningless for this run.** `compute_classification_metrics` correctly
reports 0% across the board rather than fabricating a number — that's
working as designed, not a bug — but it means exact-string ground truth
cannot evaluate a live model at all, only a scripted fake with known
output. Fixing this needs an independent oracle (e.g. actually *executing*
generated code against the task's test cases) — tracked as follow-up, not
attempted in this session.

**Finding 2 — the retry loop did not outperform single-shot in this run,
and may have done worse.** Looking at verification status (not
ground-truth) instead:

| Baseline | pass | fail | not_verified | avg confidence | avg latency |
|---|---|---|---|---|---|
| Single-shot Z3 (no retry) | 7/28 (25.0%) | 8/28 (28.6%) | 13/28 (46.4%) | 0.42 | 6.7s |
| VerityAI full retry loop | 4/27 (14.8%) | 7/27 (25.9%) | 16/27 (59.3%) | 0.21 | 70.2s |
| (Raw LLM: always reports "pass" by construction — not a real signal) | | | | | |

(27, not 28, for the retry loop — one task hit a real bug, see Finding 3,
and is excluded from this table rather than silently miscounted.)

The retry loop passed *fewer* attempts, not more, at 10× the latency cost.
Plausible confounds, none confirmed: (a) the retry loop's prompt injects
KG rule context that single-shot doesn't get, which may add noise a 3B
model handles poorly; (b) the "previous attempt failed, here's why" retry
prompt may confuse a smaller model rather than helping it, unlike the
scripted `SelfCorrectingFakeLLMClient` tests assume; (c) n=28 on a single
run with no repetition is a small, noisy sample. **This result should not
be read as "the architecture doesn't work"** — it's a signal that the
retry mechanism's value may depend on model capability in ways not yet
isolated, worth a dedicated follow-up (compare retry-with-KG-context vs.
retry-without, across model sizes) before drawing a real conclusion.

**Finding 3 — a real crash bug, found and fixed in this session.** One
task (`correctness_010_check_even`) crashed `Orchestrator.run()` entirely
with `SyntaxError: expected an indented block` instead of returning a
graceful `status="failed"` response. Root cause: `SymbolicDebugger.__init__`
called `ast.parse()` with no exception handling, and
`Orchestrator._build_response` always constructs a `SymbolicDebugger` for
the explanation text — even for a failed attempt. A `FakeLLMClient` never
generates syntactically invalid Python, so this path was never exercised
before a real (smaller, less reliable) model actually produced malformed
code. Fixed by catching `SyntaxError` in the constructor and degrading to
"no line mapping available" rather than crashing (see
`tests/unit/test_debugger.py`'s regression test). This is exactly the
kind of gap a live run finds and a scripted-fake-only test suite cannot.

**What this run does NOT tell us**: whether VerityAI meets the confirmed
50% threshold. That metric is defined in terms of `buggy_accept_rate`,
which depends on knowing whether output is actually buggy — which
Finding 1 says this harness can't currently determine for live-model
output. The threshold question remains open until the ground-truth gap
is closed.

Raw data: `real_run_results.json`, `real_run_report.md`,
`real_run_dashboard.html` in the session scratchpad (not committed to the
repo — this is one exploratory run, not a tracked benchmark result).

## Target threshold (confirmed before a real run, per the plan's hardened
acceptance criterion)

The plan requires fixing a target *before* running the real comparison,
not concluding "we did the comparison" after the fact.

> **Confirmed threshold**: VerityAI's full retry loop must reduce the rate
> of finally-accepted buggy code by **at least 50%** relative to `raw_llm`,
> measured as `1 - (buggy_accept_rate_full / buggy_accept_rate_raw)`,
> across the full Lote 1+2+3 correctness + security benchmark set (target:
> 50+ correctness, 20+ security tasks).

Provenance: 50% was proposed as a round, illustrative number (not derived
from an external benchmark or prior study) and confirmed by the project
owner as a product-level target — a number that's both meaningful to an
enterprise buyer and plausibly achievable for an MVP, per the business
framing in `CLAUDE.md` ("empresas no confían en código generado por IA
porque no pueden ver por qué es correcto"). It is not a technical
derivation; treat it as a business decision, not a scientific one, and
revisit it if real Lote 1+ results make it clearly too easy or too hard
to be a meaningful bar.

## Dashboard

`evaluation/dashboard.py` renders a self-contained HTML dashboard (grouped
bar chart for accuracy/precision/recall/F1, a separate latency chart, a
legend, and a full data table) from the same `results` dict the markdown
`report.py` consumes. It requires a `data_source_note` argument — a
caption stating where the numbers came from — specifically so a dashboard
built from simulated data can never be mistaken for a real benchmark run
just because the charts look finished.

## How to run this for real

```python
from verityai.evaluation.baselines import load_benchmark_tasks, run_all_baselines
from verityai.evaluation.report import render_comparison_report
from verityai.neural.ollama_client import OllamaClient

client = OllamaClient(model="llama2:13b")
tasks = load_benchmark_tasks("src/verityai/evaluation/benchmarks/correctness_benchmarks.json")
tasks += load_benchmark_tasks("src/verityai/evaluation/benchmarks/security_benchmarks.json")

results = run_all_baselines(lambda task, baseline: client, tasks)
print(render_comparison_report(results))
```

## Next steps

Updated after Real run #1 (above) — steps 1-2 from the original plan are
now done; what's left is harder and more important than just "run it":

1. **Close the ground-truth gap (Finding 1).** Replace/augment
   `classify_ground_truth`'s exact-string match with an independent
   oracle — most plausibly, actually executing generated code against
   per-task test cases (inputs → expected outputs) rather than comparing
   source text. Without this, accuracy/precision/recall cannot be
   computed for any live model, ever, regardless of which model is used.
2. **Investigate the retry-loop underperformance (Finding 2)** before
   trusting or dismissing it: rerun with retry-loop KG-context injection
   disabled (isolate that variable), rerun with more repetitions per task
   to see if it's noise, and if possible compare against a larger model
   than llama3.2:3B once one is available.
3. Get `llama2:13b` actually available — either restore outbound network
   access to the Docker network so it can be pulled, or pull it host-side
   first and mount/import it into the `ollama_data` volume some other way.
   Until then, every number in this repo is about substitute models
   (`llama3.2`), not the model the plan names.
4. Scale benchmarks to Lote 3 (50+ correctness, 20+ security) — unblocked
   by ADR-0002 for correctness; security additions still need the
   ground-truth fix (step 1) to be worth adding, or they'll just add more
   unjudgeable "novel" cases.
5. Decide whether SQL injection / race-condition detection belongs in this
   evaluation framework at all, or whether it's better scoped as a
   separate `rule_engine`-based benchmark track (pattern matching, not Z3
   satisfiability).
6. Confirm or revise the 50% target threshold once step 1 makes it
   computable at all — right now there's no way to check it either way.
7. Draft actual research findings only after the above — a findings
   write-up needs a working ground-truth oracle, not just "we ran it."
