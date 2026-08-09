"""Tests for the deterministic narration templates.

These assert on exact substrings rather than whole sentences so wording can
be polished without churn -- except where the wording itself is the
requirement (the NOT_VERIFIED honesty guard).
"""

import pytest

from verityai.agent import events
from verityai.agent.event_narration import narrate


def test_unknown_event_type_does_not_raise():
    assert narrate("something_nobody_defined") == "Something nobody defined."


def test_missing_data_does_not_raise():
    """Every template must tolerate an empty data dict."""
    for event_type in events.EVENT_TYPES:
        assert isinstance(narrate(event_type, {}), str)
        assert narrate(event_type, {}) != ""


def test_retrieval_hybrid_reports_count_and_similarity():
    message = narrate(
        events.RETRIEVAL_COMPLETED,
        {"strategy": "hybrid", "rule_count": 5, "top_semantic_similarity": 0.8123},
    )
    assert "5 rule(s)" in message
    assert "0.81" in message


def test_retrieval_degraded_names_the_reason_and_does_not_claim_semantic_ranking():
    message = narrate(
        events.RETRIEVAL_COMPLETED,
        {
            "strategy": "hybrid",
            "rule_count": 3,
            "degraded_reason": "no embedding function",
        },
    )
    assert "no embedding function" in message
    assert "keyword overlap only" in message


def test_retrieval_legacy_does_not_claim_relevance():
    """Legacy fetch-all is prompt-agnostic -- saying 'matching your prompt' would be a lie."""
    message = narrate(events.RETRIEVAL_COMPLETED, {"strategy": "legacy", "rule_count": 12})
    assert "not ranked against your prompt" in message
    assert "matching your prompt" not in message


def test_verification_fail_renders_the_counterexample_inputs():
    message = narrate(
        events.VERIFICATION_COMPLETED,
        {
            "status": "fail",
            "counterexamples": [
                {"rule": "no_negative_index", "counterexample_inputs": {"x": -1, "y": 0}}
            ],
        },
    )
    assert "Z3 found a case where this fails: x=-1, y=0." in message
    assert "no_negative_index" in message


def test_verification_fail_without_counterexample_says_so_plainly():
    message = narrate(events.VERIFICATION_COMPLETED, {"status": "fail", "counterexamples": []})
    assert "self-contradictory" in message


def test_not_verified_is_never_phrased_as_a_pass():
    """The load-bearing honesty guard: NOT_VERIFIED must read as weaker than a pass."""
    message = narrate(
        events.VERIFICATION_COMPLETED,
        {"status": "not_verified", "non_verifiable_count": 4},
    )
    assert "NOT a pass" in message
    assert "no proof" in message
    assert "4 construct(s)" in message


@pytest.mark.parametrize("status", ["timeout", "unknown"])
def test_timeout_and_unknown_are_not_presented_as_passes(status):
    message = narrate(events.VERIFICATION_COMPLETED, {"status": status})
    assert "not as a pass" in message


def test_confidence_breakdown_shows_every_weighted_component():
    message = narrate(
        events.CONFIDENCE_COMPUTED,
        {
            "total": 0.55,
            "components": {
                "verification": 1.0,
                "pattern_similarity": 0.2,
                "complexity": 0.0,
                "test_coverage": 0.0,
            },
            "weights": {
                "verification": 0.5,
                "pattern_similarity": 0.25,
                "complexity": 0.15,
                "test_coverage": 0.1,
            },
        },
    )
    assert "Confidence 55%" in message
    assert "verification 100%x50%" in message
    assert "pattern similarity 20%x25%" in message


def test_retry_includes_the_failure_reason_fed_back_into_the_prompt():
    message = narrate(
        events.RETRY_SCHEDULED,
        {"next_attempt_number": 2, "failure_reason": "index can be negative"},
    )
    assert "attempt 2" in message
    assert "index can be negative" in message


def test_narration_never_imports_the_neural_layer():
    """Structural guard: narration is templates, never a second LLM call."""
    import verityai.agent.event_narration as module

    source = open(module.__file__).read()
    assert "neural" not in source.replace("# ", "").split('"""')[-1]
