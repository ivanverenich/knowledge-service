# M01-T02 — Question and Answer contracts evidence

Completed: 2026-09-24

## Invariant

Transport data is validated at the application boundary with stable,
provider-neutral Pydantic models. Invalid input fails before a workflow or
external model adapter runs.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/contracts.py` | Added documented `Question`, `Answer`, `Usage`, and `ErrorResponse` models |
| `tests/test_contracts.py` | Added valid, invalid, typed-validation, and generated-OpenAPI schema tests |
| `pyproject.toml` / `uv.lock` | Recorded the FastAPI and HTTPX dependencies used by the transport boundary and tests |

The contracts contain no provider-specific request or response types. Question
length and usage token values are validated with Pydantic constraints, and
request IDs use UUID values.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_contracts.py
7 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
25 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check` and the generated OpenAPI schema assertions also passed. No
secrets, provider payloads, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** Pydantic models hide
  parsing and boundary validation behind stable Python types and OpenAPI shapes.
- **Which alternative would make the next change harder, and why?** Passing
  unvalidated dictionaries into workflows would duplicate validation and make
  provider adapters define competing contracts.
- **What production failure would escape if the negative test were removed?**
  Empty questions or negative usage values could reach downstream workflows and
  produce misleading answers or accounting data.
