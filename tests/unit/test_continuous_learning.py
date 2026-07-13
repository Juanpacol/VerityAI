"""Unit tests for the continuous learning feedback loop (agent/continuous_learning.py)."""

from uuid import uuid4

import pytest

from verityai.agent.continuous_learning import FeedbackStore, derive_candidate_rule
from verityai.ontology.models import Feedback, FeedbackType, ReasoningTrace


def make_trace() -> ReasoningTrace:
    return ReasoningTrace(
        user_prompt="write a divide function",
        generated_code="def divide(a, b):\n    return a / b",
        attempt_number=1,
        kg_context={},
        llm_reasoning="",
        confidence_score=0.8,
    )


class TestFeedbackStore:
    def test_record_then_for_trace_returns_it(self):
        store = FeedbackStore()
        trace_id = uuid4()
        feedback = Feedback(trace_id=trace_id, feedback_type=FeedbackType.ACCEPT)

        store.record(feedback)

        assert store.for_trace(trace_id) == [feedback]

    def test_for_trace_excludes_other_traces(self):
        store = FeedbackStore()
        store.record(Feedback(trace_id=uuid4(), feedback_type=FeedbackType.ACCEPT))
        target_id = uuid4()
        target_feedback = Feedback(trace_id=target_id, feedback_type=FeedbackType.REJECT)
        store.record(target_feedback)

        assert store.for_trace(target_id) == [target_feedback]

    def test_all_returns_every_recorded_event_in_order(self):
        store = FeedbackStore()
        f1 = Feedback(trace_id=uuid4(), feedback_type=FeedbackType.ACCEPT)
        f2 = Feedback(trace_id=uuid4(), feedback_type=FeedbackType.REJECT)

        store.record(f1)
        store.record(f2)

        assert store.all() == [f1, f2]


class TestDeriveCandidateRule:
    def test_accept_feedback_yields_no_candidate(self):
        trace = make_trace()
        feedback = Feedback(trace_id=trace.id, feedback_type=FeedbackType.ACCEPT)

        assert derive_candidate_rule(feedback, trace) is None

    def test_reject_without_reason_yields_no_candidate(self):
        trace = make_trace()
        feedback = Feedback(trace_id=trace.id, feedback_type=FeedbackType.REJECT)

        assert derive_candidate_rule(feedback, trace) is None

    def test_reject_with_reason_yields_candidate_rule(self):
        trace = make_trace()
        feedback = Feedback(
            trace_id=trace.id,
            feedback_type=FeedbackType.REJECT,
            reason="Division by zero not guarded",
        )

        rule = derive_candidate_rule(feedback, trace)

        assert rule is not None
        assert rule.category == "learned"
        assert rule.description == "Division by zero not guarded"
        assert rule.examples["trace_id"] == str(trace.id)
        assert rule.examples["rejected_code"] == trace.generated_code

    def test_correct_with_reason_includes_corrected_code_in_examples(self):
        trace = make_trace()
        feedback = Feedback(
            trace_id=trace.id,
            feedback_type=FeedbackType.CORRECT,
            reason="Missing zero check",
            corrected_code="def divide(a, b):\n    assert b != 0\n    return a / b",
        )

        rule = derive_candidate_rule(feedback, trace)

        assert rule is not None
        assert rule.examples["corrected_code"] == feedback.corrected_code

    def test_correct_without_reason_yields_no_candidate(self):
        trace = make_trace()
        feedback = Feedback(
            trace_id=trace.id,
            feedback_type=FeedbackType.CORRECT,
            corrected_code="def divide(a, b):\n    assert b != 0\n    return a / b",
        )

        assert derive_candidate_rule(feedback, trace) is None
