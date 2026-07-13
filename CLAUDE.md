# VerityAI — Architecture Documentation

## Overview

VerityAI is a neuro-symbolic code verification system that:
1. Generates code using Ollama (llama2:13b) with dynamic context from a Knowledge Graph
2. Verifies it formally using Z3 Theorem Prover + symbolic reasoning
3. Retries up to 3 times if verification fails (injecting the failure reason into the prompt)
4. Returns code + reasoning trace + confidence score

**Business Problem**: Enterprises don't trust AI-generated code because they can't see WHY it's correct. VerityAI solves this by showing formal proof + explanation, not just code.

---

## Architecture (6 Logical Layers)

```
┌─────────────────────────────────────────────────────┐
│ 6. INTERFACE   → CLI + REST API + Web Dashboard      │
├─────────────────────────────────────────────────────┤
│ 5. ORCHESTRATION → LangChain Agent (loop)            │
├─────────────────────────────────────────────────────┤
│ 4. VERIFICATION → confidence score + explanation     │
├─────────────────────────────────────────────────────┤
│ 3. SYMBOLIC    → Z3 + rule engine                    │
├─────────────────────────────────────────────────────┤
│ 2. KNOWLEDGE   → Neo4j (patterns, rules, examples)   │
├─────────────────────────────────────────────────────┤
│ 1. NEURAL      → Ollama (llama2:13b) + prompts       │
└─────────────────────────────────────────────────────┘
```

### Request Flow
1. User asks for code → Agent queries KG for relevant rules/patterns
2. Ollama generates code + step-by-step reasoning (with KG context injected)
3. Verification layer runs Z3 + symbolic rules
4. If fail → Agent retries with failure reason (max 3 attempts)
5. If pass → return code + trace + confidence

---

## Module Dependency Graph

**Critical Rule**: `ontology/` has ZERO dependencies on neo4j/z3/llm — it's pure Pydantic models.
This breaks the circular dependency: KG needs to validate rules against symbolic (Continuous Learning),
while symbolic needs KG schema.

```
ontology/ (no deps)
  ├─ neural/ (depends: ontology)
  ├─ kg/ (depends: ontology, neo4j)
  ├─ symbolic/ (depends: ontology, z3)
  └─ agent/ (depends: neural, kg, symbolic, langchain)
       ├─ evaluation/
       ├─ compliance/
       ├─ api/
       └─ cli/
```

---

## Key Design Decisions

### 1. No Fine-tuning
- Instead: dynamic context injection in prompts
- Rules from KG are injected into the prompt at runtime
- Allows rule updates without retraining
- Cost: $0/month vs. $52K/year for fine-tuned model

### 2. Single Python Package (not 5 separate packages)
- Avoids semver burden for one developer
- Breaks circular dependency via neutral `ontology/`
- Can split into separate packages in Phase 4 if needed

### 3. "Verifiable Python Subset" (ADR-0001)
- Z3 can't automatically infer loop invariants or handle recursion
- Define what IS verifiable upfront (linear code + bounded loops with explicit invariants + types)
- Mark code outside subset as "not verified" (degradation), not "verification failed"
- Prevents silent scope creep in the AST→Z3 converter

### 4. Walking Skeleton First (not waterfall)
- Build end-to-end pipeline with 1 algorithm + 3 rules first
- Then scale seed data in lotes with regression testing
- This validates the shape of data before writing 50+ rules

---

## Directory Structure

```
VerityAI/
├── pyproject.toml                       # Python dependencies
├── docker-compose.yml                   # Services: Neo4j, Postgres, Redis, Ollama
├── .env.example                         # Config template
├── CLAUDE.md                            # This file
├── docs/
│   └── adr/
│       └── 0001-verifiable-python-subset.md
├── research/                            # Cloned reference repos (non-importable)
│   ├── neuro-symbolic-ai-toolkit/
│   ├── kg-deductive-reasoner/
│   ├── truthfulqa/
│   ├── neuralkg/
│   └── sccipher/
├── src/verityai/
│   ├── ontology/models.py               # Pydantic: Rule, Pattern, Algorithm, etc. (NO DEPS)
│   ├── neural/
│   │   ├── ollama_client.py
│   │   ├── prompt_builder.py
│   │   └── model_config.py
│   ├── kg/
│   │   ├── neo4j_client.py
│   │   ├── schema.py
│   │   ├── nl_to_cypher.py
│   │   ├── ingestion/
│   │   └── seed_data/
│   ├── symbolic/
│   │   ├── z3_engine.py
│   │   ├── ast_to_smt.py
│   │   ├── rule_engine.py
│   │   ├── counterexample.py
│   │   └── debugger.py
│   ├── agent/
│   │   ├── orchestrator.py
│   │   ├── state.py
│   │   ├── confidence.py
│   │   ├── trace.py
│   │   ├── session.py
│   │   ├── refinement.py
│   │   └── continuous_learning.py
│   ├── evaluation/
│   ├── compliance/
│   ├── api/rest.py
│   └── cli/verityai_cli.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── scripts/
    └── setup.py
```

---

## Integrated Improvements (4 of 10)

### Mejora 2 — Continuous Learning Loop (Phase 2)
- Production feedback (accept/reject/correct) updates KG with new rules
- No retraining needed
- Rules validated against Z3 before ingestion

### Mejora 4 — Interactive Refinement Mode (Phase 2)
- Multi-turn conversation: "make it thread-safe", "show me the proof"
- Incremental verification (only re-verify changed code)
- Session state preserved

### Mejora 5 — Compliance & Audit Trail Reports (Phase 4)
- PDF/SARIF exports showing security rules applied, confidence, verification proof
- Audit log: who generated, when, what changed
- Enterprise sales enabler

### Mejora 8 — Symbolic Debugging Mode (Phase 1/2)
- When verification fails, extract minimal counterexample
- Map back to source line + suggest fix
- "Teach the user why code is wrong, not just 'it's wrong'"

---

## Getting Started (Phase 0)

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Ollama installed locally (`brew install ollama` on macOS)

### Quick Start
```bash
# 1. Clone the repo (already done)
cd /Users/juanpablo/VerityAI

# 2. Copy environment config
cp .env.example .env

# 3. Start services
docker-compose -f docker/docker-compose.yml up -d

# 4. Pull the model (takes ~5-10 min, ~7.4GB)
docker-compose -f docker/docker-compose.yml exec ollama ollama pull llama2:13b

# 5. Verify services are healthy
docker-compose -f docker/docker-compose.yml ps

# 6. Run the setup script (Phase 0 deliverable)
python scripts/setup.py
```

---

## Development Notes

### Coding Style
- Black (line length 100)
- Type hints required for public APIs
- Tests in `tests/{unit,integration,e2e}/` with pytest

### Before Committing
```bash
black src/ tests/
isort src/ tests/
pytest tests/
```

### Key Files to Watch During Development
- `/src/verityai/ontology/models.py` — the schema that everything depends on
- `/src/verityai/symbolic/ast_to_smt.py` — the hardest piece (ADR-0001 defines its scope)
- `/src/verityai/agent/orchestrator.py` — the retry loop that ties everything together

---

## References

- Plan: `/Users/juanpablo/.claude/plans/playful-plotting-lollipop.md`
- Source repos (in `research/`):
  - IBM Neuro-Symbolic Toolkit → patterns for rule_engine + LLM integration
  - kg-deductive-reasoner → counterexample generation
  - TruthfulQA → benchmark methodology
  - NeuralKG → KG design patterns
  - scCIPHER → ETL pipeline inspiration

---

## Contact / Questions

Juan Pablo Botero Espinosa  
juanpabloboteroespinosa@gmail.com

Generated with Claude Code (Claude Fable + Sonnet for architecture review)
