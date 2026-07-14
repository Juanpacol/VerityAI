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

## No live Ollama/llama2:13b run yet

Every baseline runner (`evaluation/baselines.py`) works against any object
exposing `.generate(prompt) -> str` — a live `OllamaClient` included — so
running this for real is a matter of pointing `llm_client_factory` at a
shared `OllamaClient` instance and calling `run_all_baselines()`. But no
live Ollama/llama2:13b instance is available in this session or in CI, so:

- All current tests use scripted fakes (`FakeLLMClient`,
  `SelfCorrectingFakeLLMClient`, `AlwaysBuggyFakeLLMClient`) with
  *deterministic, known* behavior. They validate that the harness — task
  loading, all 3 runners, metrics, report rendering — is wired correctly
  end-to-end (`tests/integration/test_evaluation_e2e.py`).
- **No real accuracy/precision/recall numbers for llama2:13b exist yet.**
  Any number in this repo comes from a simulated LLM whose behavior was
  scripted specifically to demonstrate the retry loop's recovery
  mechanism — it is not a claim about llama2:13b's actual bug rate.

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

1. Confirm the target threshold above (or set a different one) before the
   first live run.
2. Run the above against a real `llama2:13b` instance; record actual
   numbers in a dated results file (not this methodology doc, which
   should stay stable).
3. Scale benchmarks to Lote 2/3 (50+ correctness, 20+ security) — now
   unblocked by ADR-0002, using real parameterized function signatures
   instead of the locally-assigned-variable workaround Lote 1 needed.
4. Decide whether SQL injection / race-condition detection belongs in this
   evaluation framework at all, or whether it's better scoped as a
   separate `rule_engine`-based benchmark track (pattern matching, not Z3
   satisfiability).
5. Draft actual research findings only after step 2 produces real numbers
   — no fabricated or illustrative numbers belong in a findings write-up.
