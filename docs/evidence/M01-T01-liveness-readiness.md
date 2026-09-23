# M01-T01 — Liveness and readiness evidence

Completed: 2026-09-23

## Invariant

The process health signal must remain independent from external AI providers.
Liveness reports that the application process responds; readiness reports
whether an injected dependency check permits traffic.

## What changed

| File | Change |
|---|---|
| `pyproject.toml` / `uv.lock` | Added FastAPI and HTTPX test dependencies |
| `src/knowledge_service/app.py` | Added `create_app()` with `/health/live` and `/health/ready` |
| `tests/test_app.py` | Added async ASGI-interface tests for live, ready, and not-ready responses |

The readiness check is an async callable injected into the factory. A failed
check returns `503` without contacting an AI provider or exposing provider
details.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_app.py
3 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
18 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check` and `git diff --check` also passed. No secrets, provider
payloads, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** The application
  factory hides route registration and the ASGI wiring while exposing only the
  health contract.
- **Which alternative would make the next change harder, and why?** Calling
  providers directly from health routes would make deployment health depend on
  AI availability and make local tests slow and nondeterministic.
- **What production failure would escape if the negative test were removed?**
  A broken readiness dependency could incorrectly return healthy and receive
  traffic it cannot serve.
