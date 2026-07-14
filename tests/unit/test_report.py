"""Unit tests for evaluation/report.py."""

from verityai.evaluation.metrics import BenchmarkOutcome
from verityai.evaluation.report import render_comparison_report
from verityai.ontology.models import VerificationStatus


def outcome(ground_truth, status, confidence=1.0, latency=0.1):
    return BenchmarkOutcome(
        task_id="t",
        ground_truth=ground_truth,
        predicted_status=status,
        confidence=confidence,
        latency_seconds=latency,
        attempts=1,
    )


class TestRenderComparisonReport:
    def test_includes_a_row_per_baseline(self):
        results = {
            "raw_llm": [outcome("buggy", VerificationStatus.PASS)],
            "verityai_full": [outcome("buggy", VerificationStatus.FAIL)],
        }
        report = render_comparison_report(results)

        assert "Raw LLM (no verification)" in report
        assert "VerityAI (full retry loop)" in report

    def test_unknown_baseline_key_falls_back_to_raw_name(self):
        results = {"some_new_baseline": [outcome("correct", VerificationStatus.PASS)]}
        report = render_comparison_report(results)
        assert "some_new_baseline" in report

    def test_is_valid_markdown_table_structure(self):
        results = {"raw_llm": [outcome("correct", VerificationStatus.PASS)]}
        report = render_comparison_report(results)
        lines = report.strip().split("\n")
        assert lines[0].startswith("# ")
        assert "|---|" in lines[3] or lines[3].startswith("|---")
