"""FastAPI REST API: POST /generate, GET /trace/{id}, POST /verify.

Dependencies (Orchestrator, DB session) are constructed via FastAPI's
`Depends()` from environment variables, so tests can override them with
fakes via `app.dependency_overrides` instead of needing a live Ollama
instance or Postgres — the same offline-testable pattern used throughout
the rest of this codebase (FakeLLMClient, in-memory sqlite for TraceStore).
"""

import os
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse
from neo4j import GraphDatabase
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from verityai.agent.orchestrator import Orchestrator
from verityai.agent.trace import TraceStore
from verityai.api.dashboard import render_dashboard
from verityai.api.rate_limit import RateLimitMiddleware
from verityai.compliance.audit_log import AuditLogStore
from verityai.compliance.report_generator import (
    build_compliance_report_from_trace,
    export_to_pdf,
    export_to_sarif,
)
from verityai.db.base import Base
from verityai.kg.client import KGClient
from verityai.neural.ollama_client import OllamaClient
from verityai.ontology.models import (
    Algorithm,
    AuditLogEntry,
    ComplianceReport,
    GenerationRequest,
    GenerationResponse,
    ReasoningTrace,
    Rule,
    VerificationResult,
)
from verityai.symbolic.verify import verify_python_snippet

app = FastAPI(
    title="VerityAI API",
    description="Neuro-symbolic code generation + formal verification",
    version="0.0.1",
)
app.add_middleware(
    RateLimitMiddleware,
    limit=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60")),
    window_seconds=60.0,
)

_engine = None
_session_factory = None


def _get_engine():
    global _engine, _session_factory
    if _engine is None:
        database_url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
        if database_url == "sqlite:///:memory:":
            # A plain in-memory sqlite DB is per-connection -- FastAPI runs
            # sync endpoints in a worker thread pool, so without StaticPool
            # (one connection, shared across threads) each request would
            # see a fresh, table-less database.
            _engine = create_engine(
                database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            _engine = create_engine(database_url)
        # Base.metadata already has TraceRecord + AuditLogRecord registered,
        # since importing TraceStore/AuditLogStore above imports the modules
        # that define them against the shared Base (verityai.db.base).
        Base.metadata.create_all(_engine)
        _session_factory = sessionmaker(bind=_engine)
    return _engine, _session_factory


def get_db_session():
    """Yield a SQLAlchemy session, defaulting to in-memory sqlite for dev/test.

    Set DATABASE_URL (see .env.example) to point at a real Postgres instance
    in production.
    """
    _, session_factory = _get_engine()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_orchestrator() -> Orchestrator:
    """Construct an Orchestrator from environment config.

    Overridden in tests (app.dependency_overrides[get_orchestrator]) with a
    FakeLLMClient-backed instance so the test suite never needs a live
    Ollama server.
    """
    llm_client = OllamaClient(
        model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    )
    return Orchestrator(llm_client=llm_client)


_neo4j_driver = None


def get_kg_client() -> KGClient:
    """Construct a KGClient from environment config (NEO4J_URI/USER/PASSWORD).

    Overridden in tests with a fake driver -- see test_api.py -- so the
    test suite never needs a live Neo4j instance. The driver itself is
    cached at module level (a neo4j Driver is meant to be a long-lived
    connection pool, not recreated per request).
    """
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            auth=(
                os.environ.get("NEO4J_USER", "neo4j"),
                os.environ.get("NEO4J_PASSWORD", "verityai_password_123"),
            ),
        )
    return KGClient(_neo4j_driver)


def get_trace_store(db: Session = Depends(get_db_session)) -> TraceStore:
    return TraceStore(db)


def get_audit_log_store(db: Session = Depends(get_db_session)) -> AuditLogStore:
    return AuditLogStore(db)


class VerifyRequest(BaseModel):
    code: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Self-contained web dashboard: trace viewer + confidence meter + KG explorer."""
    return render_dashboard()


@app.post("/generate", response_model=GenerationResponse)
def generate(
    request: GenerationRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    trace_store: TraceStore = Depends(get_trace_store),
    audit_log: AuditLogStore = Depends(get_audit_log_store),
    x_actor: str = Header(default="api"),
) -> GenerationResponse:
    """Run the full generate-verify-retry loop, persist every attempt's
    trace, and record an audit log entry.

    Always returns 200: an unreachable LLM or failed verification is a
    business-logic outcome the orchestrator already handles internally
    (response.status == "failed"), not a transport-level error -- the
    request itself was processed successfully.

    `X-Actor` header identifies the caller for the audit trail -- there's
    no real auth system behind this yet (see AuditLogEntry's docstring),
    so it's recorded as-is, defaulting to "api" for unidentified callers.

    If verify_python_snippet's security scan blocked the final attempt
    (dangerous construct like os.system/eval/subprocess -- see
    symbolic/security_scan.py), that's recorded in the audit details too:
    a blocked RCE-style attempt must be both refused *and* logged.
    """
    response = orchestrator.run(request)
    trace_store.save_traces(response.traces)

    final_trace_id = response.traces[-1].id if response.traces else None
    details = {"prompt": request.prompt, "status": response.status}
    if response.final_verification.metadata.get("blocked_reason") == "dangerous_code_pattern":
        details["security_findings"] = response.final_verification.metadata["security_findings"]

    audit_log.record(
        AuditLogEntry(
            actor=x_actor,
            action="generate",
            trace_id=final_trace_id,
            details=details,
        )
    )
    return response


@app.get("/trace/{trace_id}/compliance-report", response_model=ComplianceReport)
def get_compliance_report(
    trace_id: UUID, trace_store: TraceStore = Depends(get_trace_store)
) -> ComplianceReport:
    """Human-facing compliance evidence for one trace (rules applied,
    verification proof, confidence) -- see compliance/report_generator.py.
    """
    trace = trace_store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return build_compliance_report_from_trace(trace)


@app.get("/trace/{trace_id}/compliance-report.sarif")
def get_compliance_report_sarif(
    trace_id: UUID, trace_store: TraceStore = Depends(get_trace_store)
) -> dict:
    """SARIF 2.1.0 rendering of the same report, for CI/CD and code-scanning tools."""
    trace = trace_store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    report = build_compliance_report_from_trace(trace)
    return export_to_sarif(report)


@app.get("/trace/{trace_id}/compliance-report.pdf")
def get_compliance_report_pdf(
    trace_id: UUID, trace_store: TraceStore = Depends(get_trace_store)
) -> Response:
    """PDF rendering of the same report, for an audit binder or a reviewer
    who never opens a terminal."""
    trace = trace_store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    report = build_compliance_report_from_trace(trace)
    pdf_bytes = export_to_pdf(report)
    return Response(content=pdf_bytes, media_type="application/pdf")


@app.get("/trace/{trace_id}", response_model=ReasoningTrace)
def get_trace(trace_id: UUID, trace_store: TraceStore = Depends(get_trace_store)) -> ReasoningTrace:
    trace = trace_store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@app.post("/verify", response_model=VerificationResult)
def verify(request: VerifyRequest) -> VerificationResult:
    """Verify a standalone code snippet -- no LLM involved, no trace persisted."""
    return verify_python_snippet(request.code)


@app.get("/kg/algorithms", response_model=list[Algorithm])
def list_algorithms(
    language: str = "python", kg_client: KGClient = Depends(get_kg_client)
) -> list[Algorithm]:
    """List all KG algorithms for a language -- backs the dashboard's KG explorer."""
    return kg_client.get_all_algorithms(language=language)


@app.get("/kg/rules", response_model=list[Rule])
def list_rules(
    language: str = "python", kg_client: KGClient = Depends(get_kg_client)
) -> list[Rule]:
    """List all KG rules for a language -- backs the dashboard's KG explorer."""
    return kg_client.get_all_rules(language=language)
