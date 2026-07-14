# Phase 4 Part B — Compliance & Audit Trail Reports

## What shipped

- **`ontology/models.py`**: `ComplianceReport` (rules applied, patterns
  reviewed, verification proof, confidence, code) and `AuditLogEntry`
  (actor, action, trace_id, details, timestamp).
- **`compliance/report_generator.py`**:
  - `build_compliance_report(response)` — from a full in-memory
    `GenerationResponse` (right after a `/generate` call).
  - `build_compliance_report_from_trace(trace)` — from a single persisted
    `ReasoningTrace` fetched later (e.g. via the API), inferring
    `final_status` from the trace's own verification result using the
    same PASS→success / NOT_VERIFIED→partial / else→failed mapping
    `Orchestrator._build_response` uses.
  - `export_to_sarif(report)` — SARIF 2.1.0 dict; a clean run still emits
    one informational result rather than an empty `results` array, so a
    passing report doesn't read as "nothing was checked."
  - `export_to_pdf(report)` — real PDF bytes via `reportlab` (new
    dependency, added at the user's request after discussing that this
    report's actual audience is a compliance/security reviewer or auditor,
    not a developer — see `CLAUDE.md`'s "Enterprise sales enabler" framing
    for Mejora 5).
- **`compliance/audit_log.py`**: `AuditLogStore`, same injected-`Session`
  pattern as `TraceStore` — append-only, `record()` / `for_trace()` / `all()`.
- **API wiring**: `/generate` now records an audit log entry per call
  (actor from an `X-Actor` header, defaulting to `"api"` — there's no real
  auth system yet, so this is a caller-supplied label, not a verified
  identity). Three new read endpoints: `GET /trace/{id}/compliance-report`
  (JSON), `.../compliance-report.sarif`, `.../compliance-report.pdf`.

## Test coverage

`test_report_generator.py`, `test_audit_log.py`, plus new cases in
`test_api.py` for the audit log and the three compliance-report endpoints
— 25 new tests. Full suite: 383 tests passing.

## Known gap carried over from Part A

`get_audit_log_store` and `get_trace_store` both depend on
`get_db_session`, which — like Part A's original bug — defaults to a
`StaticPool` sqlite in-memory DB shared across FastAPI's worker threads.
Both `TraceBase` and `AuditLogBase` (two separate `DeclarativeBase`
classes, mirroring how `agent/trace.py` and `compliance/audit_log.py` are
independent modules) now have their tables created on the same shared
engine in `_get_engine()`. Tests override both stores explicitly to avoid
relying on this global.

## Deferred (Phase 4 Parts C/D — not started)

- **C. Web Dashboard** — code + trace viewer, confidence meter, KG
  explorer.
- **D. Security & Deployment** — Docker execution sandbox, prompt-injection
  pentest cases (note: `neural/prompt_builder.py` already has
  injection-mitigation tests from Phase 2 — check overlap before
  duplicating), API rate limiting, `verityai:latest` Docker image.
