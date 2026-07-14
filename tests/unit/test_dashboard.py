"""Unit tests for evaluation/dashboard.py."""

from verityai.evaluation.dashboard import render_html_dashboard
from verityai.evaluation.metrics import BenchmarkOutcome
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


class TestRenderHtmlDashboard:
    def test_includes_data_source_note(self):
        results = {"raw_llm": [outcome("correct", VerificationStatus.PASS)]}
        html_out = render_html_dashboard(results, data_source_note="Simulated test data")
        assert "Simulated test data" in html_out

    def test_note_is_html_escaped(self):
        results = {"raw_llm": [outcome("correct", VerificationStatus.PASS)]}
        html_out = render_html_dashboard(results, data_source_note="<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in html_out
        assert "&lt;script&gt;" in html_out

    def test_includes_all_present_baselines_in_legend_and_table(self):
        results = {
            "raw_llm": [outcome("buggy", VerificationStatus.PASS)],
            "verityai_full": [outcome("buggy", VerificationStatus.FAIL)],
        }
        html_out = render_html_dashboard(results, data_source_note="note")
        assert "Raw LLM (no verification)" in html_out
        assert "VerityAI (full retry loop)" in html_out
        assert "LLM + Z3 (no retry)" not in html_out  # not present in results

    def test_produces_two_svg_charts_and_a_table(self):
        results = {"raw_llm": [outcome("correct", VerificationStatus.PASS)]}
        html_out = render_html_dashboard(results, data_source_note="note")
        assert html_out.count("<svg") == 2
        assert "<table" in html_out

    def test_single_baseline_does_not_crash(self):
        results = {"verityai_full": [outcome("correct", VerificationStatus.PASS)]}
        html_out = render_html_dashboard(results, data_source_note="note")
        assert "VerityAI (full retry loop)" in html_out

    def test_empty_results_does_not_crash(self):
        html_out = render_html_dashboard({}, data_source_note="note")
        assert "<table" in html_out

    def test_is_self_contained_no_external_resources(self):
        results = {"raw_llm": [outcome("correct", VerificationStatus.PASS)]}
        html_out = render_html_dashboard(results, data_source_note="note")
        assert "http://" not in html_out
        assert "https://" not in html_out
        assert "<link" not in html_out
