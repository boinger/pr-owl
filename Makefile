.PHONY: venv install dev run test test-cov lint format format-fix format-check version build clean pre-commit integration-test

venv:
	uv venv

install:
	uv sync

dev:
	uv sync --group dev

run:
	uv run pr-owl $(ARGS)

test:
	uv run pytest -m "not integration"

test-cov:
	uv run pytest -m "not integration" --cov=pr_owl --cov-report=term-missing

integration-test:
	uv run pytest -m integration

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

format-fix:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

format-check:
	uv run ruff format --check src/ tests/

version:
	git describe --tags --always --dirty 2>/dev/null || echo "no tags"

build:
	uv build

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache/ .coverage htmlcov/ .ruff_cache/
	rm -f src/pr_owl/_version.py
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

pre-commit: format lint test
