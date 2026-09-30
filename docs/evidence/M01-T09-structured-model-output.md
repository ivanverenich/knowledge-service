# M01-T09 — Structured model output evidence

Completed: 2026-09-30

## Invariant

The OpenAI adapter requests and validates one provider-neutral structured answer,
then returns the existing `Answer` with its request ID and usage. Provider-specific
response parsing and error details remain inside the adapter.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/contracts.py` | Added `GeneratedAnswer` with a non-empty text field. |
| `src/knowledge_service/model.py` | Added typed refusal and structured-output capability failures; clarified malformed-response coverage. |
| `src/knowledge_service/openai_model.py` | Uses the Responses API parser, maps parsed answer text, and translates refusal, invalid-data, and narrowly identified unsupported-capability cases. |
| `tests/test_openai_model.py` | Covers structured success and metadata, schema validation, refusal, invalid output, unsupported structured output, and propagation of unrelated bad requests. |

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_openai_model.py
16 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
Ruff format and lint passed
Pyright: 0 errors, 0 warnings, 0 informations
77 passed, 1 deselected
Total coverage: 92%

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed, including deterministic unit tests

$ git diff --cached --check && git diff --check
passed
```

No secrets, provider payloads, or private documents entered the evidence or test fixtures.

## Reflection

- **What complexity did structured output hide from its caller?** Structured output hides parsing, schema validation, refusals, and capability failures behind one stable application-level result.
- **Which alternative would make the next change harder, and why?** Returning raw model text would make the next change harder because every caller would need to understand and validate provider-specific output.
- **What production failure might escape if the negative tests were removed?** Malformed output, refusals, or unsupported-capability responses could reach production as if they were valid answers.
