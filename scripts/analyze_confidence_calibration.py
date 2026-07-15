#!/usr/bin/env python3
"""Fase 1 of the T1-T6 research roadmap: confidence calibration (T1) and the
retry-loop trade-off (T2/T7) -- computed entirely from already-collected
real run data, no new LLM calls, no new infrastructure:

- docs/results/2026-07-13_cross_model_run.json (Real run #2: raw_llm,
  single_shot_z3, verityai_full baselines against live llama3.2)
- docs/results/2026-07-14_retrieval_ab.json (Real run #3: no_kg, legacy_kg,
  hybrid_kg arms, also against live llama3.2)

T1 (calibration): bins every outcome by its own confidence score, and
reports the EMPIRICAL fraction where ground_truth=="correct" per bin --
a reliability diagram -- plus the Expected Calibration Error (ECE). Ground
truth is per-OUTCOME (each baseline call independently re-generates code
at temperature=0.7 and gets its own execution-oracle verdict), not a fixed
per-task label, so "does confidence predict correctness" is evaluated
against what that specific call actually produced.

T2 (retry trade-off): the aggregate accuracy/precision/recall difference
between single-shot and full-retry was already reported in
PHASE_3_METHODOLOGY.md's Real run #2. This goes one level deeper: since
ground_truth is per-outcome, not per-task, a task where BOTH baselines
happened to produce code of the same actual quality (ground_truth
agrees) isolates the retry MECHANISM'S effect on the verifier's verdict
from noise in what code got generated in the first place. Tasks where
ground_truth disagrees are confounded by generation variance and are
reported separately, not folded into a false attribution.

Usage:
  python scripts/analyze_confidence_calibration.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CROSS_MODEL_PATH = REPO_ROOT / "docs" / "results" / "2026-07-13_cross_model_run.json"
RETRIEVAL_AB_PATH = REPO_ROOT / "docs" / "results" / "2026-07-14_retrieval_ab.json"

# (low, high) confidence ranges; the last bin's high is inclusive of 1.0.
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]


def _bin_index(confidence: float) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if i == len(BINS) - 1:
            if lo <= confidence <= hi:
                return i
        elif lo <= confidence < hi:
            return i
    return len(BINS) - 1 if confidence > BINS[-1][1] else 0


def reliability_diagram(outcomes: list[dict]) -> dict:
    """Bin outcomes by confidence; empirical accuracy per bin = fraction
    where ground_truth=="correct" (ground_truth=="novel" is excluded --
    there's no oracle verdict for output matching neither known variant).
    """
    scored = [o for o in outcomes if o["ground_truth"] != "novel"]
    bin_data = [{"count": 0, "conf_sum": 0.0, "correct": 0} for _ in BINS]

    for outcome in scored:
        idx = _bin_index(outcome["confidence"])
        bin_data[idx]["count"] += 1
        bin_data[idx]["conf_sum"] += outcome["confidence"]
        if outcome["ground_truth"] == "correct":
            bin_data[idx]["correct"] += 1

    total = len(scored)
    rows = []
    ece = 0.0
    for (lo, hi), data in zip(BINS, bin_data):
        if data["count"] == 0:
            rows.append(
                {
                    "range": f"[{lo:.1f}, {hi:.1f}]",
                    "count": 0,
                    "mean_confidence": None,
                    "empirical_accuracy": None,
                }
            )
            continue
        mean_conf = data["conf_sum"] / data["count"]
        emp_acc = data["correct"] / data["count"]
        rows.append(
            {
                "range": f"[{lo:.1f}, {hi:.1f}]",
                "count": data["count"],
                "mean_confidence": round(mean_conf, 3),
                "empirical_accuracy": round(emp_acc, 3),
            }
        )
        ece += (data["count"] / total) * abs(mean_conf - emp_acc)

    return {
        "bins": rows,
        "ece": round(ece, 4),
        "n_scored": total,
        "n_excluded_novel": len(outcomes) - total,
    }


def load_labeled_outcomes() -> dict[str, list[dict]]:
    labeled: dict[str, list[dict]] = {}

    cross_model = json.loads(CROSS_MODEL_PATH.read_text())
    for baseline, outcomes in cross_model["results"]["llama3.2"].items():
        labeled[f"cross_model/llama3.2/{baseline}"] = outcomes

    retrieval_ab = json.loads(RETRIEVAL_AB_PATH.read_text())
    for arm, outcomes in retrieval_ab["results"].items():
        labeled[f"retrieval_ab/{arm}"] = outcomes

    return labeled


def analyze_t1_calibration(labeled_outcomes: dict[str, list[dict]]) -> dict:
    return {label: reliability_diagram(outcomes) for label, outcomes in labeled_outcomes.items()}


def ground_truth_agreement(a: list[dict], b: list[dict]) -> dict:
    """Compares two independent outcome lists over their common task_ids."""
    a_by_task = {o["task_id"]: o for o in a}
    b_by_task = {o["task_id"]: o for o in b}
    common = sorted(set(a_by_task) & set(b_by_task))

    gt_agree = 0
    status_diff_given_gt_agree = 0
    status_diff_total = 0
    for task_id in common:
        oa, ob = a_by_task[task_id], b_by_task[task_id]
        status_differs = oa["status"] != ob["status"]
        if status_differs:
            status_diff_total += 1
        if oa["ground_truth"] == ob["ground_truth"]:
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


def analyze_t2_retry_tradeoff(labeled_outcomes: dict[str, list[dict]]) -> dict:
    single = labeled_outcomes["cross_model/llama3.2/single_shot_z3"]
    full = labeled_outcomes["cross_model/llama3.2/verityai_full"]
    no_kg = labeled_outcomes["retrieval_ab/no_kg"]
    legacy_kg = labeled_outcomes["retrieval_ab/legacy_kg"]
    hybrid_kg = labeled_outcomes["retrieval_ab/hybrid_kg"]

    single_vs_full = ground_truth_agreement(single, full)
    noise_floor = ground_truth_agreement(full, no_kg)

    kg_pairs = {
        "no_kg_vs_legacy_kg": ground_truth_agreement(no_kg, legacy_kg),
        "no_kg_vs_hybrid_kg": ground_truth_agreement(no_kg, hybrid_kg),
        "legacy_kg_vs_hybrid_kg": ground_truth_agreement(legacy_kg, hybrid_kg),
    }

    return {
        "single_shot_vs_full_retry": single_vs_full,
        "same_config_cross_run_noise_floor": {
            "description": (
                "cross_model_run's verityai_full and retrieval_ab's no_kg are the SAME "
                "configuration (full retry loop, zero KG context) run on different days -- "
                "this estimates how much task-level disagreement to expect from LLM sampling "
                "variance ALONE (temperature=0.7), with zero mechanism difference between them."
            ),
            **noise_floor,
        },
        "kg_context_pairwise": kg_pairs,
    }


def _print_reliability_table(label: str, diagram: dict) -> None:
    print(
        f"\n{label} (n={diagram['n_scored']}, {diagram['n_excluded_novel']} novel excluded, ECE={diagram['ece']})"
    )
    for row in diagram["bins"]:
        if row["count"] == 0:
            print(f"  {row['range']}: (empty)")
        else:
            print(
                f"  {row['range']}: n={row['count']}, mean_confidence={row['mean_confidence']}, "
                f"empirical_accuracy={row['empirical_accuracy']}"
            )


def main() -> int:
    labeled_outcomes = load_labeled_outcomes()

    t1_report = analyze_t1_calibration(labeled_outcomes)
    print("=" * 70)
    print("T1 -- Confidence calibration (reliability diagrams + ECE)")
    print("=" * 70)
    for label, diagram in t1_report.items():
        _print_reliability_table(label, diagram)

    t2_report = analyze_t2_retry_tradeoff(labeled_outcomes)
    print()
    print("=" * 70)
    print("T2/T7 -- Retry-loop trade-off, ground-truth-controlled")
    print("=" * 70)
    print(json.dumps(t2_report, indent=2))

    output_dir = REPO_ROOT / "docs" / "results"
    output_path = output_dir / "2026-07-15_t1_t2_analysis.json"
    output_path.write_text(
        json.dumps({"t1_calibration": t1_report, "t2_retry_tradeoff": t2_report}, indent=2)
    )
    print(f"\nWrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
