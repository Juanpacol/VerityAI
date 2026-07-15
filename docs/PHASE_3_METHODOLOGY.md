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

## Real run #2 (2026-07-13, execution-oracle ground truth, cross-model)

Real run #1's Finding 1 (exact-string ground truth reports 100% "novel"
against any live model) was closed by replacing `classify_ground_truth`
with an execution-based oracle (`evaluation/execution_oracle.py`): the
candidate function is run against per-task `test_cases` in an isolated
subprocess, and correctness is judged by *behavior*, not source text. This
run is the first time that oracle was pointed at real models, and it was
designed from the start as a **cross-model** run — `llama3.2` (3B) and
`qwen3:8b` (8B), both already present locally, specifically to test
whether the architecture's results generalize past one model rather than
being tuned to it. Raw data: `docs/results/2026-07-13_cross_model_run.json`.

**`llama3.2` — complete run, 28/28 tasks × 3 baselines, real numbers:**

| Baseline | accuracy | precision | recall | novel_rate | abstention_rate | avg latency | avg attempts |
|---|---|---|---|---|---|---|---|
| `raw_llm` | 50.0% | 0.0% | 0.0% | 14.3% | 0.0% | 6.5s | 1.00 |
| `single_shot_z3` | 66.7% | 25.0% | 100.0% | 17.9% | 50.0% | 5.7s | 1.00 |
| `verityai_full` (retry loop) | 63.6% | 50.0% | 75.0% | 35.7% | 25.0% | 70.4s | 2.75 |

Reading this honestly, not selectively:

- **The ground-truth gap is closed.** `novel_rate` dropped from 100% (Real
  run #1) to 14-36% depending on baseline — the oracle judges most output
  by actual behavior now. The remaining novel cases are legitimate
  (unparseable code, wrong function name, code the security scanner
  refuses to execute), not a broken mechanism.
- **`raw_llm`'s 0% precision/recall is expected, not a bug**: with no
  verification, `raw_llm` never predicts "buggy" — it has nothing to
  compare against, so precision/recall over the "buggy" class are
  undefined-by-construction 0%. Its 50% accuracy is really "half of tasks'
  reference implementations happened to be easy enough that llama3.2 wrote
  correct code anyway," which says nothing about bug-catching.
- **The retry loop trades accuracy for precision, and that trade is not
  obviously a win.** `verityai_full` has *lower* raw accuracy than
  `single_shot_z3` (63.6% vs 66.7%) but *higher* precision (50% vs 25%),
  *lower* recall (75% vs 100%), and lower abstention (25% vs 50%). In
  plain terms: `single_shot_z3` abstains
  (`NOT_VERIFIED`) on half of all tasks and is right most of the time it
  doesn't abstain; the retry loop commits to an answer more often
  (abstains 25% instead of 50%) and is more likely to be correct when it
  does commit (50% precision vs 25%), but its extra attempts sometimes
  convert a would-be abstention into a wrong "pass," which is why overall
  accuracy doesn't clearly improve. This replaces Real run #1's "the retry
  loop did worse" with a more precise, and less alarming, finding: **the
  retry loop shifts the error profile (fewer abstentions, better precision
  when it commits) rather than uniformly outperforming single-shot** — at
  ~12x the latency cost (70.4s vs 5.7s avg). Whether that trade is worth
  it depends on whether a deployment prefers "commit more, be more precise
  when committing" over "abstain more, be very reliable when not
  abstaining" — a product decision, not a bug.
- **This is one run, n=28, no repetition** — same caveat as Real run #1.
  These numbers describe what happened this run, not a statistically
  robust claim about `llama3.2` in general.

**`qwen3:8b` — incomplete, and the incompleteness is itself the finding.**
The run was killed after 3/28 tasks (raw_llm: 1/28, single_shot_z3: 0/28,
verityai_full: 3/28) because it was failing, not just slow:

- Every Ollama call used a 90s timeout with 3 retries; most `qwen3:8b`
  calls exhausted all 3 attempts (`TimeoutError`), meaning several
  *minutes* per single generation call that then still failed.
- One call crashed the Ollama server outright: `"model runner has
  unexpectedly stopped, this may be due to resource limitations or an
  internal error"` (HTTP 500).
- Per-task latency for the few `verityai_full` calls that did complete:
  222-274 seconds (vs. `llama3.2`'s 70s average) — and those were failures
  (`status=fail`), not passes.
- At the observed rate, completing the full 28-task × 3-baseline set would
  have taken multiple hours with a non-trivial chance of repeated server
  crashes, for data quality already trending toward unusable (0% accuracy
  on the 3 completed `verityai_full` tasks, all "novel").

**Honest conclusion**: this is a real, reportable finding, not a null
result to hide — **the architecture's model-agnostic design (any Ollama
model works through the same `OllamaClient` interface) is real, but
"works through the interface" and "runs reliably" are different claims.**
`qwen3:8b` at 8B parameters was not reliably runnable on this machine's
CPU under this workload; `llama3.2` at 3B was. A cross-model evaluation is
only as informative as the hardware it runs on, and this is exactly the
kind of constraint a synthetic/scripted benchmark would never surface.
Documented here instead of quietly dropping `qwen3:8b` from the results
with no explanation.

## Real run #3 (2026-07-14, three-arm retrieval A/B: no_kg vs legacy_kg vs hybrid_kg)

Real run #1 suspected, and Real run #2 didn't isolate, whether KG context
helps or hurts a small model — `verityai_full` in both prior runs was
actually a `no_kg` arm all along (`get_orchestrator` never wired a
`kg_client` into `/generate`; see `docs/adr/0003-hybrid-retrieval.md`).
This run fixes that by exercising three arms on the same 28 tasks against
the same live `llama3.2`: **`no_kg`** (no KG context at all — the prior
runs' actual behavior), **`legacy_kg`** (fetch-all rules by two hardcoded
categories, no ranking), and **`hybrid_kg`** (`HybridRetriever`: BM25 +
cosine similarity fused via RRF, ranked against the actual prompt). Raw
data: `docs/results/2026-07-14_retrieval_ab.json`.

**Setup note, corrected from the original plan**: the plan assumed
`llama3.2` itself would double as a mediocre-but-usable embedder, since no
dedicated embedding model was thought to be pullable into the project's
Ollama container. Both assumptions turned out to be wrong once actually
tested — `llama3.2` returns HTTP 501 (`"This server does not support
embeddings"`) from `/api/embed` regardless of flags, and a dedicated
embedding model (`nomic-embed-text`, 274MB) pulled and ran without any
special configuration. `OLLAMA_EMBED_MODEL=nomic-embed-text` is what
actually powered semantic scoring for this run; see the corrected section
of ADR-0003 for the full story. A live spot-check after the run (same rule
corpus, same embeddings, `retrieval_strategy=hybrid`) confirms the
mechanism genuinely engages hybrid mode with real semantic similarity
(0.73 top score, retrieving `Array Bounds Check` for an array-bounds
prompt) — not a lexical-only degradation dressed up as hybrid.

**Complete run, 28 tasks × 3 arms = 84 pairs, 91.3 minutes:**

| Arm | N | accuracy | precision | recall | F1 | abstention | avg latency | avg attempts |
|---|---|---|---|---|---|---|---|---|
| `no_kg` | 26 | 75.0% | 75.0% | 60.0% | 66.7% | 23.1% | 62.5s | 2.54 |
| `legacy_kg` | 26 | 83.3% | 80.0% | 80.0% | 80.0% | 26.9% | 73.5s | 2.62 |
| `hybrid_kg` | 28 | 63.2% | 100.0% | 12.5% | 22.2% | 17.9% | 62.0s | 2.11 |

(N differs because 4/84 pairs hit a Z3 engine crash — see below — reducing
`no_kg` and `legacy_kg` to 26 completed outcomes each; `hybrid_kg`
completed all 28.)

Reading this honestly, not selectively:

- **Hybrid retrieval does not win this run — legacy_kg does, clearly.**
  `legacy_kg`'s dumb fetch-all beats both `no_kg` and `hybrid_kg` on
  accuracy, recall, and F1. `hybrid_kg` has the *worst* accuracy of the
  three arms (63.2%, vs. legacy's 83.3%) and dramatically worse recall
  (12.5% vs. legacy's 80.0%). If the working hypothesis going in was
  "smarter retrieval beats fetch-all," this run says the opposite.
- **`hybrid_kg`'s one clean win is precision (100%) — and it comes from
  never committing, not from being sharp.** 100% precision with 12.5%
  recall means: of the tasks that actually had a bug, `hybrid_kg` flagged
  1 out of 8. Combined with its lowest average attempts (2.11 vs.
  2.54/2.62) and its `pass` rate (19/28 = 67.9%, well above `no_kg`'s
  38.5% and `legacy_kg`'s 30.8%), the picture is a model that converges
  faster and passes more readily when semantically-ranked rule context is
  injected — but that faster convergence is *not* earning its keep on
  catching real bugs. This is the same *shape* of trade-off Real run #2
  found for the retry loop itself (fewer abstentions, different precision/
  recall balance, not a uniform win) — worth investigating together, not
  as two unrelated findings. Flagged for Phase 1 (T2) of the next research
  round rather than concluded here.
- **A Z3 engine bug surfaced 4 times, and zero of them were in
  `hybrid_kg`.** All 4 failures are the identical error, `"Value cannot be
  converted into a Z3 Boolean value"`, spread across 3 tasks
  (`correctness_022_negate_twice_identity` in `no_kg`;
  `security_002_bounds_check` in `legacy_kg`;
  `security_006_check_auth_before_action` in both `no_kg` and
  `legacy_kg`). `hybrid_kg` completed all three of those tasks cleanly.
  With n=1 per (task, arm) this is not evidence that hybrid context avoids
  the bug — it's exactly the kind of pattern that looks meaningful with 3
  data points and evaporates with 30. Documented as an open question, not
  a claim: does KG-context shape (via the LLM's generated code) correlate
  with which Z3 parse paths get hit, or is this coincidence? The
  underlying bug (a boolean-coercion gap in the AST→SMT converter) is a
  real defect regardless of the answer and should be fixed on its own
  merits.
- **A real methodological gap, disclosed rather than glossed over**:
  `run_verityai_full_baseline` (`evaluation/baselines.py`) does not
  persist `kg_context`/`retrieval.mode` per task — `BenchmarkOutcome` only
  keeps `predicted_status`/`confidence`/`latency_seconds`/`attempts`. The
  live spot-check above confirms the retrieval *mechanism* engaged hybrid
  mode with real embeddings using the same corpus state as the run, but it
  does **not** prove each of the 28 individual `hybrid_kg` calls used
  semantic scoring rather than silently degrading to `lexical_only` for
  that specific call (e.g. a transient `embed()` failure). Next A/B run
  should thread `kg_context.retrieval.mode` into `BenchmarkOutcome` so
  this is observed per-task, not inferred after the fact.
- **This is one run, n=28, no repetition** — same caveat as Real runs #1
  and #2. A single run where `hybrid_kg` loses this badly on recall is
  reason to *not* flip `VERITYAI_RETRIEVAL_STRATEGY`'s default away from
  `legacy` yet, not reason to conclude hybrid retrieval is a dead end.

**What this does NOT show**: whether the recall collapse is specific to
`llama3.2` at 3B parameters, specific to this particular rule corpus (50
rules, unfiltered by task relevance beyond the retriever's own ranking),
or a structural property of injecting ranked-but-untested context into a
retry loop. It also does not show whether `legacy_kg`'s win here
generalizes — fetch-all works when the corpus is small enough that "all
the rules" is still a reasonable prompt payload; that stops being true as
the corpus grows past a few hundred rules, which `hybrid_kg` was
specifically built to handle. **Decision**: `VERITYAI_RETRIEVAL_STRATEGY`
default stays `legacy` — the data available today does not justify
flipping it, and `hybrid_kg`'s recall collapse is a specific, trackable
problem (see the research roadmap's T2 and T4) rather than grounds to
abandon the hybrid retriever entirely.

## Analysis: confidence calibration and the retry-loop noise floor (2026-07-15)

No new generation happened for this section — it's pure analysis over the
already-persisted outcomes from Real run #2 and Real run #3
(`scripts/analyze_confidence_calibration.py`, output:
`docs/results/2026-07-15_t1_t2_analysis.json`). This answers Fase 1 of the
T1-T6 research roadmap (T1 confidence calibration, T2/T7 the retry-loop
trade-off), and the second finding below is significant enough to qualify
how every prior "Real run" result in this document should be read.

**T1 — confidence does not calibrate monotonically, and the miscalibration
is sometimes inverted, not just imprecise.** Binning every outcome by its
own confidence score and computing the empirical fraction of
`ground_truth == "correct"` per bin (a reliability diagram) gives an
Expected Calibration Error (ECE) per baseline/arm:

| Baseline/arm | ECE | Notable pattern |
|---|---|---|
| `raw_llm` | 0.500 | Always emits confidence 1.0 (never verifies); empirical accuracy is 50% — the "null model," included as a contrast, not a real baseline |
| `single_shot_z3` | 0.252 | The `[0.0, 0.2)` bin (confidence≈0, i.e. `FAIL`) has 75% empirical accuracy — code flagged as buggy with near-zero confidence was usually actually *correct*. The `[0.2, 0.4)` bin (`NOT_VERIFIED`, fixed 0.3 baseline) has only 12.5% empirical accuracy — abstained cases were usually actually *buggy*. Backwards from what a useful confidence signal should show. |
| `verityai_full` | 0.381 | Directionally monotonic (0.5 → 0.6 → 1.0 as confidence rises) but systematically underconfident at the top |
| `no_kg` | 0.236 | Monotonic (0.25 → 0.375 → 1.0), also underconfident at the top |
| `legacy_kg` | 0.304 | Monotonic (0.2 → 0.6 → 1.0) |
| `hybrid_kg` | 0.136 | **Lowest ECE of any real config** — but not monotonic (0.8 → 0.5 → 0.8) |

Reading this honestly: no configuration is well-calibrated in the strict
sense, `single_shot_z3` shows a genuinely *inverted* signal in its two
lowest bins (not just noisy — backwards), and `hybrid_kg` has the best ECE
of the bunch despite having the worst recall in Real run #3 — calibration
quality and detection quality are not the same claim and shouldn't be
conflated. **The load-bearing caveat**: bins here hold as few as 2-6
outcomes each (n=28 total split five ways), so these numbers describe what
this run showed, not a statistically robust calibration claim — a single
flipped verdict moves a bin's empirical accuracy by 12-25 percentage
points. This is a first look that shows enough signal to say "don't trust
the current confidence formula as calibrated," not enough n to pin down
by how much or confirm the inversions survive repetition.

**T2/T7 — the retry-loop "trade-off" is statistically indistinguishable
from run-to-run noise, and this is the more important finding of the
two.** Since `ground_truth` is decided per-outcome (each baseline
independently re-generates code at `temperature=0.7`, so it isn't a fixed
per-task label), a task where two baselines' outputs land on the *same*
ground truth isolates what the verification/retry mechanism did with
equivalently-good-or-bad code, separate from noise in what code got
generated in the first place:

- `single_shot_z3` vs `verityai_full` (Real run #2, same day): ground
  truth agrees on 71.4% of tasks (20/28); of those, the verifier's
  **status still differs 55% of the time** (11/20) — on code of
  equivalent actual quality, the retry mechanism reaches a different
  verdict about half the time.
- **The noise floor**: `verityai_full` (Real run #2) and `no_kg` (Real
  run #3) are the *same configuration* — full retry loop, zero KG
  context — run on two different days. Ground truth agrees on 69.2% of
  tasks (18/26); status still differs on **50%** of those (9/18) —
  essentially identical to the single-shot-vs-full-retry numbers above
  (71.4%/55% vs. 69.2%/50%).

That similarity is the finding: **two runs of the literal same
configuration disagree on verdicts to almost the same degree as
single-shot and full-retry disagree with each other.** The accuracy/
precision/recall differences reported in Real run #2 cannot currently be
confidently attributed to the retry mechanism itself — they are
statistically indistinguishable, at this sample size, from ordinary
`temperature=0.7` sampling noise across independent runs. This doesn't
mean the retry loop has no real effect; it means Real run #2's framing
("the retry loop shifts the error profile") was stated with more
confidence than n=28-with-no-repetition actually supports, and that
should have been flagged there rather than only surfacing now.

**A secondary, more encouraging signal from the same method**: pairwise
ground-truth agreement across the three retrieval arms (all real-run-#3
configurations, same day) is *lower* than the cross-day noise floor —
`no_kg` vs `legacy_kg` agree on only 48.0% of tasks, `no_kg` vs
`hybrid_kg` on 57.7%, `legacy_kg` vs `hybrid_kg` on 61.5% (vs. ~69-71%
for two same-config runs). Lower-than-noise-floor agreement suggests the
KG context genuinely does shift what code gets generated — with
`legacy_kg`'s unranked fetch-all producing the biggest divergence from
no-context, more than `hybrid_kg`'s narrower ranked context does. Unlike
the retry-loop trade-off, this part of the signal *exceeds* the noise
floor rather than sitting inside it, so it's on firmer ground — though
still n=25-26 pairs, still one run each, still not repeated.

**What this changes going forward**: any future A/B or cross-model run in
this project should budget for **repeated runs of the same configuration**
before attributing an accuracy/precision/recall difference to a
mechanism, not just to the LLM's sampling variance. A single run's
confusion matrix is not enough to separate the two, and this analysis is
the first time that's been checked directly rather than assumed away.

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

Updated after Real run #2 (above) — the ground-truth gap (Real run #1's
Finding 1) is closed and a cross-model attempt has been made; what's left:

1. ~~Close the ground-truth gap~~ **Done** — `execution_oracle.py`
   replaced exact-string matching; Real run #2's `novel_rate` (14-36%)
   vs. Real run #1's 100% confirms it works against a live model.
2. **Investigate the retry-loop's shifted error profile (Real run #2)**:
   it abstains less and is more precise when it commits, but doesn't
   clearly beat single-shot on raw accuracy. Worth isolating whether KG
   context injection specifically is the driver (rerun with it disabled)
   before treating the current trade-off as inherent to retrying.
3. **Get a second model actually running reliably.** `qwen3:8b` was not
   viable on this machine (Real run #2) — either get access to hardware
   that can run an 8B+ model without timing out/crashing, or explicitly
   scope the portfolio narrative to "validated on `llama3.2`-class models;
   architecture is model-agnostic by design but larger models are
   untested on available hardware" rather than claiming cross-model
   validation that didn't actually complete. `llama2:13b` remains
   unavailable in this sandboxed environment (no outbound network access
   to pull it) for the same underlying reason this was worth trying with
   what was already local.
4. Scale benchmarks to Lote 3 (50+ correctness, 20+ security) — unblocked
   by ADR-0002 for correctness, and now also unblocked by the working
   ground-truth oracle for security tasks that were previously excluded.
5. Decide whether SQL injection / race-condition detection belongs in this
   evaluation framework at all, or whether it's better scoped as a
   separate `rule_engine`-based benchmark track (pattern matching, not Z3
   satisfiability).
6. Confirm or revise the 50% target threshold now that `buggy_accept_rate`
   is actually computable — Real run #2 gives a first real data point
   (`raw_llm` never predicts buggy, so its `buggy_accept_rate` baseline is
   trivially bad; `verityai_full`'s improvement over it should be computed
   explicitly as a dedicated follow-up, not read off the confusion-matrix
   summary above).
7. Repeat with n>1 per task before treating any of Real run #2's numbers
   as more than a first data point — single-run stats on n=28 are noisy.
