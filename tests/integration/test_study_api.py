"""Integration tests for the T5 study endpoints.

The two properties worth defending here are both about study integrity:
the display condition must come from the server, and participants' verbatim
answers must never be exposed by an unconfigured deployment.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.fakes import FakeLLMClient, wrap_code
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.trace import TraceStore
from verityai.api.live_runs import reset_live_run_state
from verityai.api.rest import (
    app,
    get_audit_log_store,
    get_background_session_factory,
    get_orchestrator,
    get_study_store,
    get_trace_store,
)
from verityai.compliance.audit_log import AuditLogStore
from verityai.study.store import CSV_COLUMNS, StudyResponseStore

PASSING_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_live_run_state()
    yield
    reset_live_run_state()


@pytest.fixture
def study_client(sqlite_engine, db_session):
    factory = sessionmaker(bind=sqlite_engine)
    app.dependency_overrides[get_trace_store] = lambda: TraceStore(db_session)
    app.dependency_overrides[get_audit_log_store] = lambda: AuditLogStore(db_session)
    app.dependency_overrides[get_study_store] = lambda: StudyResponseStore(db_session)
    app.dependency_overrides[get_background_session_factory] = lambda: factory
    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
        llm_client=FakeLLMClient([wrap_code(PASSING_CODE)])
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def _start_run(client):
    response = client.post("/live/runs", json={"prompt": "add two numbers", "consent": True})
    assert response.status_code == 202
    return response.json()


def _answers(run_id, **overrides):
    payload = {
        "run_id": run_id,
        "trusts_code": True,
        "trust_reason": "the Z3 panel showed no counterexample",
        "merge_intent": "full_review",
        "kept_element": "z3",
    }
    payload.update(overrides)
    return payload


class TestSubmission:
    def test_a_response_is_stored_and_echoed_back(self, study_client):
        created = _start_run(study_client)
        response = study_client.post("/study/responses", json=_answers(created["run_id"]))

        assert response.status_code == 201
        body = response.json()
        assert body["run_id"] == created["run_id"]
        assert body["trusts_code"] is True
        assert body["merge_intent"] == "full_review"

    def test_condition_is_stamped_from_the_registry(self, study_client):
        created = _start_run(study_client)
        body = study_client.post("/study/responses", json=_answers(created["run_id"])).json()
        assert body["condition"] == created["condition"]

    def test_a_client_supplied_condition_is_ignored(self, study_client):
        """Self-selecting a condition would destroy the manipulation."""
        created = _start_run(study_client)
        forged = _answers(created["run_id"])
        forged["condition"] = "Z"

        body = study_client.post("/study/responses", json=forged).json()
        assert body["condition"] == created["condition"]
        assert body["condition"] != "Z"

    def test_an_unknown_run_is_rejected(self, study_client):
        response = study_client.post(
            "/study/responses",
            json=_answers("00000000-0000-0000-0000-000000000000"),
        )
        assert response.status_code == 404

    def test_merge_intent_is_required(self, study_client):
        created = _start_run(study_client)
        payload = _answers(created["run_id"])
        del payload["merge_intent"]
        assert study_client.post("/study/responses", json=payload).status_code == 422

    def test_an_invalid_merge_intent_is_rejected(self, study_client):
        created = _start_run(study_client)
        payload = _answers(created["run_id"], merge_intent="ship_it_yolo")
        assert study_client.post("/study/responses", json=payload).status_code == 422

    def test_trust_and_merge_intent_can_disagree(self, study_client):
        """The attitudinal/behavioural gap is the finding, not a validation error."""
        created = _start_run(study_client)
        body = study_client.post(
            "/study/responses",
            json=_answers(created["run_id"], trusts_code=True, merge_intent="full_review"),
        ).json()
        assert body["trusts_code"] is True
        assert body["merge_intent"] == "full_review"


class TestExportGating:
    def test_exports_404_when_no_token_is_configured(self, study_client, monkeypatch):
        """An unconfigured deployment must not look like it has an export at all."""
        monkeypatch.delenv("VERITYAI_STUDY_TOKEN", raising=False)
        assert study_client.get("/study/responses.json").status_code == 404
        assert study_client.get("/study/responses.csv").status_code == 404

    def test_exports_404_with_a_wrong_token(self, study_client, monkeypatch):
        monkeypatch.setenv("VERITYAI_STUDY_TOKEN", "correct-horse")
        response = study_client.get(
            "/study/responses.json", headers={"X-Study-Token": "wrong"}
        )
        assert response.status_code == 404

    def test_json_export_returns_stored_responses_with_the_right_token(
        self, study_client, monkeypatch
    ):
        monkeypatch.setenv("VERITYAI_STUDY_TOKEN", "correct-horse")
        created = _start_run(study_client)
        study_client.post("/study/responses", json=_answers(created["run_id"]))

        response = study_client.get(
            "/study/responses.json", headers={"X-Study-Token": "correct-horse"}
        )
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["run_id"] == created["run_id"]

    def test_csv_export_has_the_declared_header(self, study_client, monkeypatch):
        monkeypatch.setenv("VERITYAI_STUDY_TOKEN", "correct-horse")
        created = _start_run(study_client)
        study_client.post("/study/responses", json=_answers(created["run_id"]))

        response = study_client.get(
            "/study/responses.csv", headers={"X-Study-Token": "correct-horse"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        lines = response.text.strip().splitlines()
        assert lines[0].split(",") == CSV_COLUMNS
        assert len(lines) == 2
