# Makefile for the knowledge_service RAG project.
#
# The single command you need to remember is `make do_good`: it prints a menu of
# every documented command below and runs whichever one you pick. Individual
# targets keep working exactly as before (CI and pre-commit call them directly),
# and a bare `make` opens the same menu.

# Bare `make` shows the command menu.
.DEFAULT_GOAL := do_good

.PHONY: do_good setup format format-check lint typecheck test-unit test-integration check hooks-install hooks docs-check

# ---------------------------------------------------------------------------
# Command menu
# ---------------------------------------------------------------------------

# Interactive launcher. It lists every target that carries a `## description`,
# so to add an item to the menu you only document the target:
#
#   my-target: ## Short description shown in the menu
#   	the command to run
#
# This target has no `##` description on purpose, so it never lists itself.
do_good:
	@cmds="$$(grep -E '^[a-zA-Z0-9_.-]+:.*## ' Makefile | sed -E 's/^([^:]+):[[:space:]]*##[[:space:]]*(.*)$$/\1\t\2/')"; \
	if [ -z "$$cmds" ]; then \
		echo "  No documented commands found."; \
		exit 1; \
	fi; \
	total=$$(printf '%s\n' "$$cmds" | wc -l | tr -d ' '); \
	printf '\n  Available commands:\n\n'; \
	printf '%s\n' "$$cmds" | awk -F'\t' '{ printf "   %2d) %-18s %s\n", NR, $$1, $$2 }'; \
	printf '\n   %2d) %-18s %s\n' 0 "quit" "Exit without running anything"; \
	printf '\n  Choose a command: '; \
	read -r choice; \
	case "$$choice" in \
		""|0|q|Q) printf '\n  Cancelled.\n'; exit 0 ;; \
	esac; \
	case "$$choice" in \
		*[!0-9]*) printf '\n  "%s" is not a valid choice.\n' "$$choice"; exit 1 ;; \
	esac; \
	if [ "$$choice" -lt 1 ] || [ "$$choice" -gt "$$total" ]; then \
		printf '\n  "%s" is out of range (1-%s).\n' "$$choice" "$$total"; \
		exit 1; \
	fi; \
	target=$$(printf '%s\n' "$$cmds" | sed -n "$${choice}p" | cut -f1); \
	printf '\n  Running: make %s\n\n' "$$target"; \
	$(MAKE) --no-print-directory "$$target"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup: ## Create/refresh the local virtualenv from the lockfile
	uv sync --locked

# ---------------------------------------------------------------------------
# Formatting and linting
# ---------------------------------------------------------------------------

format: ## Auto-format the code with ruff
	uv run ruff format .

format-check: ## Check formatting without changing files
	uv run ruff format --check .

lint: ## Run ruff lint checks
	uv run ruff check .

typecheck: ## Run pyright static type checks
	uv run pyright

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test-unit: ## Run fast deterministic unit tests (no network)
	uv run pytest -m "not integration and not live"

# Integration tests skip themselves without KNOWLEDGE_SERVICE_TEST_DATABASE_URL.
# When it is unset, the URL is built from the local Docker container, so the
# password is read at run time and never stored in a file.
TEST_DB_CONTAINER ?= knowledge-service-postgres

test-integration: ## Run integration tests (needs services)
	@url="$$KNOWLEDGE_SERVICE_TEST_DATABASE_URL"; \
	if [ -z "$$url" ]; then \
		url="$$(uv run python scripts/test_database_url.py "$(TEST_DB_CONTAINER)")"; \
	fi; \
	if [ -z "$$url" ]; then \
		echo "No test database: set KNOWLEDGE_SERVICE_TEST_DATABASE_URL or start the $(TEST_DB_CONTAINER) container."; \
		exit 1; \
	fi; \
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

# ---------------------------------------------------------------------------
# Combined checks and git hooks
# ---------------------------------------------------------------------------

check: ## Run the full local gate (format, lint, types, tests + coverage)
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov=knowledge_service --cov-report=term-missing

hooks-install: ## Install the git pre-commit hooks
	uv run pre-commit install

hooks: ## Run every pre-commit hook against all files
	uv run pre-commit run --all-files

docs-check: ## Validate the documentation under docs/
	uv run python scripts/validate_docs.py
