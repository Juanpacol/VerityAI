# ADR-0005: Repositioning VerityAI as an agentic harness

- **Status**: Accepted
- **Date**: 2026-08-09
- **Supersedes**: the product thesis behind ADR-0001, ADR-0002, ADR-0003, ADR-0004
  (their engineering reasoning stands; the system they served no longer exists)

## Context

VerityAI generated code with an LLM and verified it with Z3. The T1–T6 research
programme, recorded in `RESEARCH_FINDINGS_LEGACY.md`, was run to find out
whether that worked. It concluded, largely, that it did not:

- **T3** measured the verifiable Python subset against the full HumanEval and
  MBPP corpora using the real `ASTtoSMTConverter`: **6.1% and 9.4% coverage**.
  Adding Z3 String theory moved MBPP by 6 problems out of 974. This is a census,
  not a sample, and it is the most solid number the project has ever produced.
- **T2** retracted the retry-loop improvement. Same-configuration runs on
  different days disagreed 50% of the time against 55% for the supposed
  treatment effect — indistinguishable from `temperature=0.7` sampling noise.
- **T1** found the confidence score uncalibrated (ECE 0.14–0.50) and, in the
  `single_shot_z3` configuration, inverted: near-zero-confidence `FAIL` verdicts
  were 75% correct code, while `NOT_VERIFIED` abstentions were 12.5% accurate.
- **T4** found no accuracy return on growing the rule corpus from 10 to 48.
- **T5** was never run.

One result pointed the other way. **T6** showed that deterministic AST fact
extraction plus a forward-chaining rule engine caught SQL injection and
check-then-act race conditions that Z3 structurally cannot reach — and in
building it, exposed a real bug: `apply_rule_to_code` was incapable of ever
returning `FAIL`, so it had been reporting `PASS` on genuinely vulnerable code.

Read together, T3 and T6 say something specific. Formal proof over arbitrary
generated code does not scale past a tenth of real programs. Deterministic
analysis over project structure, with the LLM confined to genuinely ambiguous
judgements, does work. The differentiator was never the theorem prover.

Meanwhile the product had no defensible position: it competed with Claude,
Codex and Cursor at code generation — the thing they are best at — while
offering proofs about 6% of the output.

## Decision

VerityAI becomes a **model-agnostic agentic harness**: a context, memory and
verification layer wrapped around AI coding agents rather than a system that
replaces them. It does not generate code. It manages the environment the agent
generates code in.

Four engines, built in order, each gated on the previous one working:

1. **Context Engine** — token accounting, relevance classification, pruning,
   multi-dimensional health.
2. **Memory / Handoff Engine** — append-only task state that survives a context
   reset, and a structured handoff document.
3. **Knowledge Graph** — the project's real structure: files, functions,
   imports, tests, decisions.
4. **Consistency + Reliability Engines** — agent claims checked against that
   graph; changes checked against architecture, tests and security policy.

The neuro-symbolic core survives, inverted. Not *LLM generates, Z3 proves*, but
*LLM proposes, deterministic analysis over the graph checks, evidence justifies*.

### Consequences

**Removed**: `neural/`, the Z3 stack (`z3_engine`, `ast_to_smt`, `verify`,
`counterexample`), the generation orchestrator and its retry loop,
`compliance/`, the T5 study apparatus, the KG's textbook seed data, and roughly
500 tests. Recoverable from tag `pre-harness-pivot`.

**Kept**: BM25+RRF ranking (corpus-agnostic all along, now ranks context items);
the event/SSE observability stack; `security_facts.py` and `rule_engine.py`
from T6; `repetition.py`, which encodes the standing rule that made T2's
retraction possible.

**Dependencies** drop from langchain, neo4j, z3-solver, ollama, reportlab,
psycopg2, redis and fastapi to pydantic, typer, rich and python-dotenv. A tool
that manages someone else's context must install next to any agent without
dragging in an LLM SDK or a solver.

**Cost**: the formal-verification work is written off as a product, retained as
a finding. `docs/RESEARCH_FINDINGS_LEGACY.md` stays citable — T3 and T6 are the
public justification for this decision, and the negative results are the most
valuable thing the project has produced.

## Rules carried forward

The four standing rules from the research programme apply unchanged to the
harness, and one is added:

1. No A/B attribution without a same-configuration repeat establishing the
   noise floor first.
2. "No effect detected" must be checked against that floor before it is said.
3. Deterministic first; the LLM only where the question is genuinely semantic.
4. Every degraded path reports *why* it degraded — never silently returns a
   worse answer.
5. **New:** every token count travels with the method that produced it, and no
   composite score is displayed without its components. T1 is what a lone
   authoritative-looking number does when nobody can audit it.
