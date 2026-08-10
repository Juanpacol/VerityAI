"""Telling a real metric difference apart from ordinary run-to-run noise.

Carried over from the pre-pivot tree and generalized (see ADR-0010). The
original grew out of a real finding in the T1-T6 research programme: a single
run's outcome comparison cannot tell a real mechanism effect apart from
`temperature=0.7` sampling variance across independent runs — checked by hand
once, then made a reusable library so the check would actually get applied
consistently instead of re-derived from scratch each time. That standing
rule, unchanged, is `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure: *never
attribute a metric difference to a mechanism without a same-configuration
repeat establishing the noise floor first.*

The generalization: the original operated on `list[BenchmarkOutcome]` —
classification verdicts with a `predicted_status`/`ground_truth` shape tied
to the pre-pivot code-generation benchmarks. Nothing about the harness this
project is now measures classification outcomes; a repeat here is a task
attempted once under one configuration, and what varies run to run is
whatever the caller decided to measure (`{"success": 1.0}`, `{"tokens_saved":
812, "success": 0.0}`, anything). So a "repeat" is now a plain
`dict[str, float]`, and `ground_truth_agreement`/`pairwise_agreement_summary`
— which only make sense for the classification shape — are retired rather
than stretched; their "compare pairs of repeats" reasoning survives inside
`summarize_metric_variance` and `compare_to_noise_floor` below.

One deliberate improvement over the original, not just a rename: the old
`is_difference_significant_vs_noise` only checked whether a between-config
rate fell *below* the within-config floor, because for classification
agreement, less agreement always meant "more different." An arbitrary metric
has no such direction — a real improvement in `success` rate goes *up*, not
down — so `compare_to_noise_floor` checks *outside* the `[min, max]` floor
range in either direction.
"""

import statistics
from typing import Any


def summarize_metric_variance(repeats: list[dict[str, float]]) -> dict[str, Any]:
    """Mean/stdev/min/max for every metric key present, across N repeats of
    ONE configuration. This is the noise-floor computation itself — how much
    does each metric bounce around on its own, before it is ever compared to
    a different configuration's value.

    Repeats need not all report the same keys (a failed trial might report
    `{"success": 0.0}` alone, without a `tokens_saved` that only made sense
    once the task actually ran) — each metric's statistics are computed only
    over the repeats that reported it, and its own count travels with it.
    """
    if not repeats:
        raise ValueError("Need at least 1 repeat")

    keys = sorted({key for repeat in repeats for key in repeat})
    summary: dict[str, Any] = {"n_repeats": len(repeats)}
    for key in keys:
        values = [repeat[key] for repeat in repeats if key in repeat]
        summary[key] = {
            "n": len(values),
            "values": [round(v, 4) for v in values],
            "mean": round(statistics.mean(values), 4),
            "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
            "min": round(min(values), 4),
            "max": round(max(values), 4),
        }
    return summary


def compare_to_noise_floor(
    within_repeats: list[dict[str, float]],
    between_repeats: list[dict[str, float]],
    metric: str,
) -> dict[str, Any]:
    """Is `between_repeats`' mean for `metric` outside the noise floor
    established by repeating the SAME configuration (`within_repeats`)?

    Per `docs/BENCHMARK_PROTOCOL.md`: "the floor is the range — min to max —
    not the mean," so the floor here is `[min(within), max(within)]`, and a
    between-configuration mean landing inside that range is reported as
    `indistinguishable_from_noise` — not as a small effect, not as a trend,
    exactly the distinction the protocol insists on. `within_repeats` needs
    at least 2 repeats to have a range at all; fewer is `insufficient_data`,
    never a silent 1-vs-1 comparison standing in for a real floor.
    """
    within_values = [r[metric] for r in within_repeats if metric in r]
    between_values = [r[metric] for r in between_repeats if metric in r]

    if len(within_values) < 2:
        return {
            "conclusion": "insufficient_data",
            "reason": (
                f"need >=2 within-config repeats reporting {metric!r} to "
                f"establish a noise floor, got {len(within_values)}"
            ),
        }
    if not between_values:
        return {
            "conclusion": "insufficient_data",
            "reason": f"no between-config repeats reported {metric!r}",
        }

    noise_floor_min = min(within_values)
    noise_floor_max = max(within_values)
    between_mean = statistics.mean(between_values)
    outside = between_mean < noise_floor_min or between_mean > noise_floor_max

    return {
        "metric": metric,
        "noise_floor_min": round(noise_floor_min, 4),
        "noise_floor_max": round(noise_floor_max, 4),
        "between_config_mean": round(between_mean, 4),
        "outside_noise_floor": outside,
        "conclusion": "likely_real_difference" if outside else "indistinguishable_from_noise",
    }
