# VerityAI

Neuro-symbolic code verification: **LLM-generated code + formal proofs + explainability**.

## Problem Statement

Enterprises don't trust AI-generated code because they can't see **why** it's correct.

VerityAI solves this by:
1. **Generating** code with an LLM (Ollama llama2:13b)
2. **Verifying** it formally with Z3 Theorem Prover
3. **Retrying** up to 3 times if verification fails
4. **Explaining** the reasoning trace + confidence score

Result: Code that enterprises can defend in audits, not just "here's code".

## Quick Start

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Ollama installed (`brew install ollama` on macOS)

### 1. Start Services
```bash
cd /Users/juanpablo/VerityAI

# Copy environment template
cp .env.example .env

# Start Docker services (Neo4j, Postgres, Redis, Ollama)
docker-compose -f docker/docker-compose.yml up -d

# Wait ~10 seconds for services to start
sleep 10

# Pull the model (~5-10 min, ~7.4GB download)
docker-compose -f docker/docker-compose.yml exec ollama ollama pull llama2:13b

# Verify services are healthy
docker-compose -f docker/docker-compose.yml ps
```

### 2. Install Python Dependencies
```bash
# Using pip (or poetry/uv if you prefer)
pip install -e .

# Or for development + research tools
pip install -e ".[dev,research]"
```

### 3. Validate Phase 0 Setup
```bash
# Run the setup script (acceptance criterion for Phase 0)
python scripts/setup.py
```

Expected output:
```
✓ Neo4j is healthy
✓ Ollama server is available
✓ Generated code: def add(a, b): return a + b
✓ Connected to Neo4j
✓ Phase 0 validation PASSED
```

## Architecture

See `CLAUDE.md` for full architecture documentation.

High-level flow:
```
User asks for code
    ↓
Agent queries KG for rules + patterns
    ↓
Ollama generates code (with context injected)
    ↓
Z3 verifies symbolically
    ↓
If fail → retry (max 3x)
    ↓
Return code + trace + confidence
```

## Development

### Project Structure
```
VerityAI/
├── src/verityai/           # Main package
│   ├── ontology/           # Pydantic models (zero infrastructure deps)
│   ├── neural/             # LLM client (Ollama)
│   ├── kg/                 # Knowledge Graph (Neo4j)
│   ├── symbolic/           # Symbolic engine (Z3 + rules)
│   ├── agent/              # Orchestration (LangChain)
│   ├── evaluation/         # Benchmarks + metrics
│   ├── compliance/         # Reports (PDF/SARIF)
│   ├── api/                # REST API (FastAPI)
│   └── cli/                # CLI (Typer)
├── tests/                  # Unit + integration + E2E tests
├── research/               # Reference repos (non-importable)
├── docker/                 # Docker Compose
└── docs/adr/               # Architecture decision records
```

### Coding Style
```bash
# Format code
black src/ tests/
isort src/ tests/

# Run tests
pytest tests/

# Type checking
mypy src/
```

### Before Committing
```bash
make test  # or: pytest tests/ -v
```

## Key Design Decisions

1. **No Fine-tuning**: Rules injected as prompt context (not frozen in model weights)
2. **Single Package**: Monorepo structure, not 5 separate packages
3. **Verifiable Subset**: Define upfront what Python constructs Z3 can verify
4. **Walking Skeleton First**: Build 1 algorithm + 3 rules end-to-end, then scale
5. **Integrated Improvements**: Continuous Learning + Interactive Refinement + Compliance Reports built-in

See `CLAUDE.md` for rationale.

## Roadmap

- **Phase 0** (Weeks 1-2): Foundation ← **YOU ARE HERE**
- **Phase 1** (Weeks 3-6): Core infrastructure (KG + Symbolic Engine)
- **Phase 2** (Weeks 7-10): Agentic loop + improvements
- **Phase 3** (Weeks 11-14): Evaluation framework + benchmarks
- **Phase 4** (Weeks 15-20): Productization (API + CLI + Dashboard)

Full plan: `/Users/juanpablo/.claude/plans/playful-plotting-lollipop.md`

## Docker Compose Services

```
neo4j       (localhost:7687)  — Knowledge Graph
postgres    (localhost:5432)  — Trace storage, audit logs
redis       (localhost:6379)  — Caching, sessions
ollama      (localhost:11434) — LLM inference
```

### View Logs
```bash
docker-compose -f docker/docker-compose.yml logs -f neo4j
docker-compose -f docker/docker-compose.yml logs -f ollama
# etc.
```

### Stop Services
```bash
docker-compose -f docker/docker-compose.yml down
```

## Troubleshooting

### Ollama not responding
```bash
# Check if service is running
docker-compose -f docker/docker-compose.yml ps ollama

# View logs
docker-compose -f docker/docker-compose.yml logs ollama

# Manually pull model
docker-compose -f docker/docker-compose.yml exec ollama ollama pull llama2:13b
```

### Neo4j won't connect
```bash
# Reset Neo4j data
docker-compose -f docker/docker-compose.yml down
rm -rf neo4j_data/  # Remove volume
docker-compose -f docker/docker-compose.yml up -d neo4j
```

### Python import errors
```bash
# Reinstall in development mode
pip install -e ".[dev]"
```

## Contributing

1. Create a branch: `git checkout -b feature/xyz`
2. Write code + tests
3. Format: `black src/ tests/`
4. Test: `pytest tests/`
5. Commit: `git commit -m "feat: xyz"`
6. Push: `git push origin feature/xyz`

## References

- Architecture: `CLAUDE.md`
- Plan: `/Users/juanpablo/.claude/plans/playful-plotting-lollipop.md`
- Implementation guide: `docs/adr/0001-verifiable-python-subset.md` (coming in Phase 1)

## Contact

Juan Pablo Botero Espinosa  
juanpabloboteroespinosa@gmail.com

---

**Generated with Claude Code** — Claude Fable for development, Claude Sonnet for architecture review.
