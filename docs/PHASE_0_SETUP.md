# Phase 0 Setup Guide

## Overview

Phase 0 establishes a reproducible development environment for VerityAI. This guide walks you through starting services and validating that everything works end-to-end.

## Prerequisites

- **Docker & Docker Compose** — to run Neo4j, Postgres, Redis, Ollama
- **Ollama** — LLM inference engine (`brew install ollama` on macOS)
- **Python 3.11+** — for the VerityAI package
- **8GB+ RAM** — to run all services + llama2:13b

## Step-by-Step Setup

### 1. Copy Environment Configuration
```bash
cd /Users/juanpablo/VerityAI
cp .env.example .env
```

Edit `.env` if you need custom ports or credentials (defaults are fine for local dev).

### 2. Start Docker Services
```bash
# Start all services (Neo4j, Postgres, Redis, Ollama)
docker-compose -f docker/docker-compose.yml up -d

# Wait for services to initialize (~10 seconds)
sleep 10

# Check service health
docker-compose -f docker/docker-compose.yml ps
```

Expected output:
```
CONTAINER ID   IMAGE                   STATUS
abc123         neo4j:5.15-community    Up 10s (healthy)
def456         postgres:15-alpine      Up 10s (healthy)
ghi789         redis:7-alpine          Up 10s (healthy)
jkl012         ollama/ollama:latest    Up 10s
```

### 3. Pull the LLM Model
This is the longest step (~5-10 minutes, ~7.4GB download):

```bash
docker-compose -f docker/docker-compose.yml exec ollama ollama pull llama2:13b

# Wait for completion...
# Expected output:
# pulling 91ef017b0aca... 100%
# verifying sha256 digest
# writing manifest
# success
```

Verify it's available:
```bash
curl http://localhost:11434/api/tags | jq '.models[] | .name'
# Should output: llama2:13b
```

### 4. Install Python Dependencies
```bash
# Using pip (or poetry/uv if you have it)
pip install -e .

# Or with development + research tools:
pip install -e ".[dev,research]"
```

### 5. Run Phase 0 Validation Script

This is the **acceptance criterion for Phase 0** — validates all layers work end-to-end:

```bash
python scripts/setup.py
```

Expected output:
```
============================================================
VerityAI — Phase 0 Setup Validation
============================================================

1. Checking service health...

Service Health Checks:
✓ Neo4j is healthy
✓ Ollama server is available
✓ PostgreSQL is healthy
✓ Redis is healthy

2. Running functional tests...

=== Testing Ollama ===
Ollama server is available
Generating code with llama2:13b...
✓ Generated code:
def add(a, b):
    return a + b

=== Testing Neo4j ===
✓ Connected to Neo4j: Hello, Neo4j!
✓ Created test pattern node in Neo4j
✓ Read pattern back: A test pattern created during Phase 0 setup
✓ Cleaned up test data

============================================================
✓ Phase 0 validation PASSED

VerityAI environment is ready for Phase 1 development.
============================================================
```

If validation passes, **Phase 0 is complete**.

## Accessing Services

### Neo4j Browser
- **URL**: http://localhost:7474
- **Username**: neo4j
- **Password**: verityai_password_123
- Use this to browse the Knowledge Graph

### PostgreSQL
```bash
# Connect via psql
psql -h localhost -U verityai_user -d verityai_db -W
# Password: verityai_pass_123

# Or from Python:
import sqlalchemy
engine = sqlalchemy.create_engine(
    "postgresql://verityai_user:verityai_pass_123@localhost:5432/verityai_db"
)
```

### Redis
```bash
# Connect via redis-cli
redis-cli -h localhost -p 6379

# Ping to verify
redis-cli ping
# Output: PONG
```

### Ollama
```bash
# Check available models
curl http://localhost:11434/api/tags

# Generate text (debug)
curl -X POST http://localhost:11434/api/generate -d '{
  "model": "llama2:13b",
  "prompt": "hello"
}'
```

## Troubleshooting

### Ollama Takes Too Long to Pull Model
- Model is 7.4GB, so pulling takes 5-10 minutes depending on internet speed
- Check download progress with: `docker-compose -f docker/docker-compose.yml logs -f ollama`

### "Service Unhealthy" After docker-compose up
```bash
# Wait longer (sometimes services take 15-20 seconds to be ready)
sleep 30

# Or check logs for errors
docker-compose -f docker/docker-compose.yml logs

# If still failing, restart services
docker-compose -f docker/docker-compose.yml restart
```

### Python Import Error: "No module named verityai"
```bash
# Reinstall in dev mode
pip uninstall verityai -y
pip install -e ".[dev]"

# Verify import works
python -c "from verityai.ontology import models; print('OK')"
```

### Neo4j Connection Refused
```bash
# Check if container is running
docker-compose -f docker/docker-compose.yml ps neo4j

# If not, start it
docker-compose -f docker/docker-compose.yml up -d neo4j

# Wait and retry
sleep 10
python scripts/setup.py
```

### Ollama "Model not found"
```bash
# Ensure model was pulled successfully
docker-compose -f docker/docker-compose.yml exec ollama ollama list
# Should show: llama2:13b

# If not in list, pull again
docker-compose -f docker/docker-compose.yml exec ollama ollama pull llama2:13b
```

## What's Next?

Once Phase 0 validation passes, you're ready for **Phase 1 — Core Infrastructure**:

- Week 1: Walking skeleton (1 algorithm + 3 rules + Z3 wrapper)
- Week 2: Scale KG + rule engine
- Week 3: Debugger + converter improvements
- Week 4: Prompt injection + hardening

See `/Users/juanpablo/.claude/plans/playful-plotting-lollipop.md` for the full plan.

## Development Workflow

### After Setup, Normal Development:
```bash
# Always start services first
docker-compose -f docker/docker-compose.yml up -d

# Write code + tests
vim src/verityai/...
pytest tests/

# Format before committing
black src/ tests/
isort src/ tests/

# Commit
git add -A && git commit -m "message"
```

### Stopping Services
```bash
# Stop but keep data
docker-compose -f docker/docker-compose.yml stop

# Remove everything (careful!)
docker-compose -f docker/docker-compose.yml down -v
```

---

**Phase 0 is complete when `python scripts/setup.py` passes all checks.** ✓
