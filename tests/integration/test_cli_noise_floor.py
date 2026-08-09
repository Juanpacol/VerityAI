"""End-to-end tests for `verity noise-floor`."""

import json

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


def write_repeats(path, values, key="success"):
    path.write_text(json.dumps([{key: v} for v in values]))


class TestNoiseFloor:
    def test_a_real_difference_is_reported(self, tmp_path):
        within = tmp_path / "within.json"
        between = tmp_path / "between.json"
        write_repeats(within, [0.6, 0.7, 0.65, 0.7, 0.6])
        write_repeats(between, [1.0, 1.0, 0.95, 1.0, 1.0])

        result = runner.invoke(
            app, ["noise-floor", str(within), str(between), "--metric", "success"]
        )

        assert result.exit_code == 0
        assert "likely_real_difference" in result.output

    def test_indistinguishable_from_noise_is_reported(self, tmp_path):
        within = tmp_path / "within.json"
        between = tmp_path / "between.json"
        write_repeats(within, [0.6, 0.8, 0.7])
        write_repeats(between, [0.65, 0.75])

        result = runner.invoke(
            app, ["noise-floor", str(within), str(between), "--metric", "success"]
        )

        assert result.exit_code == 0
        assert "indistinguishable_from_noise" in result.output

    def test_a_single_within_repeat_is_insufficient_and_exits_nonzero(self, tmp_path):
        within = tmp_path / "within.json"
        between = tmp_path / "between.json"
        write_repeats(within, [0.7])
        write_repeats(between, [1.0])

        result = runner.invoke(
            app, ["noise-floor", str(within), str(between), "--metric", "success"]
        )

        assert result.exit_code == 1
        assert "insufficient_data" in result.output

    def test_reports_both_summaries_before_the_verdict(self, tmp_path):
        within = tmp_path / "within.json"
        between = tmp_path / "between.json"
        write_repeats(within, [0.5, 0.6])
        write_repeats(between, [0.9, 0.95])

        result = runner.invoke(
            app, ["noise-floor", str(within), str(between), "--metric", "success"]
        )

        assert "WITHIN" in result.output
        assert "BETWEEN" in result.output
        assert result.output.index("WITHIN") < result.output.index("BETWEEN")
        assert result.output.index("BETWEEN") < result.output.index("conclusion")
