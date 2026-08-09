"""Tools for checking whether a metric difference between configurations
is distinguishable from ordinary LLM sampling noise, given repeated runs.

Grew out of a real finding in the T1-T6 research roadmap
(`docs/PHASE_3_METHODOLOGY.md`'s "Analysis" section, 2026-07-15): a
single run's confusion matrix cannot tell a real mechanism effect apart
from `temperature=0.7` sampling variance across independent runs. That
finding was checked by hand, comparing two already-persisted JSON result
files after the fact in a one-off script. This module makes the same
check a reusable, tested library function instead, and extends it from a
single before/after pair to N repeats with real variance statistics --
the "standing rule" from that finding (never attribute a metric
difference to a mechanism without a same-configuration repeat) needs
something more than eyeballing two numbers to actually apply going
forward.
"""

import statistics
from typing import Any

from verityai.evaluation.metrics import BenchmarkOutcome, compute_classification_metrics


def ground_truth_agreement(a: list[BenchmarkOutcome], b: list[BenchmarkOutcome]) -> dict[str, Any]:
    """Compares two independent outcome lists over their common task_ids.

    `ground_truth` is decided per-outcome (each run independently
    re-generates code), not a fixed per-task label -- so a task where both
    lists land on the same ground truth isolates what each configuration's
    verdict (`predicted_status`) did with equivalently-good-or-bad code
    from noise in what code got generated in the first place.
    """
    a_by_task = {o.task_id: o for o in a}
    b_by_task = {o.task_id: o for o in b}
    common = sorted(set(a_by_task) & set(b_by_task))

    gt_agree = 0
    status_diff_given_gt_agree = 0
    status_diff_total = 0
    for task_id in common:
        oa, ob = a_by_task[task_id], b_by_task[task_id]
        status_differs = oa.predicted_status != ob.predicted_status
        if status_differs:
            status_diff_total += 1
        if oa.ground_truth == ob.ground_truth:
            gt_agree += 1
            if status_differs:
                status_diff_given_gt_agree += 1

    n = len(common)
    return {
        "n_common_tasks": n,
        "ground_truth_agreement_rate": round(gt_agree / n, 3) if n else None,
        "status_diff_given_gt_agrees": status_diff_given_gt_agree,
        "status_diff_given_gt_agrees_rate": (
            round(status_diff_given_gt_agree / gt_agree, 3) if gt_agree else None
        ),
        "status_diff_total": status_diff_total,
        "status_agreement_rate": round(1 - status_diff_total / n, 3) if n else None,
    }


def pairwise_agreement_summary(repeats: list[list[BenchmarkOutcome]]) -> dict[str, Any]:
    """Ground-truth agreement between EVERY pair of repeats of the SAME
    configuration -- generalizes a single before/after comparison to N>=2
    repeats. The noise floor is a distribution once N>2, not one number,
    so this reports min/max/mean across all pairs rather than picking one.
    """
    if len(repeats) < 2:
        raise ValueError("Need at least 2 repeats to compute pairwise agreement")

    pairs = []
    for i in range(len(repeats)):
        for j in range(i + 1, len(repeats)):
            pairs.append(
                {"repeat_a": i, "repeat_b": j, **ground_truth_agreement(repeats[i], repeats[j])}
            )

    rates = [
        p["ground_truth_agreement_rate"]
        for p in pairs
        if p["ground_truth_agreement_rate"] is not None
    ]
    return {
        "n_repeats": len(repeats),
        "n_pairs": len(pairs),
        "pairs": pairs,
        "ground_truth_agreement_rate_mean": round(statistics.mean(rates), 3) if rates else None,
        "ground_truth_agreement_rate_min": min(rates) if rates else None,
        "ground_truth_agreement_rate_max": max(rates) if rates else None,
    }


def summarize_metric_variance(repeats: list[list[BenchmarkOutcome]]) -> dict[str, Any]:
    """Mean +/- stdev of accuracy/precision/recall/F1 across N repeats of
    the SAME configuration -- how much does this metric bounce around on
    its own, before comparing it to a different configuration's metric.
    """
    if not repeats:
        raise ValueError("Need at least 1 repeat")

    per_repeat_metrics = [compute_classification_metrics(r) for r in repeats]
    summary: dict[str, Any] = {"n_repeats": len(repeats)}
    for metric_name in ("accuracy", "precision", "recall", "f1"):
        values = [m[metric_name] for m in per_repeat_metrics]
        summary[metric_name] = {
            "values": [round(v, 3) for v in values],
            "mean": round(statistics.mean(values), 3),
            "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
        }
    return summary


def is_difference_significant_vs_noise(
    within_config_agreement_rates: list[float],
    between_config_agreement_rate: float,
) -> dict[str, Any]:
    """Is a between-configuration ground-truth agreement rate below the
    range observed WITHIN repeats of one configuration? If so, the
    configurations likely differ by more than noise (this is the check
    that found the KG-context effect real and the retry-loop trade-off
    not, in the original T2 analysis -- made reusable and programmatic
    here instead of a manual before/after comparison).
    """
    if not within_config_agreement_rates:
        return {"conclusion": "insufficient_data", "reason": "no within-config repeats available"}

    noise_floor_min = min(within_config_agreement_rates)
    noise_floor_max = max(within_config_agreement_rates)
    below_noise_floor = between_config_agreement_rate < noise_floor_min

    return {
        "noise_floor_min": round(noise_floor_min, 3),
        "noise_floor_max": round(noise_floor_max, 3),
        "between_config_agreement_rate": round(between_config_agreement_rate, 3),
        "below_noise_floor": below_noise_floor,
        "conclusion": "likely_real_difference"
        if below_noise_floor
        else "indistinguishable_from_noise",
    }
