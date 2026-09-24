# M01-T03 — ChatModel port evidence

Completed: 2026-09-24

## Invariant

Application code depends on a provider-neutral asynchronous model interface.
Provider request formats, SDKs, and transport errors stay behind that seam;
structured answers, usage, cancellation, and typed failures remain observable.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/model.py` | Added the async `ChatModel` protocol and typed model errors |
| `tests/fakes.py` | Added a deterministic fake adapter with success, failure, and blocking behavior |
| `tests/test_model.py` | Added success, typed-failure, and cancellation tests through the protocol |

The fake adapter uses no provider SDK, network, credentials, or external
service. Cancellation is allowed to propagate as `asyncio.CancelledError`.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_model.py
3 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
28 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check`, Ruff, Pyright, and `git diff --check` also passed. No
provider payloads, secrets, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** Callers depend on one
  async method while adapters hide provider protocols and response formats.
- **Which alternative would make the next change harder, and why?** Calling a
  provider SDK directly from workflows would couple application logic to one
  vendor and make deterministic tests require network behavior.
- **What production failure would escape if the negative test were removed?**
  Provider outages could be translated inconsistently, or cancellation could
  be swallowed and leave requests running after the client disconnects.
