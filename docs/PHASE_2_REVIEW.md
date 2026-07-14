# Phase 2 Review — Agentic Loop + Integrated Improvements

## Overview

Phase 2 built the generate → verify → retry orchestration loop and the
integrated improvements planned for it: Continuous Learning Loop and
Interactive Refinement Mode, plus the Z3 + human-approval gate that lets a
learned rule actually reach the Knowledge Graph. Compliance & Audit Trail
Reports remain Phase 4 scope, as planned.

## What shipped, by week

### Week 1 — Agentic orchestrator core
- `agent/state.py`: `AgentState` (code, attempt#, violations, history)
- `agent/orchestrator.py`: generate → verify → inject failure reason → retry (max 3)
- `agent/confidence.py`: weighted confidence score
- Tests: `tests/unit/test_orchestrator.py`, `tests/integration/test_orchestrator_e2e.py`

### Week 2 — Traceability + refinement infrastructure
- `agent/trace.py`: `TraceStore` (Postgres via SQLAlchemy, injected `Session`) + JSON serialization for API/CLI
- `agent/refinement.py`: `IncrementalVerifier` — caches Z3 results per function, re-verifies only functions whose source changed
- `agent/session.py`: `ConversationSession` ties `Orchestrator` + `IncrementalVerifier` across conversation turns
- Tests: `test_trace.py`, `test_refinement.py`, `test_session.py`

### Week 3 — Refinement intents + continuous learning capture
- `agent/refinement.py`: `parse_refinement_intent()` — keyword classifier; `show_proof`/`explain` skip the LLM and re-verification entirely, answering from the last turn's already-computed result
- `agent/continuous_learning.py`: `FeedbackStore` (accept/reject/correct) + `derive_candidate_rule()`
- `tests/integration/test_week3_e2e.py`: scripted 3-turn conversation with an explicit assert that only the changed function was re-verified, and that the proof turn made zero LLM calls

### Week 4 — Z3 validation gate + human approval + phase close-out
- `symbolic/verify.py`: `verify_python_snippet()` extracted from `Orchestrator.verify_code` so the same AST→Z3 check backs both code verification and rule validation
- `ontology/models.py`: `Rule.test_code` — already used by `kg/ingestion.py` and the seed data, now part of the Pydantic schema; it's the executable snippet a candidate rule is screened against
- `agent/rule_validation.py`: `validate_candidate_rule()` (Z3 consistency gate) + `RuleApprovalQueue` (human sign-off; auto-rejects Z3-contradictory candidates, but a human can override)
- `kg/ingestion.py`: `KGIngestion.ingest_learned_rule()` — MERGE-based write for an approved candidate (idempotent, unlike the CREATE-based bulk seed loader)
- `tests/integration/test_continuous_learning_e2e.py`: the full loop — orchestrator run → reject/correct feedback → candidate rule → Z3 gate → approval queue → KG ingestion (faked Neo4j driver), plus the auto-reject path
- `tests/integration/test_latency_e2e.py`: confirms the retry loop is genuinely sequential in wall-clock time, plus a closed-form SLA check against the plan's documented ~30-35 tok/s llama2:13b throughput

## Acceptance criteria (plan's hardened bar) — status

- ✅ Scripted 3+ turn conversation converging to verified code, with incremental re-verification proven (not just implemented) — `test_week3_e2e.py`
- ✅ Simulated production failure that updates the KG after passing Z3 validation + human approval — `test_continuous_learning_e2e.py`
- ⚠️ Latency check uses closed-form estimates against the plan's own throughput numbers, not a live-Ollama benchmark — no live model in CI (see Known Gaps)

## Known gaps / deliberately deferred

- **No live Neo4j/Ollama in CI.** `KGIngestion.ingest_learned_rule` and the latency SLA are exercised against fakes/closed-form math, not the real services. Run a manual smoke test against `docker-compose up` before relying on this in production.
- **`validate_candidate_rule` can only screen rules that carry `test_code`.** REJECT feedback with a `reason` but no `corrected_code` produces a candidate that's `UNVERIFIABLE` by construction — it still reaches the approval queue, just without a Z3 opinion. Intentional (ADR-0001's "degrade explicitly, don't silently pass"), but most of the safety net depends on users supplying `corrected_code`.
- ~~Function-parameter binding gap in the AST→Z3 converter~~ — **fixed in Phase 3** (see `docs/adr/0002-parameterized-verification.md`). `ast_to_smt.py` now binds parameters as free Z3 variables and checks parameter-referencing asserts for validity (via `Z3Engine.verify_property`) rather than satisfiability; the fix also uncovered and corrected a related pre-existing bug where `if`/`else` branches that both assign the same variable weren't properly phi-merged.
- **LangChain is used only at the neural layer** (`langchain_community.llms.Ollama` in `ollama_client.py`), not as an agent/tool-chain wrapping KG + Z3 the way the original plan sketched ("Integración LangChain: Ollama + KG query + Z3 como tools/chain"). The orchestrator calls `KGClient`/`Z3Engine` directly — a deliberate simplification for a deterministic 3-step loop, flagged here as a documented deviation rather than an oversight.
- **`FeedbackStore` and `RuleApprovalQueue` are in-memory only.** They mirror `TraceStore`'s injected-session pattern in shape but aren't backed by Postgres yet — fine for Phase 2's scope, but production use needs persistence so feedback/approvals survive a restart.

## Test coverage

231 tests passing (`pytest tests/ -q`), spanning unit + integration layers across everything delivered in Phases 0-2.

## Next: Phase 3 — Evaluation Framework

Per the plan: adapt TruthfulQA's methodology to code (100 correct + 100
intentionally-buggy snippets), build security/correctness benchmarks,
compare three baselines (raw llama2:13b, llama2:13b+Z3 with no retry, full
VerityAI), and fix a target threshold up front — not just "the comparison
happened."
