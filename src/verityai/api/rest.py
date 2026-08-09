"""FastAPI REST API: POST /generate, GET /trace/{id}, POST /verify.

Dependencies (Orchestrator, DB session) are constructed via FastAPI's
`Depends()` from environment variables, so tests can override them with
fakes via `app.dependency_overrides` instead of needing a live Ollama
instance or Postgres — the same offline-testable pattern used throughout
the rest of this codebase (FakeLLMClient, in-memory sqlite for TraceStore).

Concurrency model (read before assuming this scales): `/generate` is a
sync `def` endpoint, so FastAPI/Starlette runs each call in a worker
thread from anyio's default threadpool (40 threads unless configured --
see `VERITYAI_THREADPOOL_SIZE` below), not a truly async request. A real
run against llama3.2 measured ~65-125s per `/generate` call (see
docs/PHASE_3_METHODOLOGY.md's "Real run #1") -- with 40 threads and calls
that long, only ~40 concurrent `/generate` requests can be in flight at
once; the 41st queues behind whichever finishes first. Making the
threadpool bigger buys headroom, not a fix: `Orchestrator.run()` itself
is CPU/IO-bound (LLM inference + Z3 solving), so more threads just means
more concurrent inference calls competing for the same Ollama instance,
not more real throughput. A genuine fix needs an async job queue
(POST /generate returns a job_id immediately; GET /jobs/{id} polls status)
so the API layer stops holding an HTTP connection open for the entire
generation -- tracked as follow-up, not implemented here to avoid
building a queueing/worker subsystem this project doesn't otherwise need.

`POST /live/runs` (see the live-run section at the bottom of this module)
does implement the "return immediately, watch it separately" half of that
idea: it allocates a run id, starts the work on a daemon thread and
returns 202 in milliseconds, with progress delivered over SSE rather than
polled. It is *not* the general fix, though -- there is no durable queue,
no retry on worker death, and no cross-process state, so runs are capped
at VERITYAI_MAX_LIVE_RUNS and live only in this process's memory.
`/generate` deliberately keeps its simple synchronous contract.
"""

import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from uuid import UUID, uuid4

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from neo4j import GraphDatabase
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from verityai.agent import events as stage_events
from verityai.agent.events import StageEvent
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.trace import TraceStore
from verityai.api.dashboard import render_dashboard
from verityai.api.live_fragments import apply_condition, build_html
from verityai.api.live_page import render_live_page
from verityai.api.live_runs import (
    VALID_CONDITIONS,
    LiveRunRegistry,
    get_live_run_registry,
)
from verityai.api.rate_limit import RateLimitMiddleware
from verityai.api.run_view import render_run_view
from verityai.compliance.audit_log import AuditLogStore
from verityai.compliance.report_generator import (
    build_compliance_report_from_trace,
    export_to_pdf,
    export_to_sarif,
)
from verityai.db.base import Base
from verityai.db.migrate import ensure_additive_columns
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
    VerificationStatus,
)
from verityai.study.models import StudyResponse, StudyResponseSubmission
from verityai.study.store import StudyResponseStore, to_csv
from verityai.symbolic.verify import verify_python_snippet

logger = logging.getLogger(__name__)

# --- Live-run tuning -----------------------------------------------------
# The only backpressure on Ollama: live runs execute on daemon threads,
# which bypass anyio's threadpool limiter entirely. There is one Ollama
# instance behind this, so keep it small.
MAX_LIVE_RUNS = int(os.environ.get("VERITYAI_MAX_LIVE_RUNS", "4"))
# Wall-clock ceiling on one SSE connection, so a wedged run cannot pin a
# connection open forever. A real run is 65-125s; 600s is generous.
MAX_STREAM_SECONDS = float(os.environ.get("VERITYAI_MAX_STREAM_SECONDS", "600"))
# How often the async generator drains the (thread-owned) event buffer.
# The buffer's threading.Event wakes it sooner whenever an event lands, so
# this is only the ceiling on latency, not the typical case.
STREAM_POLL_SECONDS = 0.15
# SSE comment frames keep proxies from closing an idle connection during
# the long silent stretch of an LLM generation.
STREAM_KEEPALIVE_SECONDS = 15.0


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Make the sync-endpoint threadpool size configurable (see module
    docstring's concurrency model). `current_default_thread_limiter()` is
    contextvar-based and only resolvable inside a running event loop --
    it cannot be set at import time, hence a lifespan handler rather than
    a module-level assignment.
    """
    threadpool_size = os.environ.get("VERITYAI_THREADPOOL_SIZE")
    if threadpool_size:
        anyio.to_thread.current_default_thread_limiter().total_tokens = int(threadpool_size)
    yield


app = FastAPI(
    title="VerityAI API",
    description="Neuro-symbolic code generation + formal verification",
    version="0.0.1",
    lifespan=_lifespan,
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
        # create_all() never alters an existing table -- a DB from before
        # ReasoningTrace's request_id/generation_seconds/confidence_factors
        # existed needs this to gain those columns (see db/migrate.py).
        ensure_additive_columns(_engine)
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

    KG context is opt-in via VERITYAI_ENABLE_KG_CONTEXT=1: previously
    `/generate` never connected a kg_client at all (a real gap this fixes),
    so opt-in avoids silently changing existing deployments' behavior the
    moment this ships. VERITYAI_RETRIEVAL_STRATEGY defaults to "legacy"
    until the retrieval A/B (docs/PHASE_3_METHODOLOGY.md "Real run #3")
    justifies flipping it — see ADR-0003.
    """
    llm_client = OllamaClient(
        model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        embed_model=os.environ.get("OLLAMA_EMBED_MODEL"),
    )
    kg_client = get_kg_client() if os.environ.get("VERITYAI_ENABLE_KG_CONTEXT") == "1" else None
    return Orchestrator(
        llm_client=llm_client,
        kg_client=kg_client,
        retrieval_strategy=os.environ.get("VERITYAI_RETRIEVAL_STRATEGY", "legacy"),
    )


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


def get_background_session_factory():
    """Session factory for work that outlives the request that started it.

    A live run keeps writing traces long after POST /live/runs has
    returned its 202, by which point the request-scoped session from
    get_db_session is closed. This is a FastAPI dependency (rather than
    calling _get_engine() inside the worker thread) purely so tests can
    point background writes at the same in-memory database the rest of
    their assertions read from.
    """
    _, session_factory = _get_engine()
    return session_factory


def get_audit_log_store(db: Session = Depends(get_db_session)) -> AuditLogStore:
    return AuditLogStore(db)


class VerifyRequest(BaseModel):
    code: str


class RunAttempt(BaseModel):
    """API-layer view of one attempt within a request -- not an ontology
    model, since it's a projection of ReasoningTrace shaped for the
    /runs/{request_id} timeline rather than a first-class domain concept."""

    attempt_number: int
    trace_id: UUID
    status: Optional[str] = None
    confidence_score: float
    generation_seconds: Optional[float] = None
    failure_reason: Optional[str] = None


class RunSummary(BaseModel):
    """Full timeline for one Orchestrator.run() call, grouped by request_id."""

    request_id: UUID
    user_prompt: str
    status: str  # "success" | "partial" | "failed", mirrors GenerationResponse.status
    attempt_count: int
    total_generation_seconds: float
    attempts: list[RunAttempt]


def _build_run_summary(request_id: UUID, traces: list[ReasoningTrace]) -> RunSummary:
    """Derive a RunSummary from a request's attempt history (non-empty)."""
    last = traces[-1]
    if last.verification_result and last.verification_result.status == VerificationStatus.PASS:
        status = "success"
    elif (
        last.verification_result
        and last.verification_result.status == VerificationStatus.NOT_VERIFIED
    ):
        status = "partial"
    else:
        status = "failed"

    return RunSummary(
        request_id=request_id,
        user_prompt=last.user_prompt,
        status=status,
        attempt_count=len(traces),
        total_generation_seconds=sum(t.generation_seconds or 0.0 for t in traces),
        attempts=[
            RunAttempt(
                attempt_number=t.attempt_number,
                trace_id=t.id,
                status=t.verification_result.status.value if t.verification_result else None,
                confidence_score=t.confidence_score,
                generation_seconds=t.generation_seconds,
                failure_reason=t.failure_reason,
            )
            for t in traces
        ],
    )


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


@app.get("/runs/{request_id}", response_model=RunSummary)
def get_run(request_id: UUID, trace_store: TraceStore = Depends(get_trace_store)) -> RunSummary:
    """JSON timeline of every attempt belonging to one generation request.

    Traces persisted before request_id existed (Commit 5) have it as NULL
    and will never match here -- 404, same as an unknown id, since there's
    no way to distinguish the two cases from this data alone.
    """
    traces = trace_store.get_traces_by_request(request_id)
    if not traces:
        raise HTTPException(status_code=404, detail=f"Run {request_id} not found")
    return _build_run_summary(request_id, traces)


@app.get("/runs/{request_id}/view", response_class=HTMLResponse)
def get_run_view(request_id: UUID, trace_store: TraceStore = Depends(get_trace_store)) -> str:
    """Visual reasoning-trace view: pipeline, KG retrieval provenance, attempt
    timeline, symbolic verification detail, and confidence factor breakdown.
    """
    traces = trace_store.get_traces_by_request(request_id)
    if not traces:
        raise HTTPException(status_code=404, detail=f"Run {request_id} not found")
    return render_run_view(traces)


# --- Live run: watch the pipeline execute --------------------------------
# Two-call handshake. POST /live/runs allocates the run id, starts the work
# on a worker thread and returns in milliseconds; the client then opens the
# SSE stream. The id is allocated *before* the run starts precisely so the
# client has a stream URL to connect to -- it is passed into the
# orchestrator as request_id, which is why run_id == request_id and every
# existing /runs/{id} route works on the same id afterwards.


class LiveRunRequest(BaseModel):
    prompt: str
    language: str = "python"
    max_attempts: int = Field(default=3, ge=1, le=5)
    # Study participation is the default use of this page; see
    # docs/T5_HUMAN_EVAL_PROTOCOL.md. Rejected below if not explicitly true.
    consent: bool = False


class LiveRunCreated(BaseModel):
    run_id: UUID
    stream_url: str
    condition: str


def _pick_condition() -> str:
    """Assign a T5 panel-masking condition, server-side.

    Per-run rather than per-session: the protocol wants within-subject
    variation across conditions, and a run is the unit a participant
    actually judges.

    VERITYAI_FORCE_CONDITION exists so the page can be developed and
    demoed deterministically. It must be unset while collecting real study
    data -- a fixed condition would silently destroy the manipulation.
    """
    forced = os.environ.get("VERITYAI_FORCE_CONDITION")
    if forced in VALID_CONDITIONS:
        return forced
    return random.choice(VALID_CONDITIONS)


def _run_and_publish(
    orchestrator: Orchestrator,
    request: GenerationRequest,
    run_id: UUID,
    condition: str,
    registry: LiveRunRegistry,
    session_factory,
) -> None:
    """Execute one run on a worker thread, publishing events as it goes.

    Opens its own DB session from `session_factory`. The request-scoped
    session from Depends(get_db_session) is already closed by the time this
    starts -- POST /live/runs returned its 202 long before.
    """
    session = session_factory()
    trace_store = TraceStore(session)
    ctx: dict = {}

    def emit(event: StageEvent) -> None:
        # Render before masking: build_html also stashes per-run state in
        # ctx (notably the generated code the Z3 panel needs).
        event.html = build_html(event, ctx)

        # Persist each attempt as it lands, so /runs/{id}/view is usable
        # mid-run and survives a dropped stream. save_trace upserts by id,
        # so the save_traces below is idempotent. Persistence is the
        # server's own record and is deliberately *not* condition-masked.
        if event.type == stage_events.ATTEMPT_COMPLETED and event.data.get("trace"):
            try:
                trace_store.save_trace(ReasoningTrace.model_validate(event.data["trace"]))
            except Exception as e:
                logger.warning(f"Could not persist attempt mid-run: {e}")

        apply_condition(event, condition)
        registry.append(run_id, event)

    try:
        response = orchestrator.run(request, emit=emit, request_id=run_id)
        trace_store.save_traces(response.traces)
        registry.finish(run_id)
    except Exception as e:
        logger.exception(f"Live run {run_id} failed")
        registry.finish(run_id, error=str(e))
    finally:
        session.close()


@app.post("/live/runs", response_model=LiveRunCreated, status_code=202)
def create_live_run(
    body: LiveRunRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    registry: LiveRunRegistry = Depends(get_live_run_registry),
    session_factory=Depends(get_background_session_factory),
) -> LiveRunCreated:
    """Start a run in the background and hand back a URL to watch it on."""
    if not body.consent:
        raise HTTPException(
            status_code=400,
            detail="Consent is required before starting a run on this page.",
        )

    registry.sweep()
    if registry.active_count() >= MAX_LIVE_RUNS:
        raise HTTPException(
            status_code=429,
            detail="Too many runs in progress right now. Please try again in a minute.",
        )

    run = registry.create(_pick_condition(), run_id=uuid4())
    request = GenerationRequest(
        prompt=body.prompt, language=body.language, max_attempts=body.max_attempts
    )
    threading.Thread(
        target=_run_and_publish,
        args=(orchestrator, request, run.run_id, run.condition, registry, session_factory),
        daemon=True,
    ).start()

    return LiveRunCreated(
        run_id=run.run_id,
        stream_url=f"/live/runs/{run.run_id}/events",
        condition=run.condition,
    )


async def _event_stream(
    registry: LiveRunRegistry, run_id: UUID, last_sequence: int
) -> AsyncIterator[str]:
    """Yield SSE frames for one run until it finishes (or the guard trips)."""
    started = time.monotonic()
    last_output = started

    while True:
        run = registry.get(run_id)
        if run is None:
            # Swept out from under us -- nothing more will ever arrive.
            yield "event: done\ndata: {}\n\n"
            return

        pending = registry.events_since(run_id, last_sequence)
        for event in pending:
            last_sequence = event.sequence
            # `id:` is what makes EventSource's Last-Event-ID reconnect
            # resume exactly where it left off.
            yield (
                f"id: {event.sequence}\n"
                f"event: {event.type}\n"
                f"data: {event.model_dump_json()}\n\n"
            )
            last_output = time.monotonic()

        if run.finished and not registry.events_since(run_id, last_sequence):
            yield "event: done\ndata: {}\n\n"
            return

        if time.monotonic() - started > MAX_STREAM_SECONDS:
            yield "event: done\ndata: {}\n\n"
            return

        now = time.monotonic()
        if now - last_output > STREAM_KEEPALIVE_SECONDS:
            yield ": keepalive\n\n"
            last_output = now

        run.tick.clear()
        await anyio.sleep(STREAM_POLL_SECONDS)


@app.get("/live/runs/{run_id}/events")
async def stream_live_run(
    run_id: UUID,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    registry: LiveRunRegistry = Depends(get_live_run_registry),
) -> StreamingResponse:
    """Server-sent events for one live run.

    The only async route in this module: it holds a connection open for the
    length of a run, which a sync endpoint would do by occupying a
    threadpool worker the whole time.

    Every event the run has emitted is replayed before tailing, so a client
    that connects late (or reconnects) never misses a step. Rate limiting
    exempts this path -- see api/rate_limit.py.
    """
    if registry.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Live run {run_id} not found")

    try:
        last_sequence = int(last_event_id) if last_event_id else 0
    except ValueError:
        last_sequence = 0

    return StreamingResponse(
        _event_stream(registry, run_id, last_sequence),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tells nginx not to buffer the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/live", response_class=HTMLResponse)
def live_page() -> str:
    """The live pipeline view: submit a prompt, watch verification happen."""
    return render_live_page()


# --- T5 study: response collection --------------------------------------


def get_study_store(db: Session = Depends(get_db_session)) -> StudyResponseStore:
    return StudyResponseStore(db)


@app.post("/study/responses", response_model=StudyResponse, status_code=201)
def submit_study_response(
    submission: StudyResponseSubmission,
    registry: LiveRunRegistry = Depends(get_live_run_registry),
    store: StudyResponseStore = Depends(get_study_store),
    trace_store: TraceStore = Depends(get_trace_store),
) -> StudyResponse:
    """Record one participant's answers about one run.

    The panel-masking condition is read from the run registry, never from
    the request body -- see study/models.py. If the run has already been
    swept out of memory we cannot establish which condition the participant
    actually saw, and a response with an unknown condition is unusable for
    the analysis, so it is refused rather than stored with a guess.
    """
    run = registry.get(submission.run_id)
    if run is None:
        if trace_store.get_traces_by_request(submission.run_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This run has expired from the live registry, so the display "
                    "condition it was shown under can no longer be established. "
                    "The response was not recorded."
                ),
            )
        raise HTTPException(status_code=404, detail=f"Run {submission.run_id} not found")

    response = StudyResponse(condition=run.condition, **submission.model_dump())
    store.save(response)
    return response


def _require_study_token(provided: Optional[str]) -> None:
    """Gate the exports. Verbatim answers are participant data, not public.

    With VERITYAI_STUDY_TOKEN unset the endpoints 404 rather than 401: an
    unconfigured deployment should look like it has no export endpoints at
    all, so a missed env var can never become an open dump of free-text
    responses.
    """
    expected = os.environ.get("VERITYAI_STUDY_TOKEN")
    if not expected or provided != expected:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/study/responses.json")
def export_study_responses_json(
    x_study_token: Optional[str] = Header(default=None, alias="X-Study-Token"),
    store: StudyResponseStore = Depends(get_study_store),
) -> list[StudyResponse]:
    _require_study_token(x_study_token)
    return store.list_all()


@app.get("/study/responses.csv")
def export_study_responses_csv(
    x_study_token: Optional[str] = Header(default=None, alias="X-Study-Token"),
    store: StudyResponseStore = Depends(get_study_store),
) -> Response:
    _require_study_token(x_study_token)
    return Response(content=to_csv(store.list_all()), media_type="text/csv")


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
