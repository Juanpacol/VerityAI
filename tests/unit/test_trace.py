"""Unit tests for ReasoningTrace persistence (agent/trace.py).

Uses an in-memory sqlite engine instead of Postgres — TraceStore takes an
injected SQLAlchemy Session, so these tests never touch the real database.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from verityai.agent.trace import TraceStore, serialize_trace, serialize_traces
from verityai.db.base import Base
from verityai.ontology.models import ReasoningTrace, VerificationResult, VerificationStatus


@pytest.fixture
def store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield TraceStore(session)
    session.close()


def make_trace(user_prompt="test prompt", attempt_number=1, status=VerificationStatus.PASS):
    return ReasoningTrace(
        user_prompt=user_prompt,
        generated_code="x = 1",
        attempt_number=attempt_number,
        kg_context={"rules": ["r1"]},
        llm_reasoning="Simple assignment.",
        verification_result=VerificationResult(code_id="c1", status=status, confidence=0.9),
        confidence_score=0.9,
    )


class TestTraceStoreSaveAndGet:
    def test_save_then_get_round_trips(self, store):
        trace = make_trace()
        store.save_trace(trace)

        fetched = store.get_trace(trace.id)

        assert fetched is not None
        assert fetched.id == trace.id
        assert fetched.user_prompt == trace.user_prompt
        assert fetched.generated_code == trace.generated_code
        assert fetched.verification_result.status == VerificationStatus.PASS
        assert fetched.confidence_score == pytest.approx(0.9)

    def test_get_missing_trace_returns_none(self, store):
        from uuid import uuid4

        assert store.get_trace(uuid4()) is None

    def test_save_upserts_existing_trace(self, store):
        trace = make_trace()
        store.save_trace(trace)

        trace.generated_code = "x = 2"
        store.save_trace(trace)

        fetched = store.get_trace(trace.id)
        assert fetched.generated_code == "x = 2"

    def test_trace_without_verification_result_round_trips(self, store):
        trace = ReasoningTrace(
            user_prompt="p",
            generated_code="x = 1",
            attempt_number=1,
            kg_context={},
            llm_reasoning="",
            verification_result=None,
            confidence_score=0.0,
        )
        store.save_trace(trace)

        fetched = store.get_trace(trace.id)
        assert fetched.verification_result is None


class TestTraceStoreQueryByPrompt:
    def test_returns_all_attempts_in_order(self, store):
        t1 = make_trace(user_prompt="shared", attempt_number=1, status=VerificationStatus.FAIL)
        t2 = make_trace(user_prompt="shared", attempt_number=2, status=VerificationStatus.PASS)
        store.save_traces([t1, t2])

        results = store.get_traces_by_prompt("shared")

        assert len(results) == 2
        assert [r.attempt_number for r in results] == [1, 2]

    def test_does_not_return_traces_for_other_prompts(self, store):
        store.save_trace(make_trace(user_prompt="a"))
        store.save_trace(make_trace(user_prompt="b"))

        assert len(store.get_traces_by_prompt("a")) == 1


class TestSerialization:
    def test_serialize_trace_is_valid_json_with_expected_fields(self):
        import json

        trace = make_trace()
        raw = serialize_trace(trace)
        parsed = json.loads(raw)

        assert parsed["user_prompt"] == "test prompt"
        assert parsed["confidence_score"] == pytest.approx(0.9)

    def test_serialize_traces_returns_json_array(self):
        import json

        traces = [make_trace(attempt_number=1), make_trace(attempt_number=2)]
        parsed = json.loads(serialize_traces(traces))

        assert len(parsed) == 2
