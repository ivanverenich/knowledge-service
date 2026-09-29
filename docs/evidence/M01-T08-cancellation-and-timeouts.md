# M01-T08 — Cancellation and timeout evidence

Completed: 2026-09-29

## Invariant

The answer workflow owns one finite, positive deadline and passes its remaining
budget boundary to the model adapter. The adapter's provider timeout cannot
exceed the workflow budget. Workflow deadline expiry becomes a typed dependency
failure, while caller cancellation and a model-originated `TimeoutError`
propagate unchanged. Work stopped by timeout or cancellation runs cleanup.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/workflow.py` | Added finite positive deadline validation, workflow timeout enforcement, and translation of only the workflow's own expiry to `AnswerDeadlineExceeded` |
| `src/knowledge_service/model.py` | Added a provider-neutral timeout budget to the `ChatModel` contract |
| `src/knowledge_service/openai_model.py` | Bounds the provider request by the smaller workflow/configured timeout and rejects invalid configured values |
| `src/knowledge_service/errors.py` | Classifies `AnswerDeadlineExceeded` as a dependency failure |
| `tests/fakes.py` | Added a controlled slow model fake with observable start and cleanup events |
| `tests/test_workflow.py` | Tests success, typed failures, deadline cleanup, external cancellation cleanup, raw model timeout propagation, and invalid deadlines |
| `tests/test_openai_model.py` | Tests timeout budget forwarding, provider timeout translation, cancellation, and invalid settings |
| `tests/test_app.py` | Tests that a workflow deadline returns the safe public dependency response |
| `tests/test_model.py`, `tests/test_errors.py` | Updated model contract calls and deadline classification coverage |

Both workflow and provider settings reject NaN, positive and negative infinity,
zero, and negative timeout values. The model adapter receives a budget no
larger than the workflow's configured deadline. If the workflow deadline
expires, the public API returns the provider-neutral 503 dependency response.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_workflow.py tests/test_openai_model.py tests/test_app.py tests/test_errors.py
47 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
Ruff format and lint passed
Pyright: 0 errors, 0 warnings
72 passed, 1 deselected
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

- **What complexity does the deadline policy hide from callers?** It hides
  timeouts across services inside the app, so clients do not need to manage
  each timeout themselves.
- **Which design choice would make the next change harder, and why?** Letting
  every layer choose an independent timeout would make deadline behavior
  inconsistent and difficult to reason about.
- **What production failure could go unnoticed without these tests?** A timeout
  or cancellation could stop the request while underlying work keeps running
  or cleanup is skipped.
