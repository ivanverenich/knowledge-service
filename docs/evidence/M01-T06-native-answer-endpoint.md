# M01-T06 — Native Answer endpoint evidence

Completed: 2026-09-24

## Invariant

The HTTP boundary translates requests into domain contracts and translates
known workflow failures into stable `ErrorResponse` payloads. Provider objects
and SDK exceptions do not cross the route boundary.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/app.py` | Added `POST /v1/answer`, request-ID handling, validation-error handling, and typed error responses |
| `tests/test_app.py` | Added HTTP tests for success, validation failure, workflow unavailability, timeout, rate limiting, and correlation IDs |

The endpoint accepts a JSON `Question`, injects `AnswerWorkflow`, and returns
an `Answer` on success. Known failures return `ErrorResponse` with a stable
status code and the same request ID.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_app.py
8 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
41 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check` and `git diff --check` also passed. No secrets, provider
payloads, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** The route hides
  request parsing, correlation-ID handling, status mapping, and error payload
  construction.
- **Which alternative would make the next change harder, and why?** Returning
  raw exceptions or provider responses would force every client to understand
  internal implementation details.
- **What production failure would escape if the negative test were removed?**
  Invalid requests or model failures could receive misleading success responses
  or inconsistent error formats.
