"""Unit tests for evaluation/repetition.py -- the noise-floor check that
was originally a one-off script computation (Fase 1 of the T1-T6 research
roadmap), now a reusable, tested library.
"""

import pytest

from verityai.evaluation.metrics import BenchmarkOutcome
from verityai.evaluation.repetition import (
    ground_truth_agreement,
    is_difference_significant_vs_noise,
    pairwise_agreement_summary,
    summarize_metric_variance,
)
from verityai.ontology.models import VerificationStatus


def make_outcome(
    task_id,
    ground_truth="correct",
    status=VerificationStatus.PASS,
    confidence=0.5,
    latency=1.0,
    attempts=1,
):
    return BenchmarkOutcome(
        task_id=task_id,
        ground_truth=ground_truth,
        predicted_status=status,
        confidence=confidence,
        latency_seconds=latency,
        attempts=attempts,
    )


class TestGroundTruthAgreement:
    def test_identical_lists_fully_agree(self):
        a = [make_outcome("t1"), make_outcome("t2")]
        b = [make_outcome("t1"), make_outcome("t2")]

        result = ground_truth_agreement(a, b)

        assert result["n_common_tasks"] == 2
        assert result["ground_truth_agreement_rate"] == 1.0
        assert result["status_agreement_rate"] == 1.0
        assert result["status_diff_given_gt_agrees"] == 0

    def test_different_ground_truth_not_counted_as_status_diff_given_agree(self):
        a = [make_outcome("t1", ground_truth="correct", status=VerificationStatus.PASS)]
        b = [make_outcome("t1", ground_truth="buggy", status=VerificationStatus.FAIL)]

        result = ground_truth_agreement(a, b)

        assert result["ground_truth_agreement_rate"] == 0.0
        assert result["status_diff_given_gt_agrees"] == 0  # gt never agreed, so this can't count
        assert result["status_diff_total"] == 1  # but status did differ overall

    def test_same_ground_truth_different_status_counted(self):
        a = [make_outcome("t1", ground_truth="correct", status=VerificationStatus.PASS)]
        b = [make_outcome("t1", ground_truth="correct", status=VerificationStatus.NOT_VERIFIED)]

        result = ground_truth_agreement(a, b)

        assert result["ground_truth_agreement_rate"] == 1.0
        assert result["status_diff_given_gt_agrees"] == 1
        assert result["status_diff_given_gt_agrees_rate"] == 1.0

    def test_only_common_task_ids_considered(self):
        a = [make_outcome("t1"), make_outcome("t2")]
        b = [make_outcome("t1"), make_outcome("t3")]

        result = ground_truth_agreement(a, b)

        assert result["n_common_tasks"] == 1

    def test_empty_common_set_returns_none_rates(self):
        a = [make_outcome("t1")]
        b = [make_outcome("t2")]

        result = ground_truth_agreement(a, b)

        assert result["n_common_tasks"] == 0
        assert result["ground_truth_agreement_rate"] is None


class TestPairwiseAgreementSummary:
    def test_requires_at_least_two_repeats(self):
        with pytest.raises(ValueError):
            pairwise_agreement_summary([[make_outcome("t1")]])

    def test_two_repeats_one_pair(self):
        repeats = [[make_outcome("t1")], [make_outcome("t1")]]

        summary = pairwise_agreement_summary(repeats)

        assert summary["n_repeats"] == 2
        assert summary["n_pairs"] == 1
        assert summary["ground_truth_agreement_rate_mean"] == 1.0

    def test_three_repeats_three_pairs(self):
        repeats = [
            [make_outcome("t1", ground_truth="correct")],
            [make_outcome("t1", ground_truth="correct")],
            [make_outcome("t1", ground_truth="buggy")],
        ]

        summary = pairwise_agreement_summary(repeats)

        assert summary["n_pairs"] == 3
        assert summary["ground_truth_agreement_rate_min"] == 0.0
        assert summary["ground_truth_agreement_rate_max"] == 1.0


class TestSummarizeMetricVariance:
    def test_requires_at_least_one_repeat(self):
        with pytest.raises(ValueError):
            summarize_metric_variance([])

    def test_single_repeat_zero_stdev(self):
        repeats = [[make_outcome("t1", ground_truth="buggy", status=VerificationStatus.FAIL)]]

        summary = summarize_metric_variance(repeats)

        assert summary["n_repeats"] == 1
        assert summary["accuracy"]["stdev"] == 0.0
        assert len(summary["accuracy"]["values"]) == 1

    def test_variance_computed_across_repeats(self):
        repeats = [
            [
                make_outcome("t1", ground_truth="buggy", status=VerificationStatus.FAIL)
            ],  # tp -> acc 1.0
            [
                make_outcome("t1", ground_truth="buggy", status=VerificationStatus.PASS)
            ],  # fn -> acc 0.0
        ]

        summary = summarize_metric_variance(repeats)

        assert summary["accuracy"]["values"] == [1.0, 0.0]
        assert summary["accuracy"]["mean"] == 0.5
        assert summary["accuracy"]["stdev"] > 0.0


class TestIsDifferenceSignificantVsNoise:
    def test_no_within_config_data_is_insufficient(self):
        result = is_difference_significant_vs_noise([], 0.5)
        assert result["conclusion"] == "insufficient_data"

    def test_between_config_below_noise_floor_is_likely_real(self):
        result = is_difference_significant_vs_noise(
            within_config_agreement_rates=[0.69, 0.71, 0.70], between_config_agreement_rate=0.48
        )
        assert result["conclusion"] == "likely_real_difference"
        assert result["below_noise_floor"] is True

    def test_between_config_within_noise_floor_is_indistinguishable(self):
        result = is_difference_significant_vs_noise(
            within_config_agreement_rates=[0.69, 0.71, 0.70], between_config_agreement_rate=0.70
        )
        assert result["conclusion"] == "indistinguishable_from_noise"
        assert result["below_noise_floor"] is False

    def test_matches_the_original_t2_finding_numbers(self):
        # Real numbers from docs/PHASE_3_METHODOLOGY.md's Analysis section:
        # noise floor ~69.2%, single-shot-vs-full-retry was 71.4% (inside it).
        result = is_difference_significant_vs_noise(
            within_config_agreement_rates=[0.692], between_config_agreement_rate=0.714
        )
        assert result["conclusion"] == "indistinguishable_from_noise"

        # KG-context pairwise (no_kg vs legacy_kg) was 48.0% -- below the floor.
        result2 = is_difference_significant_vs_noise(
            within_config_agreement_rates=[0.692], between_config_agreement_rate=0.480
        )
        assert result2["conclusion"] == "likely_real_difference"
