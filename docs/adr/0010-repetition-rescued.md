# ADR-0010: `repetition.py` rescued and generalized to arbitrary metrics

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: closing the last item in `_quarantine/`, needed as the
  foundation for the Family B pilot (see the plan this session is executing).

## Context

`_quarantine/repetition.py` implemented the standing rule from the pre-pivot
T1–T6 research programme — never attribute a metric difference to a
mechanism without a same-configuration repeat establishing a noise floor
first — but operated on `list[BenchmarkOutcome]`, a classification-verdict
shape (`predicted_status`/`ground_truth`) tied to code-generation benchmarks
that no longer exist. `docs/BENCHMARK_PROTOCOL.md` already flagged exactly
what was needed to un-quarantine it: generalize from that shape to arbitrary
metric dicts.

## Decision

A "repeat" is now a plain `dict[str, float]` — whatever the caller measured
about one trial of one configuration (`{"success": 1.0}`, `{"tokens_saved":
812, "success": 0.0}`, anything). Two functions survive the generalization:

- `summarize_metric_variance(repeats) -> dict` — mean/stdev/min/max for every
  metric key present, computed only over the repeats that reported it (a
  failed trial might report `success` alone). This is the noise-floor
  computation itself.
- `compare_to_noise_floor(within_repeats, between_repeats, metric) -> dict` —
  is the other configuration's mean outside the `[min, max]` range the first
  configuration's own repeats established? Below 2 within-repeats or zero
  between-repeats reporting the metric returns `insufficient_data` rather
  than a verdict built on too little.

`ground_truth_agreement` and `pairwise_agreement_summary` are retired, not
adapted — they compare classification verdicts over shared `task_id`s, a
concept with no equivalent once a repeat is an arbitrary metric dict rather
than a re-generated-code outcome. Their "compare pairs of repeats" reasoning
is what `summarize_metric_variance` already does more generally.

One correction beyond a straight port: the original
`is_difference_significant_vs_noise` only checked whether a between-config
value fell *below* the within-config floor, because for classification
agreement, less agreement always meant more different. An arbitrary metric
has no privileged direction — a real improvement in a `success` rate moves
*up*, not down — so `compare_to_noise_floor` checks *outside* `[min, max]`
in either direction. A version that only checked "below" would silently miss
every real improvement.

Moved to `bench/repetition.py` — its real home, not `_quarantine/` — with
`_quarantine/repetition.py` deleted and the whole `_quarantine/` directory
removed along with it, since `rule_engine.py` (Phase 4, ADR-0008) had
already left it and nothing remained waiting.

## Consequences

- The Family B pilot (next) has its noise-floor library ready before a
  single trial runs, per the protocol's own ordering: establish the
  mechanism for telling signal from noise before generating data that needs
  it told apart.
- Zero new dependencies — `statistics` is standard library, as it always was.
- `_quarantine/` no longer exists in the tree. Anything rescued in the future
  starts a new one; there is nothing to preserve in an empty holding pattern.
