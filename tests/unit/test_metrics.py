"""Unit tests for evaluation/metrics.py."""

from verityai.evaluation.metrics import (
    BenchmarkOutcome,
    compute_classification_metrics,
    confidence_distribution,
    latency_distribution,
)
from verityai.ontology.models import VerificationStatus


def outcome(ground_truth, status, confidence=1.0, latency=0.1, attempts=1, task_id="t"):
    return BenchmarkOutcome(
        task_id=task_id,
        ground_truth=ground_truth,
        predicted_status=status,
        confidence=confidence,
        latency_seconds=latency,
        attempts=attempts,
    )


class TestComputeClassificationMetrics:
    def test_empty_outcomes_returns_zeros(self):
        metrics = compute_classification_metrics([])
        assert metrics["total"] == 0
        assert metrics["accuracy"] == 0.0

    def test_perfect_detector(self):
        outcomes = [
            outcome("buggy", VerificationStatus.FAIL),
            outcome("correct", VerificationStatus.PASS),
        ]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["tp"] == 1
        assert metrics["tn"] == 1
        assert metrics["fp"] == 0
        assert metrics["fn"] == 0
        assert metrics["accuracy"] == 1.0
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0
        assert metrics["f1"] == 1.0

    def test_never_flags_anything_has_zero_recall(self):
        """Models baseline 1: it never returns FAIL, so it misses every bug."""
        outcomes = [
            outcome("buggy", VerificationStatus.PASS),
            outcome("correct", VerificationStatus.PASS),
        ]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["tp"] == 0
        assert metrics["fn"] == 1
        assert metrics["recall"] == 0.0
        assert metrics["accuracy"] == 0.5  # got the correct one right, missed the bug

    def test_false_positive_on_correct_code(self):
        outcomes = [outcome("correct", VerificationStatus.FAIL)]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["fp"] == 1
        assert metrics["precision"] == 0.0

    def test_abstentions_excluded_from_confusion_matrix_but_counted(self):
        outcomes = [
            outcome("buggy", VerificationStatus.UNKNOWN),
            outcome("correct", VerificationStatus.PASS),
        ]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["abstained"] == 1
        assert metrics["abstention_rate"] == 0.5
        assert metrics["tp"] + metrics["fp"] + metrics["fn"] + metrics["tn"] == 1

    def test_novel_ground_truth_excluded_from_confusion_matrix_but_counted(self):
        outcomes = [
            outcome("novel", VerificationStatus.PASS),
            outcome("correct", VerificationStatus.PASS),
        ]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["novel"] == 1
        assert metrics["novel_rate"] == 0.5
        assert metrics["tp"] + metrics["fp"] + metrics["fn"] + metrics["tn"] == 1

    def test_all_abstained_yields_zero_metrics_not_divide_by_zero_crash(self):
        outcomes = [outcome("buggy", VerificationStatus.TIMEOUT)]
        metrics = compute_classification_metrics(outcomes)
        assert metrics["accuracy"] == 0.0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0


class TestConfidenceDistribution:
    def test_computes_min_max_mean(self):
        outcomes = [
            outcome("correct", VerificationStatus.PASS, confidence=0.2),
            outcome("correct", VerificationStatus.PASS, confidence=0.8),
        ]
        dist = confidence_distribution(outcomes)
        assert dist["min"] == 0.2
        assert dist["max"] == 0.8
        assert dist["mean"] == 0.5

    def test_empty_outcomes_returns_zeros(self):
        assert confidence_distribution([]) == {"min": 0.0, "max": 0.0, "mean": 0.0}


class TestLatencyDistribution:
    def test_computes_min_max_mean_total(self):
        outcomes = [
            outcome("correct", VerificationStatus.PASS, latency=0.1),
            outcome("correct", VerificationStatus.PASS, latency=0.3),
        ]
        dist = latency_distribution(outcomes)
        assert dist["min"] == 0.1
        assert dist["max"] == 0.3
        assert round(dist["mean"], 2) == 0.2
        assert round(dist["total"], 2) == 0.4
