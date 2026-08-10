# VerityAI — Architecture

## What this is

A **model-agnostic agentic harness**: a context, memory and verification layer
around AI coding agents (Claude Code, Codex, Cursor, Aider). It does not
generate code. It manages the environment an agent generates code in.

The problem: agents on long tasks lose context, forget decisions, re-walk dead
ends, hallucinate APIs, and drift from the architecture — and no amount of
model capability fixes a context window that has filled with duplicated tool
output.

> **This repository changed shape on 2026-08-09.** It used to generate code and
> verify it with Z3. Read [ADR-0005](docs/adr/0005-agentic-harness-pivot.md)
> before assuming anything about the old architecture still applies — most of
> it does not, and `docs/PHASE_*.md` describe a system that no longer exists.
> The pre-pivot tree is at tag `pre-harness-pivot`.

---

## Layers

```
┌──────────────────────────────────────────────┐
│ INTERFACE     → CLI + MCP server             │  working
├──────────────────────────────────────────────┤
│ MEASUREMENT   → bench (Family A/B protocol)  │  working
├──────────────────────────────────────────────┤
│ RELIABILITY   → architecture, security       │  working
│                 (over analysis/ AST facts)   │
├──────────────────────────────────────────────┤
│ CONSISTENCY   → claims vs evidence           │  working
├──────────────────────────────────────────────┤
│ KNOWLEDGE     → code graph                   │  working
├──────────────────────────────────────────────┤
│ MEMORY        → .verity/ append-only state   │  working
├──────────────────────────────────────────────┤
│ CONTEXT       → count, classify, prune, rank │  working
├──────────────────────────────────────────────┤
│ CORE          → models, zero dependencies    │  working
└──────────────────────────────────────────────┘
```

`bench/` and `analysis/` are load-bearing, not scaffolding: `bench/` carries
every Family A and Family B number this project publishes (see
`docs/MEASUREMENTS.md`), and `analysis/` supplies the AST facts
`reliability/security.py` reasons over.

## Dependency rule

`core/` depends on nothing but Pydantic, and every engine depends on `core/`.
Beyond that, an engine may depend on another only when the need is real and
declared here — not "nothing depends on anything," but "every cross-engine
edge is a deliberate exception, checked, not accumulated by accident." This
is the one architectural rule carried over from the pre-pivot codebase, where
a neutral `ontology/` broke a circular dependency between the KG and the
symbolic layer. Same principle, different contents.

```
core/                        (no deps)
  ├─ analysis/  (no deps -- pure AST, stdlib only)
  ├─ context/   (core)
  ├─ memory/    (core, context.tokenizer)   -- handoff needs a token budget
  ├─ graph/     (core, context.rank)        -- query.py reuses the BM25 ranker
  ├─ consistency/ (core, graph, context.rank, memory)
  ├─ reliability/ (core, graph, analysis)
  ├─ bench/     (core, context)
  ├─ cli/       (everything)
  └─ mcp/       (everything)
```

`reliability/architecture.py` checks exactly this table against the real
graph, every time — see ADR-0008. The `memory -> context` edge above was
undocumented until that check found it: the diagram said `memory` depended on
`core` alone, but `handoff.py` had already, legitimately, started importing
`context.tokenizer` to fit a document to a token budget. The code was right
and the diagram was stale; the fix was to correct the table, not the import.

`context/` must never import `memory/`. Ranking a context and persisting a
decision are independent operations, and keeping them independent is what lets
each be tested with a plain object instead of a fixture.

---

## Layout

```
src/verityai/
├── core/models.py        Task, Decision, Constraint, Discovery, Failure,
│                         Fact, Evidence, ContextItem, ContextHealth,
│                         PruneResult, Snapshot
├── context/
│   ├── tokenizer.py      TokenCounter — always reports its method
│   ├── ingest.py         transcript (JSON or text) -> ContextItem[]
│   ├── ingest_claude_code.py  Claude Code session JSONL -> ContextItem[]
│   ├── classify.py       five relevance buckets, each with a reason
│   ├── rank.py           BM25 + optional embeddings, fused with RRF
│   ├── prune.py          the 7-stage pipeline
│   └── health.py         multi-dimensional health + rendering
├── memory/
│   ├── store.py          append-only JSONL under .verity/
│   ├── snapshot.py       numbered captures; context only, never code
│   └── handoff.py        the structured handoff document
├── graph/
│   ├── store.py          SQLite; hand-written traversal, no networkx
│   ├── ingest.py         repo walk + AST; Python only, scope declared
│   └── query.py          relationship retrieval; context_for is the point
├── consistency/
│   ├── claims.py         backtick-quoted spans + closed relation phrases
│   └── check.py          symbol/relation/file checks + decision resurfacing
├── reliability/
│   ├── rule_engine.py    forward-chaining engine (carried over from T6)
│   ├── security.py       SQLi + check-then-act races; every rule states its blind spot
│   ├── architecture.py   import-policy check against the real graph
│   └── report.py         shared renderer for both
├── bench/
│   ├── deterministic.py  Family A benchmarks, self-disqualifying
│   └── repetition.py     noise floor / Family B statistics (generalized, ADR-0010)
├── analysis/facts.py     AST fact extraction (carried over from T6)
├── cli/main.py           the verity command
├── mcp/server.py         MCP server — 19 tools over the same core
```

### `.verity/` on disk

```
.verity/
├── config.toml
├── state/{task.json,decisions,constraints,discoveries,failures}.jsonl
├── memory/facts.jsonl
└── snapshots/NNN/snapshot.json
```

JSONL and append-only: it diffs under git, survives without the tool
installed, and keeps superseded decisions readable — which is the only way to
notice an agent re-proposing something already rejected.

---

## Invariants

Enforced in tests, not by convention. Breaking one is a bug.

1. **Critical context is never dropped.** Not by any budget. If the protected
   set exceeds the budget, `PruneResult.budget_met` is `False` and the items
   stay. `critical_retention()` must return `1.0`; the CLI prints `BUG:` if it
   does not.
2. **Every stage records its own token ledger.** `PruneStage` entries must
   chain — each stage's `tokens_before` equals the previous stage's
   `tokens_after`. A gap means a stage changed tokens without recording it.
3. **Every count carries its method.** `TokenCount` is a pair, never an int.
4. **No composite score without its components.** `render_health` prints the
   breakdown first and the score last.
5. **Every degraded path says why.** `RetrievalResult.degraded_reason`,
   `ContextHealth.notes`, `PruneResult.dropped_critical`.
6. **Parsing never loses input.** The parts must sum to the whole, or every
   downstream token figure is wrong at the source.

---

## Where the invariants come from

Each is a research result, not a preference. `docs/RESEARCH_FINDINGS_LEGACY.md`
has the detail; the short version:

- **T3** — the verifiable subset covered 6.1% of HumanEval. Scope creep in a
  converter is invisible until someone measures the whole population.
- **T2** — an improvement was published, then retracted, because
  same-configuration runs disagreed as much as the treatment did. Hence: no
  A/B claim without a noise floor. See `docs/BENCHMARK_PROTOCOL.md`.
- **T1** — the confidence score was uncalibrated and inverted. Hence: no lone
  composite number.
- **T6** — deterministic AST analysis caught what Z3 could not, and exposed a
  function that could never return `FAIL`. Hence: deterministic first, and be
  suspicious of a checker that has never failed anything.

---

## Development

```bash
pytest tests/           # 520 tests, no network, no services, no fixtures needed
ruff check src/ tests/
ruff format src/ tests/
```

- Python 3.10+. `X | None` is fine (PEP 604). The old 3.9 pin -- and its ban
  on that syntax in Pydantic annotations -- is gone with the code that forced it.
- Line length 100, ruff format.
- Tests use plain objects and `tmp_path`. There is nothing to mock — that is a
  property worth protecting when adding engines.

### Adding an engine

1. Models go in `core/models.py`, with no new dependencies.
2. The engine imports `core/` and nothing else from `verityai/`.
3. Deterministic first. If it needs a model call, justify why the question is
   genuinely semantic, and make the model injectable so tests pass a lambda.
4. Any degraded path reports why it degraded.
5. Wire it into `cli/` before `mcp/` — the CLI is how a human reproduces what
   an agent saw.

---

## Contact

Juan Pablo Botero Espinosa · juanpabloboteroespinosa@gmail.com
