"""Tests for the noise-floor library.

This is the standing rule from T2 made executable: never attribute a metric
difference to a mechanism without a same-configuration repeat. Every test
here traces back to that rule -- `TestInsufficientData` in particular exists
because "insufficient data" must be a real, reachable outcome, not something
the code silently upgrades to a verdict when the input is too thin to support
one.
"""

import pytest

from verityai.bench.repetition import compare_to_noise_floor, summarize_metric_variance


class TestSummarizeMetricVariance:
    def test_computes_mean_and_stdev_per_metric(self):
        repeats = [{"success": 1.0}, {"success": 0.0}, {"success": 1.0}]

        summary = summarize_metric_variance(repeats)

        assert summary["n_repeats"] == 3
        assert summary["success"]["mean"] == pytest.approx(2 / 3, rel=1e-3)
        assert summary["success"]["n"] == 3

    def test_a_single_repeat_has_zero_stdev(self):
        summary = summarize_metric_variance([{"success": 1.0}])

        assert summary["success"]["stdev"] == 0.0

    def test_multiple_metric_keys_are_all_summarized(self):
        repeats = [
            {"success": 1.0, "tokens_saved": 800},
            {"success": 0.0, "tokens_saved": 200},
        ]

        summary = summarize_metric_variance(repeats)

        assert "success" in summary
        assert "tokens_saved" in summary
        assert summary["tokens_saved"]["mean"] == 500.0

    def test_a_metric_missing_from_some_repeats_is_summarized_over_what_reported_it(self):
        repeats = [{"success": 1.0, "tokens_saved": 800}, {"success": 0.0}]

        summary = summarize_metric_variance(repeats)

        assert summary["tokens_saved"]["n"] == 1
        assert summary["success"]["n"] == 2

    def test_min_and_max_are_reported_not_just_mean_and_stdev(self):
        """The protocol requires the range, not a point estimate."""
        repeats = [{"success": 0.0}, {"success": 1.0}, {"success": 1.0}]

        summary = summarize_metric_variance(repeats)

        assert summary["success"]["min"] == 0.0
        assert summary["success"]["max"] == 1.0

    def test_no_repeats_raises_rather_than_returning_a_fake_summary(self):
        with pytest.raises(ValueError):
            summarize_metric_variance([])


class TestCompareToNoiseFloor:
    def test_a_between_config_value_inside_the_floor_is_noise(self):
        within = [{"success": 0.6}, {"success": 0.8}, {"success": 0.7}]
        between = [{"success": 0.65}, {"success": 0.75}]

        result = compare_to_noise_floor(within, between, "success")

        assert result["conclusion"] == "indistinguishable_from_noise"
        assert result["outside_noise_floor"] is False

    def test_a_between_config_value_above_the_floor_is_a_real_difference(self):
        within = [{"success": 0.4}, {"success": 0.5}, {"success": 0.45}]
        between = [{"success": 0.95}, {"success": 1.0}]

        result = compare_to_noise_floor(within, between, "success")

        assert result["conclusion"] == "likely_real_difference"

    def test_a_between_config_value_below_the_floor_is_also_a_real_difference(self):
        """Unlike the original classification-only version, a real effect
        can go in either direction for an arbitrary metric."""
        within = [{"success": 0.9}, {"success": 0.85}, {"success": 0.95}]
        between = [{"success": 0.1}, {"success": 0.2}]

        result = compare_to_noise_floor(within, between, "success")

        assert result["conclusion"] == "likely_real_difference"

    def test_the_floor_is_a_range_not_a_mean(self):
        within = [{"success": 0.0}, {"success": 1.0}]
        between = [{"success": 0.5}]

        result = compare_to_noise_floor(within, between, "success")

        assert result["noise_floor_min"] == 0.0
        assert result["noise_floor_max"] == 1.0
        assert result["conclusion"] == "indistinguishable_from_noise"

    def test_reports_the_metric_name(self):
        within = [{"success": 0.5}, {"success": 0.6}]
        between = [{"success": 0.55}]

        assert compare_to_noise_floor(within, between, "success")["metric"] == "success"


class TestInsufficientData:
    """'Insufficient data' must be a real, reachable outcome."""

    def test_zero_within_repeats_is_insufficient(self):
        result = compare_to_noise_floor([], [{"success": 1.0}], "success")

        assert result["conclusion"] == "insufficient_data"

    def test_a_single_within_repeat_cannot_establish_a_range(self):
        result = compare_to_noise_floor([{"success": 0.5}], [{"success": 1.0}], "success")

        assert result["conclusion"] == "insufficient_data"
        assert ">=2" in result["reason"]

    def test_empty_between_repeats_is_insufficient(self):
        within = [{"success": 0.5}, {"success": 0.6}]

        result = compare_to_noise_floor(within, [], "success")

        assert result["conclusion"] == "insufficient_data"

    def test_a_metric_absent_from_within_repeats_is_insufficient(self):
        within = [{"other_metric": 1.0}, {"other_metric": 2.0}]
        between = [{"success": 1.0}]

        result = compare_to_noise_floor(within, between, "success")

        assert result["conclusion"] == "insufficient_data"

    def test_insufficient_data_never_reports_a_verdict(self):
        """A caller checking only 'conclusion' must never see
        likely_real_difference or indistinguishable_from_noise when the
        data couldn't actually support either."""
        result = compare_to_noise_floor([{"success": 1.0}], [], "success")

        assert result["conclusion"] not in (
            "likely_real_difference",
            "indistinguishable_from_noise",
        )
