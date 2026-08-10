.PHONY: test lint format typecheck check dogfood clean

# `python3.11 -m ...` rather than bare `ruff`/`mypy`/`pytest`: these tools land
# in user site-packages (not necessarily on PATH) when installed via
# `pip install -e ".[dev]"` without a venv, and this machine's default
# `python3` is 3.9 -- below the project's `requires-python = ">=3.10"`.
PY := python3.11

test:
	$(PY) -m pytest tests/ --cov=verityai --cov-report=term-missing

lint:
	$(PY) -m ruff check src/ tests/
	$(PY) -m ruff format --check src/ tests/

format:
	$(PY) -m ruff check --fix src/ tests/
	$(PY) -m ruff format src/ tests/

typecheck:
	$(PY) -m mypy src/verityai

# Everything CI runs, in the same order.
check: lint typecheck test

# The harness checking itself: build the code graph over this repo and
# validate CLAUDE.md's import policy against it (ADR-0008).
dogfood:
	verity graph build .
	verity reliability architecture

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml
