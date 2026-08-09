"""Tests for the opt-in stage-event instrumentation on Orchestrator.run().

The load-bearing guarantee here is that instrumentation is *invisible* when
nobody asks for it: `tests/unit/test_orchestrator.py` is the regression gate
for behaviour, and these tests cover the emitter path on top of it.
"""

import json
from uuid import uuid4

from tests.fakes import AlwaysFailingLLMClient, FakeLLMClient, wrap_code
from verityai.agent import events
from verityai.agent.orchestrator import Orchestrator
from verityai.ontology.models import GenerationRequest

PASSING_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
CONTRADICTORY_CODE = "def bad(x: int) -> int:\n    assert x > 0\n    assert x < 0\n    return x\n"


def _collect(llm, prompt="write an add function", max_attempts=1, request_id=None):
    seen: list = []
    orchestrator = Orchestrator(llm_client=llm)
    response = orchestrator.run(
        GenerationRequest(prompt=prompt, max_attempts=max_attempts),
        emit=seen.append,
        request_id=request_id,
    )
    return response, seen


def _types(seen):
    return [event.type for event in seen]


class TestEventSequence:
    def test_single_passing_attempt_emits_the_full_ordered_sequence(self):
        _, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]))
        assert _types(seen) == [
            events.RUN_STARTED,
            events.RETRIEVAL_STARTED,
            events.RETRIEVAL_COMPLETED,
            events.ATTEMPT_STARTED,
            events.GENERATION_COMPLETED,
            events.VERIFICATION_STARTED,
            events.VERIFICATION_COMPLETED,
            events.CONFIDENCE_COMPUTED,
            events.ATTEMPT_COMPLETED,
            events.RUN_COMPLETED,
        ]

    def test_a_failing_attempt_schedules_a_retry_and_repeats_the_attempt_block(self):
        llm = FakeLLMClient([wrap_code(CONTRADICTORY_CODE), wrap_code(PASSING_CODE)])
        _, seen = _collect(llm, max_attempts=2)
        types = _types(seen)

        assert types.count(events.ATTEMPT_STARTED) == 2
        assert types.count(events.VERIFICATION_COMPLETED) == 2
        # A retry is announced between the two attempts, not after the last.
        assert types.count(events.RETRY_SCHEDULED) == 1
        assert types.index(events.RETRY_SCHEDULED) < types.index(events.RUN_COMPLETED)
        assert types[-1] == events.RUN_COMPLETED

    def test_no_retry_scheduled_when_the_attempt_budget_is_exhausted(self):
        """The last failed attempt must not promise a retry that will never happen."""
        _, seen = _collect(FakeLLMClient([wrap_code(CONTRADICTORY_CODE)]), max_attempts=1)
        assert events.RETRY_SCHEDULED not in _types(seen)

    def test_attempt_numbers_increment_across_retries(self):
        llm = FakeLLMClient([wrap_code(CONTRADICTORY_CODE), wrap_code(PASSING_CODE)])
        _, seen = _collect(llm, max_attempts=2)
        starts = [e.attempt_number for e in seen if e.type == events.ATTEMPT_STARTED]
        assert starts == [1, 2]

    def test_unreachable_llm_emits_run_failed_and_no_run_completed(self):
        _, seen = _collect(AlwaysFailingLLMClient())
        types = _types(seen)
        assert types[-1] == events.RUN_FAILED
        assert events.RUN_COMPLETED not in types
        assert "Connection refused" in seen[-1].data["error"]


class TestEventPayloads:
    def test_every_emitted_event_is_json_serializable(self):
        """Guards the silent-drop failure mode: model_dump_json runs inside the SSE layer."""
        llm = FakeLLMClient([wrap_code(CONTRADICTORY_CODE), wrap_code(PASSING_CODE)])
        _, seen = _collect(llm, max_attempts=2)
        for event in seen:
            json.loads(event.model_dump_json())

    def test_verification_event_carries_counterexample_detail_for_narration(self):
        _, seen = _collect(FakeLLMClient([wrap_code(CONTRADICTORY_CODE)]))
        verification = [e for e in seen if e.type == events.VERIFICATION_COMPLETED][0]
        assert verification.data["status"] == "fail"
        assert "counterexamples" in verification.data
        # Narration must be a real sentence, not the generic fallback.
        assert verification.message.startswith("Z3 ")

    def test_attempt_completed_carries_a_rehydratable_trace(self):
        """The API layer persists mid-run from this payload, so it must round-trip."""
        from verityai.ontology.models import ReasoningTrace

        _, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]))
        completed = [e for e in seen if e.type == events.ATTEMPT_COMPLETED][0]
        trace = ReasoningTrace.model_validate(completed.data["trace"])
        assert trace.generated_code.strip() == PASSING_CODE.strip()
        assert str(trace.id) == completed.data["trace_id"]

    def test_every_event_has_a_non_empty_narration(self):
        _, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]))
        assert all(event.message for event in seen)

    def test_elapsed_seconds_is_monotonic_across_the_run(self):
        _, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]))
        elapsed = [event.elapsed_seconds for event in seen]
        assert elapsed == sorted(elapsed)


class TestRunIdAndBackwardCompatibility:
    def test_caller_supplied_request_id_is_used_throughout(self):
        """The live UI needs the stream URL before the run starts, so it owns the id."""
        run_id = uuid4()
        response, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]), request_id=run_id)
        assert response.request_id == run_id
        assert all(event.run_id == run_id for event in seen)
        assert all(t.request_id == run_id for t in response.traces)

    def test_omitting_request_id_still_generates_one(self):
        response, seen = _collect(FakeLLMClient([wrap_code(PASSING_CODE)]))
        assert response.request_id is not None
        assert seen[0].run_id == response.request_id

    def test_run_without_an_emitter_behaves_identically(self):
        """The five existing callers pass neither kwarg and must be unaffected."""
        request = GenerationRequest(prompt="write an add function", max_attempts=1)
        plain = Orchestrator(llm_client=FakeLLMClient([wrap_code(PASSING_CODE)])).run(request)
        instrumented = Orchestrator(llm_client=FakeLLMClient([wrap_code(PASSING_CODE)])).run(
            request, emit=lambda e: None
        )
        assert plain.status == instrumented.status
        assert plain.code == instrumented.code
        assert plain.confidence == instrumented.confidence
        assert len(plain.traces) == len(instrumented.traces)

    def test_an_emitter_that_raises_does_not_break_the_run(self):
        """A broken UI listener must never fail a generation."""

        def exploding_emitter(event):
            raise RuntimeError("listener is on fire")

        orchestrator = Orchestrator(llm_client=FakeLLMClient([wrap_code(PASSING_CODE)]))
        response = orchestrator.run(
            GenerationRequest(prompt="write an add function", max_attempts=1),
            emit=exploding_emitter,
        )
        assert response.status == "success"
        assert response.code.strip() == PASSING_CODE.strip()
