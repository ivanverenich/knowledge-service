# M01-T04 — OpenAI chat adapter evidence

Completed: 2026-09-24

## Invariant

The managed provider remains behind the provider-neutral `ChatModel` seam.
Provider responses and SDK failures are translated into the project's
structured answer, usage, timeout, rate-limit, malformed-response, and
cancellation behavior.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/openai_model.py` | Added the async OpenAI Responses adapter with model configuration, timeout, request ID propagation, usage mapping, and typed error translation |
| `tests/test_openai_model.py` | Added deterministic fake-client tests for success, API-key redaction, malformed output, timeout, rate limit, and cancellation |
| `pyproject.toml` / `uv.lock` | Added the OpenAI SDK dependency and locked environment update |

The tests use an in-memory fake of the SDK boundary. No provider request,
credential, or network call is made.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_openai_model.py
6 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
34 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check` and `git diff --check` also passed.

## Reflection

- **What complexity did this task hide from its caller?** The adapter hides
  provider request construction, response usage extraction, timeout settings,
  and SDK exception classes.
- **Which alternative would make the next change harder, and why?** Calling
  the OpenAI SDK directly from workflows would spread provider-specific logic
  across the application and make deterministic tests dependent on the network.
- **What production failure would escape if the negative test were removed?**
  Timeouts, rate limits, malformed responses, or cancelled requests could be
  misclassified and produce unsafe retry or user-facing behavior.
