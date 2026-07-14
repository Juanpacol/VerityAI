.PHONY: test lint format typecheck docker-build serve clean

# python3 -m ... rather than bare `ruff`/`mypy`/`pytest`: those tools land in
# user site-packages (not necessarily on PATH) when installed via
# `pip install -e ".[dev]"` without a venv, which is exactly how this
# project's dev environment was set up.
test:
	python3 -m pytest tests/ --cov=verityai --cov-report=term-missing --cov-fail-under=85

lint:
	python3 -m ruff check src/ tests/
	python3 -m ruff format --check src/ tests/

format:
	python3 -m ruff check --fix src/ tests/
	python3 -m ruff format src/ tests/

typecheck:
	python3 -m mypy src/verityai

docker-build:
	docker build -t verityai:latest .

serve:
	python3 -m uvicorn verityai.api.rest:app --reload --host 0.0.0.0 --port 8000

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml
