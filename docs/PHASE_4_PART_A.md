# Phase 4 Part A — API, CLI & SDK

## What shipped

- **`api/rest.py`** (FastAPI): `POST /generate` (runs the full retry loop,
  persists every attempt's trace), `GET /trace/{id}` (fetch a persisted
  `ReasoningTrace`), `POST /verify` (standalone snippet check, no LLM, no
  persistence), `GET /health`. Orchestrator/DB session are constructed via
  `Depends()` from environment variables (`OLLAMA_MODEL`, `OLLAMA_HOST`,
  `DATABASE_URL`), overridable in tests via `app.dependency_overrides` —
  the whole test suite runs against a `FakeLLMClient` + in-memory sqlite,
  no live Ollama/Postgres needed.
- **`cli/verityai_cli.py`** (Typer): `verityai generate <prompt>` (needs a
  live Ollama instance — runs the full loop and prints code + confidence +
  explanation via `rich`), `verityai verify <file>` / `verityai explain
  <file>` (no LLM at all — call `verify_python_snippet` directly on the
  file). Wired to the `verityai` console script in `pyproject.toml`.
- **`sdk.py`** (`from verityai import Verifier`): thin wrapper —
  `Verifier(...).generate(prompt)` / `.verify(code)` — for embedding
  VerityAI in other Python code without going through the API or CLI.

## A real, non-obvious bug caught while testing

The API tests initially failed with `no such table: reasoning_traces` even
though `Base.metadata.create_all()` had run. Cause: FastAPI runs sync
endpoint functions in a worker **thread**, and a plain `sqlite:///:memory:`
engine hands out a **new, blank in-memory database per connection** — the
thread handling the request got a different (table-less) database than
the one the test fixture set up. Fixed with `StaticPool` +
`check_same_thread=False` (one shared connection regardless of thread),
applied both in `rest.py`'s own default and in the test fixture. Would
have surfaced in real usage the moment `/generate` ran under any real ASGI
server, not just in tests — worth calling out since it's the kind of bug
that "works on my machine" (single-threaded test scripts) and breaks in
the first real deployment.

## Design choice: /generate always returns 200

An unreachable LLM or a failed verification is `Orchestrator.run()`
returning a normal `GenerationResponse` with `status="failed"` — not an
exception. The endpoint doesn't wrap it in a try/except translating to a
5xx; the request was handled successfully, the *generation* failed, and
that distinction is in the response body, not the transport status code.

## Test coverage

`tests/unit/test_api.py`, `test_cli.py`, `test_sdk.py` — 21 new tests, all
offline (FakeLLMClient, in-memory sqlite, no live services). Full suite:
358 tests passing.

## Deferred (Phase 4 Parts B/C/D — not started)

- **B. Compliance & Audit Trail Reports** — `report_generator.py`
  (PDF/SARIF export of rules applied + verification proof + confidence),
  audit log (who/when/what changed). Natural next step: it consumes the
  same `TraceStore` data this session's API already persists.
- **C. Web Dashboard** — code + trace viewer, confidence meter, KG
  explorer. Distinct from the Phase 3 evaluation dashboard
  (`evaluation/dashboard.py`), which compares baseline metrics, not
  individual generation results.
- **D. Security & Deployment** — Docker execution sandbox for tests,
  prompt-injection pentest cases (note: `neural/prompt_builder.py` already
  has some injection-mitigation tests from Phase 2 — check overlap before
  duplicating), API rate limiting, `verityai:latest` Docker image.
