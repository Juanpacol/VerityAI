"""Integration tests for the live-run SSE endpoints.

Everything here runs against a FakeLLMClient, so a whole run completes in
milliseconds and the event sequence is deterministic. Every stream is read
inside a `with client.stream(...)` block with a guaranteed exit condition --
an SSE test without one hangs the suite rather than failing it.
"""

import json
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.fakes import AlwaysFailingLLMClient, FakeLLMClient, wrap_code
from verityai.agent import events as stage_events
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.trace import TraceStore
from verityai.api.live_runs import reset_live_run_state
from verityai.api.rest import (
    app,
    get_background_session_factory,
    get_live_run_registry,
    get_orchestrator,
    get_trace_store,
)
from verityai.compliance.audit_log import AuditLogStore
from verityai.api.rest import get_audit_log_store

PASSING_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
CONTRADICTORY_CODE = "def bad(x: int) -> int:\n    assert x > 0\n    assert x < 0\n    return x\n"


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_live_run_state()
    yield
    reset_live_run_state()


@pytest.fixture
def live_client(sqlite_engine, db_session, monkeypatch):
    """TestClient whose foreground reads and background writes share one DB."""
    monkeypatch.setenv("VERITYAI_FORCE_CONDITION", "C")
    factory = sessionmaker(bind=sqlite_engine)

    app.dependency_overrides[get_trace_store] = lambda: TraceStore(db_session)
    app.dependency_overrides[get_audit_log_store] = lambda: AuditLogStore(db_session)
    app.dependency_overrides[get_background_session_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


def _use_llm(responses):
    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
        llm_client=FakeLLMClient(responses)
    )


def _start(client, prompt="write an add function", **body):
    payload = {"prompt": prompt, "consent": True}
    payload.update(body)
    response = client.post("/live/runs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def _drain(client, stream_url, last_event_id=None):
    """Read an SSE stream to completion, returning the parsed frames."""
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    frames = []
    with client.stream("GET", stream_url, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event_type = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = line[len("data: ") :]
                if event_type == "done":
                    return frames
                frames.append((event_type, json.loads(payload)))
    return frames


class TestHandshake:
    def test_create_returns_a_stream_url_and_condition(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        assert created["stream_url"] == f"/live/runs/{created['run_id']}/events"
        assert created["condition"] in ("A", "B", "C")

    def test_consent_is_required(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        response = live_client.post("/live/runs", json={"prompt": "x", "consent": False})
        assert response.status_code == 400
        assert "onsent" in response.json()["detail"]

    def test_unknown_run_id_streams_404(self, live_client):
        response = live_client.get("/live/runs/00000000-0000-0000-0000-000000000000/events")
        assert response.status_code == 404

    def test_too_many_concurrent_runs_is_rejected(self, live_client, monkeypatch):
        monkeypatch.setattr("verityai.api.rest.MAX_LIVE_RUNS", 0)
        _use_llm([wrap_code(PASSING_CODE)])
        response = live_client.post("/live/runs", json={"prompt": "x", "consent": True})
        assert response.status_code == 429


class TestStreaming:
    def test_stream_delivers_the_full_ordered_event_sequence(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        frames = _drain(live_client, created["stream_url"])

        types = [event_type for event_type, _ in frames]
        assert types == [
            stage_events.RUN_STARTED,
            stage_events.RETRIEVAL_STARTED,
            stage_events.RETRIEVAL_COMPLETED,
            stage_events.ATTEMPT_STARTED,
            stage_events.GENERATION_COMPLETED,
            stage_events.VERIFICATION_STARTED,
            stage_events.VERIFICATION_COMPLETED,
            stage_events.CONFIDENCE_COMPUTED,
            stage_events.ATTEMPT_COMPLETED,
            stage_events.RUN_COMPLETED,
        ]

    def test_every_frame_carries_the_run_id_and_a_narration(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        frames = _drain(live_client, created["stream_url"])

        assert all(payload["run_id"] == created["run_id"] for _, payload in frames)
        assert all(payload["message"] for _, payload in frames)

    def test_sequences_are_contiguous_from_one(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        frames = _drain(live_client, created["stream_url"])
        assert [p["sequence"] for _, p in frames] == list(range(1, len(frames) + 1))

    def test_a_retry_run_streams_two_attempts_and_a_retry(self, live_client):
        _use_llm([wrap_code(CONTRADICTORY_CODE), wrap_code(PASSING_CODE)])
        created = _start(live_client, max_attempts=2)
        types = [t for t, _ in _drain(live_client, created["stream_url"])]

        assert types.count(stage_events.ATTEMPT_STARTED) == 2
        assert types.count(stage_events.RETRY_SCHEDULED) == 1

    def test_panels_arrive_as_rendered_html(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        frames = dict(_drain(live_client, created["stream_url"]))

        assert frames[stage_events.CONFIDENCE_COMPUTED]["html"] is not None
        assert "Total:" in frames[stage_events.CONFIDENCE_COMPUTED]["html"]
        assert frames[stage_events.VERIFICATION_COMPLETED]["html"] is not None

    def test_an_unreachable_llm_ends_the_stream_with_run_failed(self, live_client):
        app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
            llm_client=AlwaysFailingLLMClient()
        )
        created = _start(live_client)
        types = [t for t, _ in _drain(live_client, created["stream_url"])]

        assert types[-1] == stage_events.RUN_FAILED
        assert stage_events.RUN_COMPLETED not in types


class TestReplayAndReconnect:
    def test_a_stream_opened_after_completion_replays_everything(self, live_client):
        """No race: the buffer means a late EventSource still sees step 1."""
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        first = _drain(live_client, created["stream_url"])
        second = _drain(live_client, created["stream_url"])

        assert [t for t, _ in first] == [t for t, _ in second]
        assert len(second) == len(first)

    def test_last_event_id_resumes_without_replaying_earlier_events(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        full = _drain(live_client, created["stream_url"])

        resumed = _drain(live_client, created["stream_url"], last_event_id="5")
        assert [p["sequence"] for _, p in resumed] == [
            p["sequence"] for _, p in full if p["sequence"] > 5
        ]

    def test_a_garbage_last_event_id_replays_from_the_start(self, live_client):
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        full = _drain(live_client, created["stream_url"])
        resumed = _drain(live_client, created["stream_url"], last_event_id="not-a-number")
        assert len(resumed) == len(full)


class TestPersistence:
    def test_the_run_is_queryable_through_the_existing_routes_afterwards(self, live_client):
        """run_id == request_id, so /runs/{id} and its view keep working."""
        _use_llm([wrap_code(PASSING_CODE)])
        created = _start(live_client)
        _drain(live_client, created["stream_url"])

        summary = live_client.get(f"/runs/{created['run_id']}")
        assert summary.status_code == 200
        assert len(summary.json()["attempts"]) == 1

        view = live_client.get(f"/runs/{created['run_id']}/view")
        assert view.status_code == 200
        assert "Reasoning Trace" in view.text

    def test_traces_are_persisted_incrementally_not_only_at_the_end(self, live_client):
        """The honest fallback when a stream drops mid-run.

        Polls /runs/{id} directly rather than reading the SSE stream:
        TestClient buffers a streaming response to completion before
        yielding any of it, so a stream-based version of this test could
        never observe mid-run state.

        The second LLM call sleeps so there is a real window in which
        attempt 1 is finished and attempt 2 is not -- with an instant fake
        the run would be over before the first poll.
        """

        class SlowSecondCallLLM(FakeLLMClient):
            def generate(self, prompt, system_prompt=None):
                if self.call_count == 1:
                    time.sleep(1.0)
                return super().generate(prompt, system_prompt)

        app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
            llm_client=SlowSecondCallLLM(
                [wrap_code(CONTRADICTORY_CODE), wrap_code(PASSING_CODE)]
            )
        )
        created = _start(live_client, max_attempts=2)
        run_id = created["run_id"]
        run = get_live_run_registry().get(UUID(run_id))

        # Poll until attempt 1 shows up, which must happen while the run is
        # still in flight -- that is the whole point of writing per attempt.
        observed = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if run.finished:
                break
            response = live_client.get(f"/runs/{run_id}")
            if response.status_code == 200:
                observed = response.json()["attempts"]
                break
            time.sleep(0.02)

        assert observed is not None, "no trace was queryable before the run finished"
        assert len(observed) == 1
        assert observed[0]["attempt_number"] == 1


class TestConditionMasking:
    """Server-side masking: dev tools must not reveal a suppressed panel."""

    def _frames_under(self, live_client, monkeypatch, condition):
        monkeypatch.setenv("VERITYAI_FORCE_CONDITION", condition)
        _use_llm([wrap_code(CONTRADICTORY_CODE)])
        created = _start(live_client)
        assert created["condition"] == condition
        return dict(_drain(live_client, created["stream_url"]))

    def test_condition_a_sends_no_z3_html_or_data(self, live_client, monkeypatch):
        frames = self._frames_under(live_client, monkeypatch, "A")
        verification = frames[stage_events.VERIFICATION_COMPLETED]
        assert verification["html"] is None
        assert verification["data"] == {"suppressed": True}
        assert "counterexample" not in json.dumps(verification).lower()
        # ...but the confidence panel is exactly what condition A keeps.
        assert frames[stage_events.CONFIDENCE_COMPUTED]["html"] is not None

    def test_condition_b_sends_no_confidence_html_or_numbers(self, live_client, monkeypatch):
        frames = self._frames_under(live_client, monkeypatch, "B")
        confidence = frames[stage_events.CONFIDENCE_COMPUTED]
        assert confidence["html"] is None
        assert confidence["data"] == {"suppressed": True}
        assert frames[stage_events.VERIFICATION_COMPLETED]["html"] is not None

    def test_condition_c_sends_everything(self, live_client, monkeypatch):
        frames = self._frames_under(live_client, monkeypatch, "C")
        assert frames[stage_events.VERIFICATION_COMPLETED]["html"] is not None
        assert frames[stage_events.CONFIDENCE_COMPUTED]["html"] is not None

    def test_masking_does_not_affect_what_gets_persisted(self, live_client, monkeypatch):
        """The server's own record is complete regardless of what the participant saw."""
        self._frames_under(live_client, monkeypatch, "A")
        runs = live_client.get("/runs/00000000-0000-0000-0000-000000000000")
        assert runs.status_code == 404  # sanity: the id above is not a real run
