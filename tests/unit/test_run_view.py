"""Unit tests for api/run_view.py's server-rendered reasoning-trace view.

Exercises render_run_view() directly against hand-built ReasoningTrace
objects, independent of the API layer (see test_api.py::TestRunsEndpoints
for the endpoint-level integration tests).
"""

from verityai.api.run_view import render_run_view
from verityai.ontology.models import (
    Counterexample,
    ReasoningTrace,
    VerificationResult,
    VerificationStatus,
)


def make_trace(**overrides) -> ReasoningTrace:
    defaults = dict(
        user_prompt="write a safe divide function",
        generated_code="x = 1\nassert x == 1",
        attempt_number=1,
        kg_context={},
        llm_reasoning="Simple assignment.",
        verification_result=VerificationResult(
            code_id="c1", status=VerificationStatus.PASS, confidence=0.9
        ),
        confidence_score=0.9,
    )
    defaults.update(overrides)
    return ReasoningTrace(**defaults)


class TestRenderRunViewBasics:
    def test_renders_prompt_and_attempt_count(self):
        traces = [make_trace(attempt_number=1)]
        html_out = render_run_view(traces)

        assert "write a safe divide function" in html_out
        assert "Attempt 1" in html_out

    def test_multiple_attempts_all_rendered(self):
        traces = [
            make_trace(
                attempt_number=1,
                verification_result=VerificationResult(
                    code_id="c1", status=VerificationStatus.FAIL, confidence=0.1
                ),
            ),
            make_trace(attempt_number=2),
        ]
        html_out = render_run_view(traces)

        assert "Attempt 1" in html_out
        assert "Attempt 2" in html_out

    def test_passing_attempt_does_not_show_prior_attempts_failure_as_its_own(self):
        """trace.failure_reason on attempt 2 is attempt 1's failure, carried
        forward as retry context -- it must not render as if attempt 2
        itself failed, since attempt 2's own status is PASS."""
        traces = [
            make_trace(
                attempt_number=1,
                verification_result=VerificationResult(
                    code_id="c1", status=VerificationStatus.FAIL, confidence=0.1
                ),
                confidence_score=0.1,
                failure_reason=None,
            ),
            make_trace(
                attempt_number=2,
                failure_reason="Verification status: fail",  # injected from attempt 1
            ),
        ]
        html_out = render_run_view(traces)

        assert "Retry context (previous failure): Verification status: fail" in html_out
        # The retry-context note must be attributed to attempt 2's card, not
        # rendered as an unlabeled error directly under the passing status.
        assert '<p class="error">Verification status: fail</p>' not in html_out

    def test_first_attempt_never_shows_retry_context(self):
        traces = [make_trace(attempt_number=1, failure_reason="should never show")]
        html_out = render_run_view(traces)

        assert "Retry context" not in html_out

    def test_escapes_html_in_generated_code(self):
        traces = [make_trace(generated_code="x = '<script>alert(1)</script>'")]
        html_out = render_run_view(traces)

        assert "<script>alert(1)</script>" not in html_out
        assert "&lt;script&gt;" in html_out


class TestRenderRunViewKGRetrieval:
    def test_no_kg_context_shows_explanatory_message(self):
        traces = [make_trace(kg_context={})]
        html_out = render_run_view(traces)

        assert "No KG context" in html_out

    def test_legacy_context_lists_rule_names(self):
        traces = [
            make_trace(
                kg_context={
                    "rules": [{"name": "no_null_deref", "description": "d"}],
                    "patterns": [],
                }
            )
        ]
        html_out = render_run_view(traces)

        assert "no_null_deref" in html_out
        assert "legacy" in html_out.lower()

    def test_hybrid_context_shows_method_badges_and_mode(self):
        traces = [
            make_trace(
                kg_context={
                    "rules": [
                        {
                            "name": "division_safety",
                            "description": "d",
                            "provenance": {
                                "method": "hybrid",
                                "lexical_rank": 1,
                                "semantic_rank": 1,
                                "fused_score": 0.033,
                            },
                        }
                    ],
                    "patterns": [],
                    "retrieval": {
                        "strategy": "hybrid",
                        "mode": "hybrid",
                        "query": "divide by zero",
                        "top_k": 8,
                        "degraded_reason": None,
                        "top_semantic_similarity": 1.0,
                    },
                }
            )
        ]
        html_out = render_run_view(traces)

        assert "division_safety" in html_out
        assert "badge-hybrid" in html_out
        assert "0.033" in html_out

    def test_degraded_reason_is_shown(self):
        traces = [
            make_trace(
                kg_context={
                    "rules": [],
                    "patterns": [],
                    "retrieval": {
                        "strategy": "hybrid",
                        "mode": "lexical_only",
                        "query": "q",
                        "top_k": 8,
                        "degraded_reason": "no embed_fn configured",
                        "top_semantic_similarity": None,
                    },
                }
            )
        ]
        html_out = render_run_view(traces)

        assert "no embed_fn configured" in html_out


class TestRenderRunViewZ3Panel:
    def test_pass_shows_no_counterexamples_message(self):
        traces = [make_trace()]
        html_out = render_run_view(traces)
        assert "No counterexamples" in html_out

    def test_violation_renders_description_and_counterexample(self):
        result = VerificationResult(
            code_id="c1",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            violations=[
                Counterexample(
                    rule_id="bounds_check",
                    input_values={"idx": -1},
                    description="Negative index out of bounds",
                )
            ],
        )
        traces = [
            make_trace(
                generated_code="arr[idx]",
                verification_result=result,
                confidence_score=0.0,
                failure_reason="Negative index out of bounds (counterexample: {'idx': -1})",
            )
        ]
        html_out = render_run_view(traces)

        assert "Negative index out of bounds" in html_out
        assert "idx" in html_out

    def test_no_verification_result_shows_message(self):
        traces = [make_trace(verification_result=None)]
        html_out = render_run_view(traces)
        assert "No verification result recorded" in html_out


class TestRenderRunViewConfidenceBreakdown:
    def test_no_factors_shows_message(self):
        traces = [make_trace(confidence_factors=None)]
        html_out = render_run_view(traces)
        assert "No factor breakdown recorded" in html_out

    def test_factors_render_bar_segments_and_legend(self):
        traces = [
            make_trace(
                confidence_factors={
                    "total": 0.75,
                    "components": {
                        "verification": 1.0,
                        "pattern_similarity": 1.0,
                        "complexity": 0.5,
                        "test_coverage": 0.0,
                    },
                    "weights": {
                        "verification": 0.50,
                        "pattern_similarity": 0.25,
                        "complexity": 0.15,
                        "test_coverage": 0.10,
                    },
                }
            )
        ]
        html_out = render_run_view(traces)

        assert "Total: 75" in html_out or "75.0%" in html_out
        assert "bar-segment" in html_out
        assert "Pattern similarity" in html_out


class TestRenderRunViewSelfContained:
    def test_no_external_resources(self):
        traces = [make_trace()]
        html_out = render_run_view(traces)

        assert "<script src" not in html_out
        assert "<link" not in html_out
        assert "url(" not in html_out

    def test_generated_code_containing_http_does_not_break_containment_check(self):
        """Generated code legitimately containing 'http://' must not be
        mistaken for an external resource reference."""
        traces = [make_trace(generated_code="url = 'http://example.com'")]
        html_out = render_run_view(traces)

        assert "http://example.com" in html_out  # escaped-but-present text
        assert "<script src" not in html_out
        assert "<link" not in html_out
