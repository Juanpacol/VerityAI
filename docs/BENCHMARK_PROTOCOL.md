# Benchmark protocol

Written before any benchmark was run, deliberately. The T1–T6 programme
produced one methodological result more valuable than any of its findings: a
single run cannot tell a mechanism effect apart from `temperature=0.7` sampling
variance, and the retry-loop improvement that had already been written up had
to be **retracted** once same-configuration repeats were compared. This
document exists so that does not happen twice.

Status: **no benchmark has been run.** No figure in this repository, the
README, or any external material describes measured harness performance.

## The two families

Metrics are split by whether an LLM is in the loop, and the two families are
**never reported in the same table**. Mixing them is how a deterministic
measurement borrows credibility for a stochastic one.

### Family A — deterministic

No model call anywhere in the measured path, so the same input produces the
same number every time. `n=1` is sufficient and a repeat is only a smoke test.

| Metric | Definition |
|---|---|
| `tokens_before` / `tokens_after` | Sum of per-item counts, one counting method throughout |
| `tokens_saved`, `reduction_ratio` | Difference and ratio |
| Per-stage ledger | Tokens in/out for each of the seven pipeline stages |
| `critical_retention` | Fraction of protected items surviving. **Must be 1.0** |
| Handoff cost | Tokens in the generated document, plus sections dropped |
| Wall-clock | Pipeline duration, excluding any model call |

Family A is what Phase 1 can honestly claim. It is real, it is auditable
stage by stage, and it needs no statistics.

### Family B — stochastic

An LLM decides something in the measured path. **Requires N ≥ 5 repeats per
configuration and a noise floor established before any comparison.**

| Metric | Why it is stochastic |
|---|---|
| Task success rate | The agent's competence varies run to run |
| Hallucinated claims caught | Depends what the agent chose to claim |
| Contradictions detected | Same |
| Recovery quality after reset | Judged by a model or a human |
| Cost per completed task | Success rate is in the denominator |

Family B is **out of scope for Phase 1** and no such number will be published
until the procedure below has been executed.

## Procedure for Family B

Non-negotiable, in this order:

1. **Establish the noise floor.** Run configuration A, N times, unchanged.
   Compute pairwise agreement across all N(N−1)/2 pairs. The floor is the
   *range* — min to max — not the mean. With N > 2 it is a distribution and
   collapsing it to a point estimate throws away the thing being measured.
2. **Repeat for configuration B**, N times, unchanged.
3. **Only then compare** A against B.
4. **Interpret against the floor.** A between-configuration agreement rate
   *inside* the within-configuration range is `no effect detected`. Not "a
   small improvement", not "a promising trend". Below the floor's minimum, it
   is `likely_real_difference` — still not proof, but reportable.
5. **Report N, the floor range, and the comparison together.** A delta without
   its noise floor is not a result and must not be quoted alone.

`_quarantine/repetition.py` implements steps 1–4 and must be used rather than
reimplemented — the point of a standing rule is that it is applied the same
way every time. It needs generalizing from its classification-metrics shape to
arbitrary metric dicts before it can leave quarantine.

## What "Agent alone vs Agent + Verity" actually requires

The pivot proposal asks for this comparison across tokens, cost, hallucinations,
task success, contradictions, regressions and technical debt. Most of those
rows are Family B, and the honest position today is:

- **Tokens and cost per task**: Family A for the pipeline itself. Becomes
  Family B the moment task success enters the denominator, because a cheaper
  run that fails is not cheaper.
- **Hallucinations, contradictions, regressions**: Family B, and additionally
  blocked on the Consistency Engine existing at all (Phase 3).
- **Technical debt**: not yet operationally defined. No metric until it is.
- **Recovery after reset**: Family B, and the most valuable row in the table.
  It is the one thing the harness does that an agent cannot do for itself.

A benchmark table with unmeasured rows ships with those rows **empty and
labelled** `not measured`, never with a plausible-looking placeholder.

## Publication rule

No number appears in the README, a pitch, the GitHub description, or any
external material until:

1. It has been measured on a real workload, not a synthetic fixture.
2. If Family B, its noise floor exists and is reported next to it.
3. The counting method is stated wherever a token count appears.
4. The workload is described well enough for someone else to reproduce it.

### The synthetic-fixture trap

During Phase 1 development the pipeline was run against a generated transcript
and reported a 92.4% reduction. **That number is worthless** and is recorded
here only as an example of what not to publish: the transcript contained ~90%
exact duplicates by construction, so the figure measures the fixture, not the
tool. Real transcripts do not look like that.

The first honest measurement will come from real agent sessions on this
repository — including the sessions that build the harness itself.
