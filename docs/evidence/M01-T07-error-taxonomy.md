# M01-T07 — Error taxonomy evidence

Completed: 2026-09-29

## Invariant

Every application failure maps to one stable, provider-neutral public policy
and one telemetry outcome. Cancellation remains control flow: the HTTP boundary
re-raises it instead of manufacturing a response after the caller has gone.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/errors.py` | Added failure categories, retry ownership, telemetry outcomes, public policies, and exception classification |
| `src/knowledge_service/app.py` | Routed validation and workflow failures through the shared taxonomy while preserving cancellation |
| `tests/test_errors.py` | Added table-driven classification and policy tests for every failure category |
| `tests/test_app.py` | Added public-response coverage for dependency, capacity, authorization, and internal failures plus cancellation propagation |

The public API now exposes project-owned error codes and safe messages rather
than provider exception types or text. Validation is owned by the caller,
dependency retry by the service, capacity retry by the caller, and
authorization, cancellation, and internal failures have no automatic retry
owner.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_app.py -q
11 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
Ruff format and lint passed
Pyright: 0 errors, 0 warnings
57 passed, 1 deselected
Total coverage: 92%

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed

$ git diff --check
passed
```

No secrets, provider payloads, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** Callers receive one
  stable error contract without needing to understand provider exceptions,
  retry ownership, or telemetry classification.
- **Which alternative would make the next change harder, and why?** Keeping
  exception-specific response logic in each route would duplicate policy and
  allow public messages, status codes, and telemetry outcomes to drift apart.
- **What production failure would escape if the negative test were removed?**
  Cancellation could be converted into a misleading HTTP response, or an
  unexpected exception could leak implementation details instead of returning
  the safe internal-error contract.
