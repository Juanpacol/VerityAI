"""Markdown comparison report for the 3-baseline evaluation.

Renders whatever BenchmarkOutcome data it's given -- it has no opinion on
whether that data came from a live Ollama run or a scripted test double.
See docs/PHASE_3_METHODOLOGY.md for why no real llama2:13b numbers ship
with this repo yet.
"""

from verityai.evaluation.metrics import (
    BenchmarkOutcome,
    compute_classification_metrics,
    confidence_distribution,
    latency_distribution,
)

BASELINE_DISPLAY_NAMES = {
    "raw_llm": "Raw LLM (no verification)",
    "single_shot_z3": "LLM + Z3 (no retry)",
    "verityai_full": "VerityAI (full retry loop)",
}


def render_comparison_report(results: dict[str, list[BenchmarkOutcome]]) -> str:
    """Render a markdown table comparing accuracy/precision/recall/F1/latency
    across whichever baselines are present in `results`."""
    lines = [
        "# VerityAI Baseline Comparison",
        "",
        "| Baseline | Accuracy | Precision | Recall | F1 | Abstention | Novel | Avg Confidence | Avg Latency (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for name, outcomes in results.items():
        metrics = compute_classification_metrics(outcomes)
        confidence = confidence_distribution(outcomes)
        latency = latency_distribution(outcomes)
        display_name = BASELINE_DISPLAY_NAMES.get(name, name)

        lines.append(
            f"| {display_name} | {metrics['accuracy']:.1%} | {metrics['precision']:.1%} | "
            f"{metrics['recall']:.1%} | {metrics['f1']:.1%} | {metrics['abstention_rate']:.1%} | "
            f"{metrics['novel_rate']:.1%} | {confidence['mean']:.2f} | {latency['mean']:.3f} |"
        )

    return "\n".join(lines)
