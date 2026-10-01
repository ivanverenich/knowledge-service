# M01-T11 — Walking-skeleton contract tests evidence

Completed: 2026-10-01

## Invariant

Every `ChatModel` returns the provider-neutral `Answer` with the caller's
request ID and safe `Usage`, reports model failures as `ChatModelError`, and
propagates caller cancellation as `asyncio.CancelledError`.

## Prediction

Before the suite existed, the narrow pytest command would fail because
`tests/test_chat_model_contract.py` was missing. After adding it, the fake and
managed adapter boundary should pass the same success, typed-failure, and
cancellation checks without credentials or network access.

## What changed

| File | Change |
|---|---|
| `tests/test_chat_model_contract.py` | Parameterized success, typed-failure, and cancellation contract tests run against `FakeChatModel` and `OpenAIChatModel`; a separate live smoke test is explicitly marked `live`. |
| `docs/lessons/M01-T11-write-walking-skeleton-contract-tests.md` | Added the task guide and aligned its SDK timeout fixture with the locked OpenAI SDK's `httpx2.Request` type. |

The shared tests assert only project-owned `Answer`, `Usage`, and
`ChatModelError` behavior. The OpenAI adapter uses a stub at
`responses.parse`; no HTTP request is made by deterministic tests. The live
smoke test closes its client and stays excluded by the default pytest marker,
the unit-test command, and CI.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chat_model_contract.py` — 6 passed, 1 live test deselected.
- Focused Ruff format check, Ruff lint, and Pyright checks for the contract test — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — format, lint, and Pyright passed; 89 tests passed, 2 live tests deselected; 93% coverage.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks` — all hooks passed, including deterministic unit tests and secret scanning.
- `git diff --cached --check` and `git diff --check` — passed before completion records were updated.

No real credential, private content, or raw provider response entered the
tests or evidence. The only key value is the synthetic `test-key` fixture.

## Reflection

- **What complexity did this task hide from its caller?** The caller receives the same project-owned `Answer` contract even though the managed adapter parses provider output, maps safe usage fields, and translates provider timeouts into a project-owned typed failure.
- **Which alternative would make the next change harder, and why?** Requiring callers to handle OpenAI response objects and exceptions would couple the workflow to one provider and force later adapters to reproduce SDK details instead of the behavior the application needs.
- **What production failure would escape if your negative test were removed?** A provider timeout could escape as an SDK exception instead of `ChatModelError`, bypassing the service's stable failure handling and making behavior differ between adapters.
