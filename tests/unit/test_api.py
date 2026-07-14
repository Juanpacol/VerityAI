"""Unit tests for api/rest.py using FastAPI's TestClient.

Overrides get_orchestrator/get_trace_store so no live Ollama or Postgres
is needed -- the same offline-testable pattern (FakeLLMClient, in-memory
sqlite) used throughout the rest of this codebase.
"""

from uuid import uuid4

import pytest

from tests.fakes import AlwaysFailingLLMClient, FakeLLMClient, wrap_code
from verityai.agent.orchestrator import Orchestrator
from verityai.api.rest import app, get_audit_log_store, get_kg_client, get_orchestrator
from verityai.kg.client import KGClient
from verityai.ontology.models import Rule


def _override_orchestrator_with(llm_client) -> None:
    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(llm_client=llm_client)


@pytest.fixture
def client(api_client):
    """Alias for the shared `api_client` fixture (tests/conftest.py) --
    kept so existing test signatures in this file don't all need renaming."""
    return api_client


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestGenerateEndpoint:
    def test_successful_generation_returns_success_status(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        response = client.post("/generate", json={"prompt": "assign 1 to x"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "x = 1" in body["code"]
        assert len(body["traces"]) == 1

    def test_unreachable_llm_returns_200_with_failed_status(self, client):
        """Business-logic failure (LLM down) is still a successfully-handled
        request -- 200 with status="failed" in the body, not a 5xx."""
        _override_orchestrator_with(AlwaysFailingLLMClient())

        response = client.post("/generate", json={"prompt": "test"})

        assert response.status_code == 200
        assert response.json()["status"] == "failed"

    def test_generated_traces_are_persisted_and_fetchable(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        trace_id = generate_response.json()["traces"][0]["id"]

        trace_response = client.get(f"/trace/{trace_id}")

        assert trace_response.status_code == 200
        assert trace_response.json()["user_prompt"] == "assign 1 to x"

    def test_missing_prompt_returns_422(self, client):
        response = client.post("/generate", json={})
        assert response.status_code == 422


class TestTraceEndpoint:
    def test_unknown_trace_id_returns_404(self, client):
        response = client.get(f"/trace/{uuid4()}")
        assert response.status_code == 404

    def test_invalid_uuid_returns_422(self, client):
        response = client.get("/trace/not-a-uuid")
        assert response.status_code == 422


class TestRunsEndpoints:
    def test_run_summary_json_shape(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        request_id = generate_response.json()["request_id"]

        response = client.get(f"/runs/{request_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == request_id
        assert body["status"] == "success"
        assert body["attempt_count"] == 1
        assert len(body["attempts"]) == 1
        assert body["attempts"][0]["attempt_number"] == 1

    def test_run_view_returns_html_with_attempt_info(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        request_id = generate_response.json()["request_id"]

        response = client.get(f"/runs/{request_id}/view")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Attempt 1" in response.text

    def test_run_view_lists_retrieved_rule_names(self, client):
        class FakeCategoryKGClient:
            def get_rules_by_category(self, category, language="python"):
                if category == "security":
                    return [
                        Rule(
                            name="no_null_deref",
                            description="Ensure no null pointer dereferences",
                            category="security",
                            condition="Ensure no null pointer dereferences",
                            severity="high",
                            applies_to=["python"],
                        )
                    ]
                return []

        app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
            llm_client=FakeLLMClient([wrap_code("x = 1\nassert x == 1")]),
            kg_client=FakeCategoryKGClient(),
        )

        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        request_id = generate_response.json()["request_id"]

        response = client.get(f"/runs/{request_id}/view")

        assert "no_null_deref" in response.text

    def test_run_view_is_self_contained(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        request_id = generate_response.json()["request_id"]

        response = client.get(f"/runs/{request_id}/view")

        # Generated code may legitimately contain literal "http://" text, so
        # assert on concrete external-resource markers, not a bare substring.
        assert "<script src" not in response.text
        assert "<link" not in response.text
        assert "url(" not in response.text

    def test_unknown_request_id_returns_404(self, client):
        assert client.get(f"/runs/{uuid4()}").status_code == 404
        assert client.get(f"/runs/{uuid4()}/view").status_code == 404

    def test_failed_attempt_renders_in_view(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 5\nassert x == 999")]))

        generate_response = client.post("/generate", json={"prompt": "test", "max_attempts": 1})
        request_id = generate_response.json()["request_id"]

        response = client.get(f"/runs/{request_id}/view")

        assert response.status_code == 200
        assert "Failed" in response.text


class TestAuditLog:
    def test_generate_records_an_audit_log_entry(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        response = client.post(
            "/generate", json={"prompt": "assign 1 to x"}, headers={"X-Actor": "alice"}
        )
        trace_id = response.json()["traces"][0]["id"]

        session = app.dependency_overrides[get_audit_log_store]()
        entries = session.for_trace(trace_id)

        assert len(entries) == 1
        assert entries[0].actor == "alice"
        assert entries[0].action == "generate"

    def test_missing_actor_header_defaults_to_api(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))

        response = client.post("/generate", json={"prompt": "test"})
        trace_id = response.json()["traces"][0]["id"]

        entries = app.dependency_overrides[get_audit_log_store]().for_trace(trace_id)
        assert entries[0].actor == "api"


class TestComplianceReportEndpoints:
    def test_json_report_reflects_the_generated_trace(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))
        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        trace_id = generate_response.json()["traces"][0]["id"]

        response = client.get(f"/trace/{trace_id}/compliance-report")

        assert response.status_code == 200
        body = response.json()
        assert body["final_status"] == "success"
        assert body["user_prompt"] == "assign 1 to x"

    def test_json_report_404_for_unknown_trace(self, client):
        response = client.get(f"/trace/{uuid4()}/compliance-report")
        assert response.status_code == 404

    def test_sarif_report_has_expected_shape(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))
        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        trace_id = generate_response.json()["traces"][0]["id"]

        response = client.get(f"/trace/{trace_id}/compliance-report.sarif")

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "2.1.0"
        assert "runs" in body

    def test_pdf_report_returns_pdf_bytes(self, client):
        _override_orchestrator_with(FakeLLMClient([wrap_code("x = 1\nassert x == 1")]))
        generate_response = client.post("/generate", json={"prompt": "assign 1 to x"})
        trace_id = generate_response.json()["traces"][0]["id"]

        response = client.get(f"/trace/{trace_id}/compliance-report.pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")

    def test_pdf_report_404_for_unknown_trace(self, client):
        response = client.get(f"/trace/{uuid4()}/compliance-report.pdf")
        assert response.status_code == 404


class TestVerifyEndpoint:
    def test_verifies_passing_code(self, client):
        response = client.post("/verify", json={"code": "x = 1\nassert x == 1"})
        assert response.status_code == 200
        assert response.json()["status"] == "pass"

    def test_verifies_failing_code(self, client):
        response = client.post("/verify", json={"code": "x = 1\nassert x == 999"})
        assert response.status_code == 200
        assert response.json()["status"] == "fail"

    def test_missing_code_field_returns_422(self, client):
        response = client.post("/verify", json={})
        assert response.status_code == 422


class FakeNeo4jResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeNeo4jSession:
    def __init__(self, records):
        self._records = records

    def run(self, query, **params):
        return FakeNeo4jResult(self._records)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeNeo4jDriver:
    def __init__(self, records):
        self._records = records

    def session(self):
        return FakeNeo4jSession(self._records)


ALGORITHM_RECORD = {
    "a.name": "binary_search",
    "a.description": "Search for target in sorted array",
    "a.code": "def binary_search(arr, target): ...",
    "a.language": "python",
    "a.complexity_time": "O(log n)",
    "a.complexity_space": "O(1)",
    "a.verified": True,
}

RULE_RECORD = {
    "r.name": "no_null_deref",
    "r.description": "Ensure no null pointer dereferences",
    "r.category": "security",
    "r.severity": "critical",
    "r.formal_spec": None,
    "r.applies_to": ["python"],
}


class TestKGEndpoints:
    def test_list_algorithms_returns_kg_data(self, client):
        app.dependency_overrides[get_kg_client] = lambda: KGClient(
            FakeNeo4jDriver([ALGORITHM_RECORD])
        )

        response = client.get("/kg/algorithms?language=python")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "binary_search"
        assert body[0]["description"] == "Search for target in sorted array"

    def test_list_rules_returns_kg_data(self, client):
        app.dependency_overrides[get_kg_client] = lambda: KGClient(FakeNeo4jDriver([RULE_RECORD]))

        response = client.get("/kg/rules?language=python")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "no_null_deref"
        assert body[0]["condition"] == "Ensure no null pointer dereferences"

    def test_empty_kg_returns_empty_list(self, client):
        app.dependency_overrides[get_kg_client] = lambda: KGClient(FakeNeo4jDriver([]))

        response = client.get("/kg/algorithms")

        assert response.status_code == 200
        assert response.json() == []


class TestDashboardRoute:
    def test_dashboard_returns_html(self, client):
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "VerityAI Dashboard" in response.text

    def test_dashboard_has_no_external_resources(self, client):
        """Self-contained: no CDN scripts/stylesheets, no external fetches."""
        response = client.get("/dashboard")
        assert "http://" not in response.text
        assert "https://" not in response.text
        assert "<link" not in response.text


class TestGetOrchestratorEnvWiring:
    """get_orchestrator() previously never connected a kg_client at all --
    these call the real (un-overridden) factory function directly and only
    inspect the constructed Orchestrator's config, never triggering a live
    Ollama/Neo4j network call (OllamaClient's constructor doesn't connect)."""

    def test_defaults_to_no_kg_client_and_legacy_strategy(self, monkeypatch):
        monkeypatch.delenv("VERITYAI_ENABLE_KG_CONTEXT", raising=False)
        monkeypatch.delenv("VERITYAI_RETRIEVAL_STRATEGY", raising=False)

        orchestrator = get_orchestrator()

        assert orchestrator.kg_client is None
        assert orchestrator.retrieval_strategy == "legacy"

    def test_enable_kg_context_wires_a_kg_client(self, monkeypatch):
        monkeypatch.setenv("VERITYAI_ENABLE_KG_CONTEXT", "1")
        monkeypatch.setenv("VERITYAI_RETRIEVAL_STRATEGY", "hybrid")

        orchestrator = get_orchestrator()

        assert orchestrator.kg_client is not None
        assert orchestrator.retrieval_strategy == "hybrid"
