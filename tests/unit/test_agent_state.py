"""Unit tests for AgentState (orchestration state machine)."""

from verityai.agent.state import AgentState
from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus


def make_result(status: VerificationStatus, with_violation: bool = False) -> VerificationResult:
    violations = []
    if with_violation:
        violations = [
            Counterexample(
                rule_id="test_rule",
                input_values={"idx": -1},
                description="Negative index",
            )
        ]
    return VerificationResult(code_id="test", status=status, confidence=0.8, violations=violations)


class TestAgentStateBasics:
    def test_initial_state(self):
        state = AgentState(user_prompt="write a sort function")

        assert state.attempt_number == 0
        assert state.max_attempts == 3
        assert state.current_code is None
        assert state.history == []
        assert not state.is_exhausted
        assert not state.is_verified

    def test_custom_max_attempts(self):
        state = AgentState(user_prompt="test", max_attempts=5)
        assert state.max_attempts == 5


class TestAgentStateExhaustion:
    def test_not_exhausted_before_max_attempts(self):
        state = AgentState(user_prompt="test", max_attempts=3)
        state.record_attempt("code", {}, "reasoning", make_result(VerificationStatus.FAIL), 0.1)
        assert not state.is_exhausted

    def test_exhausted_at_max_attempts(self):
        state = AgentState(user_prompt="test", max_attempts=2)
        state.record_attempt("code1", {}, "r1", make_result(VerificationStatus.FAIL), 0.1)
        state.record_attempt("code2", {}, "r2", make_result(VerificationStatus.FAIL), 0.1)
        assert state.is_exhausted


class TestAgentStateVerification:
    def test_is_verified_true_on_pass(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("code", {}, "reasoning", make_result(VerificationStatus.PASS), 0.9)
        assert state.is_verified

    def test_is_verified_false_on_fail(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("code", {}, "reasoning", make_result(VerificationStatus.FAIL), 0.1)
        assert not state.is_verified

    def test_is_verified_false_on_unknown(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("code", {}, "reasoning", make_result(VerificationStatus.UNKNOWN), 0.3)
        assert not state.is_verified


class TestAgentStateHistory:
    def test_record_attempt_appends_to_history(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("code1", {}, "r1", make_result(VerificationStatus.FAIL), 0.1)
        state.record_attempt("code2", {}, "r2", make_result(VerificationStatus.PASS), 0.9)

        assert len(state.history) == 2
        assert state.history[0].attempt_number == 1
        assert state.history[1].attempt_number == 2

    def test_current_code_updates_each_attempt(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("first_code", {}, "r", make_result(VerificationStatus.FAIL), 0.1)
        assert state.current_code == "first_code"

        state.record_attempt("second_code", {}, "r", make_result(VerificationStatus.PASS), 0.9)
        assert state.current_code == "second_code"

    def test_trace_carries_kg_context_and_reasoning(self):
        state = AgentState(user_prompt="test")
        kg_context = {"rules": [{"name": "bounds_check"}]}
        trace = state.record_attempt(
            "code", kg_context, "step by step reasoning", make_result(VerificationStatus.PASS), 0.9
        )

        assert trace.kg_context == kg_context
        assert trace.llm_reasoning == "step by step reasoning"


class TestAgentStateFailureReason:
    def test_failure_reason_set_after_fail(self):
        state = AgentState(user_prompt="test")
        state.record_attempt(
            "code", {}, "r", make_result(VerificationStatus.FAIL, with_violation=True), 0.1
        )

        assert state.last_failure_reason is not None
        assert "Negative index" in state.last_failure_reason

    def test_failure_reason_cleared_after_pass(self):
        state = AgentState(user_prompt="test")
        state.record_attempt(
            "code1", {}, "r1", make_result(VerificationStatus.FAIL, with_violation=True), 0.1
        )
        assert state.last_failure_reason is not None

        state.record_attempt("code2", {}, "r2", make_result(VerificationStatus.PASS), 0.9)
        assert state.last_failure_reason is None

    def test_failure_reason_falls_back_to_status_without_violations(self):
        state = AgentState(user_prompt="test")
        state.record_attempt("code", {}, "r", make_result(VerificationStatus.UNKNOWN), 0.3)

        assert state.last_failure_reason is not None
        assert "unknown" in state.last_failure_reason.lower()

    def test_previous_trace_captures_prior_failure_reason(self):
        """Trace[i] should record the failure reason that was injected INTO that attempt's prompt."""
        state = AgentState(user_prompt="test")
        state.record_attempt(
            "code1", {}, "r1", make_result(VerificationStatus.FAIL, with_violation=True), 0.1
        )
        trace2 = state.record_attempt("code2", {}, "r2", make_result(VerificationStatus.PASS), 0.9)

        # trace2.failure_reason reflects what was known BEFORE attempt 2 ran
        assert trace2.failure_reason is not None
        assert "Negative index" in trace2.failure_reason
