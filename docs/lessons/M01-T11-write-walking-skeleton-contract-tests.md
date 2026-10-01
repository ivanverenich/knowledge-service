# M01-T11 — Write walking-skeleton contract tests

Source task: [M01-T11](../curriculum/milestones/M01-walking-skeleton/M01-T11-write-walking-skeleton-contract-tests.md)

## Outcome

Build one reusable behavioral test suite for the `ChatModel` port and run it
against both `FakeChatModel` and `OpenAIChatModel`. The OpenAI adapter tests must
remain deterministic and credential-free by replacing only its SDK boundary.
Add a separate real-provider smoke test marked `live`, so it cannot run through
the normal local or CI commands.

This matters because a Python `Protocol` mainly helps static type checking. It
does not prove at runtime that two adapters interpret success, failures, and
cancellation in the same way. A contract suite provides that executable proof
without exposing provider-specific response objects to callers.

The invariant inherited from M01-T10 is:

> Every `ChatModel` returns a provider-neutral `Answer` carrying the caller's
> request ID and safe `Usage`, raises a typed `ChatModelError` for model
> failures, and lets `asyncio.CancelledError` propagate unchanged.

## Plan

1. Record the invariant and the expected initial failure.
2. Create factories for the fake and managed adapters.
3. Run one success/failure/cancellation suite against both factories.
4. Add an explicitly selected live smoke test.
5. Run the narrow and repository-wide verification commands.
6. Record evidence and answer the reflection questions.

## Step 1 — Start the evidence record

**Purpose:** Make the intended behavior and the red-to-green prediction
explicit before writing a test. This prevents the test from merely describing
whatever the implementation happens to do.

**Decision:** Use `docs/evidence/M01-T11-walking-skeleton-contract-tests.md` as
the work log. Preserve the provider-neutral invariant above; do not add OpenAI
SDK types or fields to `ChatModel`, `Answer`, or `Usage`.

**Action:** Create the evidence file with this initial content:

```markdown
# M01-T11 — Walking-skeleton contract tests evidence

Completed: pending

## Invariant

Every ChatModel returns a provider-neutral Answer carrying the caller's request
ID and safe Usage, raises a typed ChatModelError for model failures, and lets
asyncio.CancelledError propagate unchanged.

## Prediction

The first narrow command will fail because
tests/test_chat_model_contract.py does not exist yet. After the contract suite
is added, both deterministic adapter cases should pass without credentials or
network access.

## Verification

Pending.

## Reflection

Pending.
```

Then run the deliberately red command:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chat_model_contract.py
```

**Check:** Pytest should report that the file or directory was not found. Put a
short summary of that observed failure under `## Verification`; do not paste a
large terminal transcript.

## Step 2 — Build adapter factories at the external seam

**Purpose:** Construct both implementations through the `ChatModel` interface.
The OpenAI case should exercise all of `OpenAIChatModel` while substituting the
network-facing `AsyncOpenAI` client. This is a fake at the external seam, not a
mock of the adapter's private methods.

**Decision:** Add one new file, `tests/test_chat_model_contract.py`. Represent
each adapter with a small `AdapterCase` containing factories for success,
failure, and a blocked call. Factories create fresh mutable state for every
test, avoiding cross-test leakage.

**Action:** Start `tests/test_chat_model_contract.py` with the imports, SDK
boundary fake, and factories below:

```python
"""Behavioral contract tests shared by every ChatModel adapter."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx2
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from fakes import FakeChatModel
from knowledge_service.contracts import Answer, Question
from knowledge_service.model import ChatModel, ChatModelError
from knowledge_service.openai_model import OpenAIChatModel, OpenAISettings

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000011")


class StubResponses:
    """Replace only AsyncOpenAI.responses for deterministic adapter tests."""

    def __init__(self, result: Any = None, *, block: bool = False) -> None:
        self._result = result
        self._block = block

    async def parse(self, **_kwargs: Any) -> Any:
        if self._block:
            await asyncio.Event().wait()
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class StubClient:
    def __init__(self, responses: StubResponses) -> None:
        self.responses = responses


def openai_model(responses: StubResponses) -> ChatModel:
    settings = OpenAISettings(
        model_api_key=SecretStr("test-key"),
        model_name="test-model",
        model_timeout_seconds=5.0,
    )
    client = cast(AsyncOpenAI, StubClient(responses))
    return OpenAIChatModel(client, settings)


def openai_success() -> ChatModel:
    response = SimpleNamespace(
        output=[],
        output_parsed=SimpleNamespace(text="A managed answer"),
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
    )
    return openai_model(StubResponses(response))


def openai_failure() -> ChatModel:
    request = httpx2.Request("POST", "https://example.test")
    return openai_model(StubResponses(openai.APITimeoutError(request)))


def openai_blocked() -> ChatModel:
    return openai_model(StubResponses(block=True))


@dataclass(frozen=True)
class AdapterCase:
    name: str
    success: Callable[[], ChatModel]
    failure: Callable[[], ChatModel]
    blocked: Callable[[], ChatModel]


CASES = (
    AdapterCase(
        name="fake",
        success=FakeChatModel,
        failure=lambda: FakeChatModel(raise_error=True),
        blocked=lambda: FakeChatModel(wait_for=asyncio.Event()),
    ),
    AdapterCase(
        name="openai",
        success=openai_success,
        failure=openai_failure,
        blocked=openai_blocked,
    ),
)
```

`cast(AsyncOpenAI, StubClient(...))` tells the static checker that the tiny test
double stands in for the much larger SDK client. It does not transform the
object at runtime. This is acceptable at the test-only external boundary: the
adapter touches only `client.responses.parse`, which the stub supplies.

The OpenAI failure starts as an SDK-specific `APITimeoutError`; the adapter must
translate it into a subclass of the project-owned `ChatModelError`. The fake
uses a different subtype (`ChatModelUnavailable`). The shared contract asserts
the stable base type instead of incorrectly requiring every backend failure to
have the same cause.

**Check:** Read `CASES` from top to bottom. Each case must expose the same three
operations and return a `ChatModel`. There must be no real API key and no real
network client in these factories.

## Step 3 — Add the shared behavior suite

**Purpose:** Execute the same observable expectations for both adapters. The
test names describe port behavior, rather than implementation details.

**Decision:** Parameterize each test over `CASES`. Test success, the important
asynchronous edge case (caller cancellation), and one typed failure. Assert
only project-owned values and exceptions.

**Action:** Append this helper and these three tests to the same file:

```python
def assert_success_contract(answer: Answer) -> None:
    assert answer.request_id == REQUEST_ID
    assert answer.text.strip()
    assert answer.usage.provider
    assert answer.usage.model
    assert answer.usage.latency_ms >= 0
    assert answer.usage.input_tokens >= 0
    assert answer.usage.output_tokens >= 0
    assert answer.usage.total_tokens >= 0


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_success_contract(case: AdapterCase) -> None:
    answer = await case.success().answer(
        Question(text="What behavior does the port promise?"),
        request_id=REQUEST_ID,
        timeout_seconds=2.0,
    )

    assert_success_contract(answer)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_typed_failure_contract(case: AdapterCase) -> None:
    with pytest.raises(ChatModelError):
        await case.failure().answer(
            Question(text="Trigger a model failure"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_cancellation_contract(case: AdapterCase) -> None:
    task = asyncio.create_task(
        case.blocked().answer(
            Question(text="Cancel this request"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
```

`asyncio.create_task` starts the model call independently. `await
asyncio.sleep(0)` yields once to let it reach the blocking await. `task.cancel()`
then injects `CancelledError` at that await point. The final assertion proves
neither adapter converts caller cancellation into an ordinary provider error.

Do not assert `provider == "openai"`, inspect `StubResponses`, or check SDK call
arguments here. Those are OpenAI adapter tests and already belong in
`tests/test_openai_model.py`; they are not promises shared by every
`ChatModel`.

**Check:** Run the new deterministic suite:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chat_model_contract.py -m "not live"
```

Expected result: six tests pass—three behaviors for each of two adapters—and no
credential is required.

## Step 4 — Add the opt-in live smoke test

**Purpose:** Provide a narrow way to check that the managed provider still
honors the success contract, without putting network calls, cost, or secrets in
the deterministic suite.

**Decision:** Mark the test `live`, read `OPENAI_API_KEY` only inside the test,
and skip when it is absent. Close the SDK client in `finally` so its HTTP
resources are released even if the assertion or request fails. Keep the prompt
harmless and small.

**Action:** Append this test:

```python
@pytest.mark.live
async def test_openai_live_success_contract() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    settings = OpenAISettings(
        model_api_key=SecretStr(api_key),
        model_name="gpt-4.1-mini",
        model_timeout_seconds=30.0,
    )
    try:
        answer = await OpenAIChatModel(client, settings).answer(
            Question(text="Reply with one short sentence about testing."),
            request_id=REQUEST_ID,
            timeout_seconds=30.0,
        )
    finally:
        await client.close()

    assert_success_contract(answer)
```

The repository already registers `live` and sets the default pytest expression
to `not live` in `pyproject.toml`. Both `make test-unit` and CI also exclude
`live`. Do not put a key in `.env.example`, a test fixture, shell history,
evidence, or a committed file.

Run the normal command first and confirm the live test is deselected:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chat_model_contract.py
```

Only if you intentionally want to spend a provider call and already exported a
real key, run the exact live node with the default marker expression overridden:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest \
  tests/test_chat_model_contract.py::test_openai_live_success_contract -m live
```

**Check:** The ordinary command reports six passed and one deselected. The live
test must never be needed to accept this task; if explicitly run without a key,
it should skip rather than fail or search for credentials elsewhere.

## Step 5 — Run verification in increasing scope

**Purpose:** Find local mistakes quickly before paying for the full quality
gate, then prove the new file respects formatting, lint, typing, and existing
behavior.

**Decision:** Run the narrow contract suite first, followed by the repository's
existing commands. Do not run `make format` until you have inspected the new
file; it changes files, while `format-check` is read-only.

**Action:** Run these commands in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chat_model_contract.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff format --check tests/test_chat_model_contract.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff check tests/test_chat_model_contract.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright tests/test_chat_model_contract.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

If formatting fails, run this focused formatter, inspect the diff, and repeat
the checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff format tests/test_chat_model_contract.py
```

**Check:** The narrow run has six passed and one deselected. All non-live tests
and quality gates pass. Search the diff for accidental secrets or provider
payloads before proceeding:

```bash
git diff -- docs/evidence/M01-T11-walking-skeleton-contract-tests.md \
  tests/test_chat_model_contract.py
```

The diff may contain the literal test value `test-key`; it must not contain a
real credential, private question, or raw response captured from a live call.

## Step 6 — Finish the evidence and reflection

**Purpose:** Demonstrate understanding and leave a concise, durable record for
the milestone gate and the next task.

**Decision:** Record summaries and exact pass/deselect counts, not entire logs.
Describe the seam in your own words.

**Action:** Replace `Completed: pending`, `## Verification`, and
`## Reflection` in the evidence file. Include:

```markdown
## What changed

| File | Change |
|---|---|
| `tests/test_chat_model_contract.py` | Shared success, typed-failure, and cancellation behavior across the fake and OpenAI adapters; added an opt-in live smoke test. |

## Verification

- `<narrow command>` — 6 passed, 1 deselected.
- `<format command>` — passed.
- `<lint command>` — passed.
- `<type command>` — passed.
- `make check` — record the actual pass/deselect counts.
- `make docs-check` — passed.
- `git diff --check` — passed.

No real credential, private content, or raw provider payload entered tests or
evidence. The deterministic suite made no network request.

## Reflection

- **What complexity did this task hide from its caller?** `<your explanation>`
- **Which alternative would make the next change harder, and why?** `<your explanation>`
- **What production failure would escape if your negative test were removed?** `<your explanation>`
```

Use your own explanations for the three reflection answers. A useful answer
should mention concrete repository behavior—for example, SDK exception
translation or cancellation—not only say that abstractions make testing easier.

**Check:** Compare the implementation and evidence against every item in the
completion checklist below. Do not update `docs/curriculum/PROGRESS.md` or
commit until the task has been reviewed and accepted.

## Completion checklist

- [ ] The invariant and initial failure prediction are recorded.
- [ ] One parameterized suite runs success, typed failure, and cancellation
      behavior against both `FakeChatModel` and `OpenAIChatModel`.
- [ ] Shared assertions mention only project-owned contracts, not OpenAI SDK
      response fields or private adapter state.
- [ ] Deterministic tests use a fake SDK boundary, require no credential, and
      make no network calls.
- [ ] The real-provider smoke test is marked `live`, skipped without a key, and
      absent from normal local and CI runs.
- [ ] The narrow suite, formatting, lint, Pyright, `make check`, documentation
      validation, and `git diff --check` all pass.
- [ ] Evidence contains actual results and no secrets, private content, or raw
      provider payloads.
- [ ] All three reflection questions are answered in your own words.
- [ ] The original M01-T11 acceptance criteria are satisfied.

When these are complete, share the implementation or the first failing command
for review. The intended checkpoint after acceptance is:
`m01-t11: write walking skeleton contract tests`.
