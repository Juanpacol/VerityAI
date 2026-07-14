# Phase 4 Part D — Security & Deployment

With this part done, **Phase 4 (Parts A–D) is complete.**

## What shipped

- **`symbolic/security_scan.py`** — AST-based blocklist scanner for
  dangerous constructs (`os.system`, `subprocess.*`, `eval`/`exec`/`compile`,
  `__import__`, `pickle.loads`, raw `socket`, `shutil.rmtree`, ...). Wired
  directly into `verify_python_snippet` (not just the orchestrator's retry
  loop), so *every* caller gets this safety net for free: refinement's
  incremental re-verification, `rule_validation`'s Z3 gate for candidate KG
  rules, and the Phase 3 evaluation baselines all reject dangerous code the
  same way LLM-generated code does. A match forces `VerificationStatus.FAIL`
  unconditionally, regardless of what Z3 says about the snippet's asserts.
- **Retry-loop integration**: `AgentState._summarize_failure` recognizes a
  security block and feeds the model a specific reason ("contains: os.system(...).
  Do not use these constructs") on the next retry attempt, instead of a
  generic "Verification status: fail."
- **Audit log integration**: `/generate` records `security_findings` in the
  audit entry's `details` when a response was blocked — a crafted-prompt
  RCE attempt is both refused *and* logged, per the plan's explicit
  acceptance criterion.
- **`api/rate_limit.py`** — hand-rolled fixed-window rate limiter
  (per-client-IP, configurable via `RATE_LIMIT_PER_MINUTE`, default 60/min),
  wired as FastAPI middleware. `/health` is exempt (so a load balancer's
  health checks can't trip it). No new dependency: `slowapi` wasn't
  installed and a single-process limiter this simple didn't need it.
- **`Dockerfile`** + **`docker/docker-compose.yml`**'s new `app` service
  (`verityai:latest`) — built and smoke-tested for real in this session
  (not just written and assumed to work): `docker build`, then a live
  container answering `GET /health` and `GET /dashboard` with 200, then
  torn down. `docker compose config` also validated the compose file.

## Pentest-style tests (the plan's explicit ask)

`tests/integration/test_security_pentest.py` scripts five distinct
RCE-style payloads (`os.system`, `subprocess` with `shell=True`, `eval`,
`pickle.loads`, `__import__`) as if the LLM had actually been successfully
jailbroken into emitting them, and confirms:
- The orchestrator never reports them as verified, even across all 3 retry
  attempts (bounded — it doesn't loop forever).
- The retry prompt tells the model what was blocked, and the loop
  successfully recovers once the model "removes" the dangerous construct.
- Through the full API, a blocked attempt returns `200` with
  `status: "failed"` in the body (request handled, generation refused) and
  is recorded in the audit log with the actor and the specific findings.
- A legitimate request is not flagged (no false positive noise in the
  audit trail).

## A design question worth surfacing: this is a blocklist, not a sandbox

The plan asked for both a "Docker execution sandbox for tests" and
"input validation... pentest cases." This session implemented the second
(static blocklist scanning) but not the first (actually *running*
generated code in an isolated container). A blocklist can be evaded by
patterns not on the list (e.g. `getattr(__builtins__, 'ex' + 'ec')`); a
real execution sandbox would catch that by containing the *effect* rather
than pattern-matching the *source*. Given VerityAI doesn't execute
generated code anywhere in its current pipeline (only Z3 static
verification), building a sandbox now would be new infrastructure for a
capability (test execution) that doesn't exist yet, rather than hardening
one that does. Recorded here as a real gap, not silently skipped.

## Test coverage

`test_security_scan.py` (14 tests), `test_security_pentest.py` (5 tests),
`test_rate_limit.py` (4 tests). Full suite: 420 tests passing.

## Phase 4 status: complete

| Part | Status |
|---|---|
| A — API, CLI, SDK | Done |
| B — Compliance & Audit Trail Reports | Done |
| C — Web Dashboard | Done |
| D — Security & Deployment | Done (blocklist scanner + rate limiting + Docker image; execution sandbox is the one open gap, see above) |
