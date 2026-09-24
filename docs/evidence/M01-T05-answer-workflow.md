# M01-T05 — Minimal Answer workflow evidence

Completed: 2026-09-24

## Invariant

The application workflow accepts validated domain contracts, delegates to the
provider-neutral `ChatModel` interface, and returns an `Answer` without knowing
about OpenAI, retrieval, persistence, or conversation state.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/workflow.py` | Added `AnswerWorkflow` to coordinate one Question-to-Answer call |
| `tests/test_workflow.py` | Added deterministic success and typed model-failure tests using `FakeChatModel` |

The workflow does not inspect provider objects or translate model errors; those
responsibilities remain at the adapter boundary.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_workflow.py
2 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
36 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

`make docs-check` and `git diff --check` also passed. No provider payloads,
secrets, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** The workflow hides
  coordination while exposing one typed Question-to-Answer operation.
- **Which alternative would make the next change harder, and why?** Putting
  provider calls directly in the HTTP route would couple transport code to model
  details and make later adapters harder to add.
- **What production failure would escape if the negative test were removed?**
  Model failures could be swallowed by the workflow and appear as successful,
  empty answers.
