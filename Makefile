.PHONY: setup format format-check lint typecheck test-unit test-integration check

setup:
	uv sync --locked

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run pyright

test-unit:
	uv run pytest -m "not integration and not live"

test-integration:
	uv run pytest -m "integration and not live"

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov=knowledge_service --cov-report=term-missing