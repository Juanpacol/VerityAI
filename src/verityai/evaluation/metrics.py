"""Metrics for comparing baseline configurations against benchmark ground truth.

Ground truth per outcome is derived *after* generation by comparing the
baseline's final code against the task's known reference_solution /
known_buggy_variant (see evaluation/baselines.py's classify_ground_truth) --
"correct", "buggy", or "novel" if the output matches neither (a live LLM
won't reliably reproduce either fixed string verbatim; "novel" keeps that
case from silently corrupting the confusion matrix).

Positive class for the confusion matrix is "flagged as buggy"
(VerificationStatus.FAIL), the standard framing for a bug/vuln detector:
recall answers "what fraction of real bugs did we catch?" and precision
answers "when we flag something, how often is it really buggy?"

Two things are excluded from the confusion matrix and reported as their
own rates instead of being forced into TP/FP/FN/TN:
- Predicted abstentions (UNKNOWN/TIMEOUT/NOT_VERIFIED) -- ADR-0001's
  "degrade explicitly" principle means a case the system declined to
  judge shouldn't silently reward or punish it.
- "novel" ground truth -- we have no oracle for code that doesn't match
  either known variant, so it can't be scored as right or wrong either.
"""

from dataclasses import dataclass

from verityai.ontology.models import VerificationStatus


@dataclass
class BenchmarkOutcome:
    """One baseline's result on one benchmark task."""

    task_id: str
    ground_truth: str  # "correct", "buggy", or "novel"
    predicted_status: VerificationStatus
    confidence: float
    latency_seconds: float
    attempts: int


def _is_abstention(status: VerificationStatus) -> bool:
    return status in (
        VerificationStatus.UNKNOWN,
        VerificationStatus.TIMEOUT,
        VerificationStatus.NOT_VERIFIED,
    )


def compute_classification_metrics(outcomes: list[BenchmarkOutcome]) -> dict:
    """Accuracy/precision/recall/F1 + abstention/novel rates over a list of outcomes.

    Returns a dict with keys: accuracy, precision, recall, f1,
    abstention_rate, novel_rate, tp, fp, fn, tn, abstained, novel, total.
    """
    total = len(outcomes)
    if total == 0:
        return {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "abstention_rate": 0.0,
            "novel_rate": 0.0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "tn": 0,
            "abstained": 0,
            "novel": 0,
            "total": 0,
        }

    tp = fp = fn = tn = abstained = novel = 0
    for outcome in outcomes:
        if outcome.ground_truth == "novel":
            novel += 1
            continue
        if _is_abstention(outcome.predicted_status):
            abstained += 1
            continue
        flagged_buggy = outcome.predicted_status == VerificationStatus.FAIL
        if outcome.ground_truth == "buggy" and flagged_buggy:
            tp += 1
        elif outcome.ground_truth == "correct" and flagged_buggy:
            fp += 1
        elif outcome.ground_truth == "buggy" and not flagged_buggy:
            fn += 1
        else:
            tn += 1

    judged = tp + fp + fn + tn
    accuracy = (tp + tn) / judged if judged else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "abstention_rate": abstained / total,
        "novel_rate": novel / total,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "abstained": abstained,
        "novel": novel,
        "total": total,
    }


def confidence_distribution(outcomes: list[BenchmarkOutcome]) -> dict:
    """min/max/mean over outcome confidence scores."""
    if not outcomes:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    values = [o.confidence for o in outcomes]
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}


def latency_distribution(outcomes: list[BenchmarkOutcome]) -> dict:
    """min/max/mean/total over outcome latency in seconds."""
    if not outcomes:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "total": 0.0}
    values = [o.latency_seconds for o in outcomes]
    return {
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "total": sum(values),
    }
