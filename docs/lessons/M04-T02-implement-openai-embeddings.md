# M04-T02 — Implement OpenAI embeddings

Source task: [M04-T02](../curriculum/milestones/M04-dense-retrieval/M04-T02-implement-openai-embeddings.md)

## What you will do

M04-T01 defined the `EmbeddingModel` port and a fake. This lesson adds the first real adapter: `OpenAIEmbeddingModel`, which turns one `embed` call into one request to OpenAI's embeddings endpoint and keeps every promise of the port.

| Requirement | How this lesson meets it |
|---|---|
| Configured model and dimensions | `OpenAIEmbeddingSettings`: `text-embedding-3-small` with 1536 dimensions by default; the dimensions are sent with every request and are part of the model identity. |
| Batching | One call sends its whole batch as one request, within the endpoint's limits (2048 inputs, 8192 tokens each, 300,000 tokens per request). Splitting many Chunks into batches is M04-T03. |
| Deadlines | The shorter of the caller's and the configured timeout, enforced around the whole call. The SDK's hidden retries are turned off, so they cannot stretch it. |
| Retry classification | Every SDK failure becomes one port error whose class says who retries: the service, the caller, or nobody. |
| Usage | Input tokens from the response, measured latency, and an optional price ID. |
| Safe logging | One log record per call with counts, timing, and the outcome, never text; no port error chains the SDK error, which holds the request body. |
| Contract tests | One suite runs against both the fake and the OpenAI adapter: ordering, token-limit rejection, rate limits, wrong dimensions, cancellation, and a bad deadline. |

This lesson includes coding: Steps 2–6 write code, while Steps 1 and 7 do not. No test calls the real API; an opt-in live test does, only when you set `OPENAI_API_KEY` and ask for it.

Terms used in this lesson:

- **Adapter:** the class that implements a port for one provider.
- **Stub:** a stand-in for the provider's client that returns prepared answers, so tests run offline and the same way every time.
- **Contract test:** one test that runs against every adapter of a port, so all adapters keep the same promises.
- **Retry owner:** who should try again after a failure: the service (soon), the caller (after slowing down), or nobody (a configuration problem).

## Before you begin

- **Why the SDK's retries are turned off.** The OpenAI client retries some failures twice by default, silently. Then a 10-second deadline can take 30 seconds, and a rate limit never reaches the batcher that should slow down. The adapter calls `client.with_options(max_retries=0)` and keeps the copy it returns; M04-T03 owns retrying.
- **Why errors are not chained.** An SDK error keeps the HTTP request, and the request's body holds every input text. Raising a port error `from` it, or inside its `except` block, would attach that error, and with it the document text, to anything that logs the exception. The adapter remembers the failure and raises outside the `except` block.
- **Why tokens are counted as bytes.** OpenAI's tokenizer turns at least one byte into each token, so a text never has more tokens than UTF-8 bytes. Counting bytes needs no tokenizer download and can never under-count, so the adapter never sends a request that is too large. It is conservative: Ukrainian letters are two bytes each.
- **What the adversarial review found.** An independent review drove the first version through the real OpenAI SDK, with a mocked network transport and no real requests. Ordering, the deadline, cancellation, and log safety held. It found four breaks, all fixed in the code below with a test that fails without the fix:
  - A deadline of NaN (`float("nan")`) from a caller crashed the whole event loop inside `asyncio.timeout`, not just the call.
  - The SDK does not validate a successful response body, so a 200 with plain text, an empty object, or missing usage escaped as a raw `AttributeError` or `TypeError`, unlogged.
  - HTTP 408 (request timeout) and 409 (conflict) were treated as "refused, never retry", although both are passing failures.
  - The test stub's `with_options` returned the stub itself, so a change that dropped the configured client, turning the SDK's retries back on, passed every test.
- **An existing logging bug this lesson fixes.** `migrations/env.py` called `fileConfig(...)`, which by default switches off every logger that already exists. Any process that ran migrations in-process, including the test suite, silently lost the application's log records. The new logging test caught it.
- **Files you will touch:**
  - `src/knowledge_service/embeddings.py` and `src/knowledge_service/errors.py` (one new error)
  - new `src/knowledge_service/openai_embeddings.py`
  - `migrations/env.py`
  - `tests/fakes.py`, `tests/test_errors.py`, and new `tests/openai_stubs.py`, `tests/test_embedding_model_contract.py`, `tests/test_openai_embeddings.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone. No database or API key is needed.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

**Invariant:** the OpenAI adapter keeps every promise of the `EmbeddingModel` port: one vector per input, matched to its input by the response's index, or one port error and nothing. A failure never takes longer than the deadline, never retries on its own, and never carries input text in its message, arguments, or chained exceptions. Every call leaves exactly one log record with counts and the outcome, and no text.

**Action:** create `docs/evidence/M04-T02-openai-embeddings.md`:

```markdown
# M04-T02 — OpenAI embeddings evidence

Completed: pending

## Invariant

Pending.

## Expected failing check

Before writing code, say which test fails first, and how, once the tests
exist but `openai_embeddings.py` does not. Then predict what `embed` returns
or raises when the provider: answers with items in reverse order; answers
429, 408, 409, 500, 401, or 400; answers 200 with a body that is not JSON;
answers with vectors of the wrong length; takes longer than the deadline.
Also predict what happens to an input over the token limit, and to a
deadline of NaN.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and predictions.

### Step 2 — Coding step: a port error that says "do not retry"

The port has errors for passing failures (timeout, unavailable, rate limit) but none for "the provider refused this request". A wrong API key, an unknown model, or dimensions the model does not support will fail the same way every time, so retrying only wastes time and money.

| Where | Change |
|---|---|
| `embeddings.py` | New `EmbeddingRequestRefused(EmbeddingError)`. |
| `errors.py` | `classify_failure` maps it to `INTERNAL`: HTTP 500, retried by nobody. It is a subclass of `EmbeddingError`, so it is checked before the general `DEPENDENCY` branch. |
| `tests/test_errors.py` | One new row. |

**Action:** in `src/knowledge_service/embeddings.py`, after `EmbeddingMalformedResponse`, add:

```python
class EmbeddingRequestRefused(EmbeddingError):
    """The provider refused the request itself; sending it again cannot help.

    For example a wrong API key, an unknown model, or dimensions the model
    does not support: a configuration problem, not a passing failure.
    """
```

In `src/knowledge_service/errors.py`, add `EmbeddingRequestRefused,` to the `knowledge_service.embeddings` import and, right after the `EmbeddingInputRejected` check in `classify_failure`, add:

```python
    if isinstance(error, EmbeddingRequestRefused):
        return FailureKind.INTERNAL
```

In `tests/test_errors.py`, add `EmbeddingRequestRefused,` to the embeddings import and this row after the `EmbeddingRateLimited` row:

```python
        (EmbeddingRequestRefused("bad key"), FailureKind.INTERNAL),
```

**Check:** `uv run pytest tests/test_errors.py -q` gives 19 passed.

### Step 3 — Coding step: the adapter

New module `src/knowledge_service/openai_embeddings.py`.

| Part | What it does |
|---|---|
| `OpenAIEmbeddingSettings` | Model, dimensions, timeout, optional price ID, and the three limits, each validated (positive, non-empty). No environment prefix, like `OpenAISettings`. |
| `__init__` | Keeps `client.with_options(max_retries=0)`, the configured copy, and builds the identity and limits from the settings. |
| `count_tokens` | UTF-8 byte length: a safe upper bound. |
| `embed` | Refuses a NaN deadline; checks the inputs (logging a refusal); returns an empty batch for no inputs; otherwise sends one request under `asyncio.timeout(deadline)`, translates any failure, and reads the response without trusting it. |
| `_batch_from` | Reads `data` and `usage.prompt_tokens` with `getattr`: the SDK does not validate a successful body, so a malformed one must become `EmbeddingMalformedResponse`, not a raw `AttributeError`. |
| `_translated` | The retry classification table below. |
| `_ordered_vectors` | Puts each vector at its input's position using the item's `index`; a missing, duplicate, or out-of-range index is malformed. |
| `_log` | One record named `knowledge_service.openai_embeddings`, with `extra={"embedding": {...}}`: request ID, provider, model, dimensions, input count, latency, input tokens, and outcome. |

How each SDK failure is translated:

| SDK failure | Port error | Retry owner |
|---|---|---|
| Deadline passed, `APITimeoutError`, HTTP 408 | `EmbeddingTimeout` | service |
| `APIConnectionError`, HTTP 5xx, HTTP 409 | `EmbeddingUnavailable` | service |
| HTTP 429 | `EmbeddingRateLimited` | caller |
| Any other HTTP status (400, 401, 403, 404, 413, 422) | `EmbeddingRequestRefused` | nobody |
| A body that is not JSON, or an unreadable response | `EmbeddingMalformedResponse` | service |

`match` with class patterns (`case openai.RateLimitError():`) checks `isinstance`, so more specific classes come first. `openai.APIStatusError(status_code=408)` matches an `APIStatusError` whose `status_code` attribute is 408.

**Action:** create `src/knowledge_service/openai_embeddings.py`:

```python
"""OpenAI implementation of the provider-neutral EmbeddingModel port.

One call to `embed` is one request to the embeddings endpoint. The adapter
adds what the port cannot know:

- **Configured model and dimensions.** `text-embedding-3` models can shorten
  their vectors; the configured dimensions are sent with every request and
  are part of the model identity.
- **A deadline that includes retries.** The SDK's own retries are turned off,
  so a timeout means what it says and a rate limit reaches the caller, which
  owns retrying a batch (M04-T03).
- **Retry classification.** Each SDK error becomes one port error:
  `EmbeddingTimeout` or `EmbeddingUnavailable` (retry later),
  `EmbeddingRateLimited` (retry after slowing down), or
  `EmbeddingRequestRefused` (do not retry: fix the configuration).
- **An untrusted response.** The SDK does not validate a successful body, so
  the adapter reads `data` and `usage` itself; anything unexpected becomes
  `EmbeddingMalformedResponse`, never a raw `AttributeError`.
- **Safe logging.** One log record per call, sent or refused, with counts,
  timing, and the outcome; never text. Port errors are raised without
  chaining the SDK error, because the SDK error holds the HTTP request, and
  the request holds every input text.
"""

import asyncio
import logging
import math
import time
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import openai
from openai import AsyncOpenAI
from pydantic import Field
from pydantic_settings import BaseSettings

from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
    EmbeddingUsage,
    check_inputs,
    checked_batch,
)

logger = logging.getLogger(__name__)

PROVIDER = "openai"


class OpenAIEmbeddingSettings(BaseSettings):
    """Settings for the OpenAI embedding adapter.

    The limits default to the endpoint's documented maximums: 2048 inputs,
    8192 tokens per input, and 300,000 tokens per request.
    """

    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    embedding_price_id: str | None = None
    embedding_max_inputs: int = Field(default=2048, gt=0)
    embedding_max_input_tokens: int = Field(default=8192, gt=0)
    embedding_max_batch_tokens: int = Field(default=300_000, gt=0)


class OpenAIEmbeddingModel:
    """Translate EmbeddingModel calls to the OpenAI embeddings endpoint."""

    def __init__(self, client: AsyncOpenAI, settings: OpenAIEmbeddingSettings) -> None:
        # Retries are the caller's job; hidden ones would stretch the deadline.
        self._client = client.with_options(max_retries=0)
        self._settings = settings
        self._identity = EmbeddingModelIdentity(
            provider=PROVIDER,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        self._limits = EmbeddingLimits(
            max_inputs=settings.embedding_max_inputs,
            max_input_tokens=settings.embedding_max_input_tokens,
            max_batch_tokens=settings.embedding_max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        """Return an upper bound: the text's length in UTF-8 bytes.

        OpenAI's tokenizer turns at least one byte into each token, so a text
        never has more tokens than bytes. The bound is exact for nothing but
        safe for everything, and needs no tokenizer download.
        """
        return len(text.encode("utf-8"))

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error)
            raise
        if not texts:
            return EmbeddingBatch(
                identity=self._identity,
                vectors=(),
                usage=EmbeddingUsage(input_tokens=0, latency_ms=0),
            )
        deadline = min(timeout_seconds, self._settings.embedding_timeout_seconds)
        started_at = time.perf_counter()
        response: object = None
        failure: EmbeddingError | None = None
        try:
            async with asyncio.timeout(deadline):
                response = await self._client.embeddings.create(
                    model=self._settings.embedding_model,
                    input=list(texts),
                    dimensions=self._settings.embedding_dimensions,
                    encoding_format="float",
                    timeout=deadline,
                    extra_headers={"X-Request-ID": str(request_id)},
                )
        except (TimeoutError, openai.APIError) as error:
            failure = _translated(error)
        except ValueError:
            # A successful status with a body that is not JSON.
            failure = EmbeddingMalformedResponse("the provider response was not JSON")
        latency_ms = round((time.perf_counter() - started_at) * 1_000)
        try:
            if failure is not None:
                # Raised outside the SDK's except block: its error holds the request.
                raise failure
            batch = self._batch_from(response, len(texts), latency_ms)
        except EmbeddingError as error:
            _log(request_id, self._identity, len(texts), latency_ms, error)
            raise
        _log(request_id, self._identity, len(texts), latency_ms, None, batch.usage)
        return batch

    def _batch_from(
        self, response: object, expected: int, latency_ms: int
    ) -> EmbeddingBatch:
        """Read the response without trusting its shape."""
        data = getattr(response, "data", None)
        tokens = getattr(getattr(response, "usage", None), "prompt_tokens", None)
        if not isinstance(data, list):
            raise EmbeddingMalformedResponse("the response has no list of vectors")
        if type(tokens) is not int or tokens < 0:
            raise EmbeddingMalformedResponse("the response has no token usage")
        return checked_batch(
            self._identity,
            _ordered_vectors(cast(list[object], data), expected),
            EmbeddingUsage(
                input_tokens=tokens,
                latency_ms=latency_ms,
                price_id=self._settings.embedding_price_id,
            ),
            expected=expected,
        )


def _translated(error: TimeoutError | openai.APIError) -> EmbeddingError:
    """Map one SDK failure to the port error that says whether to retry."""
    match error:
        case (
            TimeoutError()
            | openai.APITimeoutError()
            | openai.APIStatusError(status_code=408)
        ):
            return EmbeddingTimeout()
        case openai.RateLimitError():
            return EmbeddingRateLimited()
        case (
            openai.APIConnectionError()
            | openai.InternalServerError()
            | openai.ConflictError()
        ):
            return EmbeddingUnavailable()
        case openai.APIStatusError():
            return EmbeddingRequestRefused(
                f"the provider refused the request with status {error.status_code}"
            )
        case _:
            return EmbeddingMalformedResponse("the provider response was not readable")


def _ordered_vectors(data: Sequence[object], expected: int) -> list[object]:
    """Put each vector at its input's position, using the index it came with."""
    by_index: dict[int, object] = {}
    for item in data:
        index = getattr(item, "index", None)
        if type(index) is not int or not 0 <= index < expected or index in by_index:
            raise EmbeddingMalformedResponse("the response indexes do not match")
        by_index[index] = getattr(item, "embedding", None)
    if len(by_index) != expected:
        raise EmbeddingMalformedResponse(
            f"expected {expected} vectors, received {len(by_index)}"
        )
    return [by_index[index] for index in range(expected)]


def _log(
    request_id: UUID,
    identity: EmbeddingModelIdentity,
    inputs: int,
    latency_ms: int,
    error: EmbeddingError | None,
    usage: EmbeddingUsage | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "dimensions": identity.dimensions,
        "inputs": inputs,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if usage is not None:
        fields["input_tokens"] = usage.input_tokens
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "embedding request finished", extra={"embedding": fields})
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: keep the application's loggers during migrations

`logging.config.fileConfig` has `disable_existing_loggers=True` by default: every logger created before the call stops emitting. `tests/test_migrations.py` runs `migrations/env.py` in the test process, so after it, the adapter's log records vanish, and so would any application log in a process that runs migrations.

**Action:** in `migrations/env.py`, replace the `fileConfig` line with:

```python
    # Keep the application's loggers: by default fileConfig disables them all.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
```

**Check:** `uv run pytest tests/test_migrations.py -q` still passes.

### Step 5 — Coding step: test helpers

Two helpers make the adapter testable without a network.

| Helper | What it gives the tests |
|---|---|
| `FakeEmbeddingModel(returned_dimensions=...)` | The fake answers with vectors of another length, so the contract can check that wrong dimensions are refused by both adapters. The fake also refuses a NaN deadline, like the adapter. |
| `tests/openai_stubs.py` | `StubEmbeddings` stands in for `AsyncOpenAI.embeddings`: it records each request and returns a prepared response, an error, a delay, or blocks. Its default response lists items in **reverse** order, each with its index, so ordering is always exercised. `StubClient.with_options` returns a configured copy, and only the copy reaches the stub; an adapter that kept the original client would fail. `status_error` builds the SDK error for a status, with a request whose body holds the input text, like the real one. |

**Action:** in `tests/fakes.py`, change `FakeEmbeddingModel`: add the parameter `returned_dimensions: int | None = None,` after `wait_for`, and in `__init__`, after `self._wait_for = wait_for`, add:

```python
        # Pretend the provider answered with vectors of another length.
        self._returned_dimensions = returned_dimensions or dimensions
```

At the start of `embed`, before `check_inputs`, add:

```python
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
```

and in `_vector`, use `self._returned_dimensions` instead of `self._identity.dimensions`:

```python
        digest = hashlib.shake_256(text.encode()).digest(self._returned_dimensions)
```

Create `tests/openai_stubs.py`:

```python
"""A stand-in for AsyncOpenAI's embeddings endpoint, for deterministic tests."""

import asyncio
import hashlib
import math
from typing import Any, cast

import httpx2
import openai
from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse

from knowledge_service.openai_embeddings import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)

URL = "https://api.openai.test/v1/embeddings"


def vector_for(text: str, dimensions: int) -> list[float]:
    """A stable unit vector for `text`, as a real model would give."""
    raw = [b / 127.5 - 1.0 for b in hashlib.shake_256(text.encode()).digest(dimensions)]
    norm = math.sqrt(sum(value * value for value in raw))
    return [value / norm for value in raw]


def response_for(
    texts: list[str], dimensions: int, *, reverse: bool = True
) -> CreateEmbeddingResponse:
    """A response whose items come back in reverse order, each with its index."""
    data = [
        {
            "object": "embedding",
            "index": index,
            "embedding": vector_for(text, dimensions),
        }
        for index, text in enumerate(texts)
    ]
    return CreateEmbeddingResponse.model_validate(
        {
            "object": "list",
            "model": "text-embedding-3-small",
            "data": list(reversed(data)) if reverse else data,
            "usage": {"prompt_tokens": len(texts) * 3, "total_tokens": len(texts) * 3},
        }
    )


def status_error(status: int, *, body_text: str = "") -> openai.APIStatusError:
    """An SDK error whose request, like the real one, holds the input texts."""
    request = httpx2.Request("POST", URL, json={"input": [body_text]})
    response = httpx2.Response(status, request=request)
    error_type = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        409: openai.ConflictError,
        422: openai.UnprocessableEntityError,
        429: openai.RateLimitError,
    }.get(
        status, openai.InternalServerError if status >= 500 else openai.APIStatusError
    )
    return error_type(f"status {status}", response=response, body=None)


class StubEmbeddings:
    """Replace only `AsyncOpenAI.embeddings`; record every request."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        response: object = None,
        dimensions: int | None = None,
        delay: float | None = None,
        block: bool = False,
    ) -> None:
        self._error = error
        self._response = response
        self._dimensions = dimensions
        self._delay = delay
        self._block = block
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._block:
            await asyncio.Event().wait()
        if self._delay is not None:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return response_for(kwargs["input"], self._dimensions or kwargs["dimensions"])


class UnconfiguredEmbeddings:
    """The endpoint of the client as passed in, with the SDK's retries on."""

    async def create(self, **kwargs: Any) -> CreateEmbeddingResponse:
        raise AssertionError("the adapter used the client without its options")


class StubClient:
    """Enough of AsyncOpenAI for the adapter: `with_options` and `embeddings`.

    Like the real client, `with_options` returns a configured copy. Only the
    copy reaches the stubbed endpoint, so an adapter that ignores the copy
    fails loudly.
    """

    def __init__(self, embeddings: StubEmbeddings) -> None:
        self._configured = embeddings
        self.embeddings: StubEmbeddings | UnconfiguredEmbeddings = (
            UnconfiguredEmbeddings()
        )
        self.options: dict[str, Any] = {}
        self.copies: list[StubClient] = []

    def with_options(self, **options: Any) -> StubClient:
        copy = StubClient(self._configured)
        copy.embeddings = self._configured
        copy.options = {**self.options, **options}
        self.copies.append(copy)
        return copy


def openai_embedding_model(
    embeddings: StubEmbeddings,
    settings: OpenAIEmbeddingSettings | None = None,
) -> tuple[OpenAIEmbeddingModel, StubClient]:
    """Return the adapter and the configured client copy it must use."""
    client = StubClient(embeddings)
    model = OpenAIEmbeddingModel(
        cast(AsyncOpenAI, client), settings or OpenAIEmbeddingSettings()
    )
    (configured,) = client.copies
    return model, configured
```

**Check:** `uv run pytest tests/test_embeddings.py -q` still gives 43 passed.

### Step 6 — Coding step: contract and adapter tests

The contract suite runs every test against the fake and the OpenAI adapter. `AdapterCase` builds each adapter in the states the contract needs, and `Adapter.sent` reports how many requests reached the provider.

| Contract test | Promise |
|---|---|
| `test_each_vector_belongs_to_its_input` | Each vector equals the vector of its text embedded alone. Comparing a batch with its reverse would not catch a reversed response, because that comparison is symmetric. |
| `test_an_input_over_the_token_limit_is_refused_before_sending` | Token-limit rejection, with nothing sent. |
| `test_a_deadline_that_is_not_a_number_is_refused_before_sending` | A NaN deadline: `asyncio.timeout(nan)` would crash the whole event loop. |
| `test_a_rate_limit_is_a_typed_error` | Rate limits. |
| `test_vectors_of_the_wrong_dimensions_are_refused` | Malformed dimensions. |
| `test_cancellation_reaches_the_caller` | Cancellation. |
| `test_openai_live_embeddings_keep_the_contract` | Opt-in, marked `live`: one real request with 256 dimensions, English and Ukrainian. |

The adapter tests check what only OpenAI has: the request's model, dimensions, format, deadline, request ID header, and disabled retries; the shorter deadline; a slow provider timing out; the retry owner of 14 failure kinds; no request text in an error or its chain; responses whose indexes do not match; nine malformed 200 bodies and a body that is not JSON; byte counting; one safe log record per call, including a refused one; and impossible settings.

**Action:** create `tests/test_embedding_model_contract.py`:

```python
"""Behavioural contract tests shared by every EmbeddingModel adapter."""

import asyncio
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from fakes import FakeEmbeddingModel
from knowledge_service.embeddings import (
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingModel,
    EmbeddingRateLimited,
    RejectionReason,
)
from knowledge_service.openai_embeddings import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000022")


@dataclass(frozen=True)
class Adapter:
    """One adapter in the states the contract needs, and how many calls it sent."""

    model: EmbeddingModel
    sent: Callable[[], int]


@dataclass(frozen=True)
class AdapterCase:
    name: str
    working: Callable[[], Adapter]
    rate_limited: Callable[[], Adapter]
    wrong_dimensions: Callable[[], Adapter]
    blocked: Callable[[], Adapter]


def fake(model: FakeEmbeddingModel) -> Adapter:
    return Adapter(model=model, sent=lambda: len(model.batches))


def openai_adapter(embeddings: StubEmbeddings) -> Adapter:
    model, _ = openai_embedding_model(embeddings)
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


CASES = (
    AdapterCase(
        name="fake",
        working=lambda: fake(FakeEmbeddingModel()),
        rate_limited=lambda: fake(FakeEmbeddingModel(error=EmbeddingRateLimited())),
        wrong_dimensions=lambda: fake(FakeEmbeddingModel(returned_dimensions=3)),
        blocked=lambda: fake(FakeEmbeddingModel(wait_for=asyncio.Event())),
    ),
    AdapterCase(
        name="openai",
        working=lambda: openai_adapter(StubEmbeddings()),
        rate_limited=lambda: openai_adapter(StubEmbeddings(error=status_error(429))),
        wrong_dimensions=lambda: openai_adapter(StubEmbeddings(dimensions=3)),
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
)
IDS = [case.name for case in CASES]


async def embed(
    model: EmbeddingModel, texts: list[str]
) -> tuple[tuple[float, ...], ...]:
    batch = await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=2.0)
    assert batch.identity == model.identity
    assert all(len(vector) == model.identity.dimensions for vector in batch.vectors)
    return batch.vectors


def over_the_input_limit(model: EmbeddingModel) -> str:
    text = "word"
    while model.count_tokens(text) <= model.limits.max_input_tokens:
        text = f"{text} {text}"
    return text


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_each_vector_belongs_to_its_input(case: AdapterCase) -> None:
    model = case.working().model
    texts = ["vacation policy", "laptop setup", "expense report"]

    batch = await embed(model, texts)
    alone = [(await embed(model, [text]))[0] for text in texts]

    assert list(batch) == alone
    assert len(set(batch)) == 3


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_an_input_over_the_token_limit_is_refused_before_sending(
    case: AdapterCase,
) -> None:
    adapter = case.working()

    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(adapter.model, ["fine", over_the_input_limit(adapter.model)])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.INPUT_TOO_LONG,
        1,
    )
    assert adapter.sent() == 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_a_deadline_that_is_not_a_number_is_refused_before_sending(
    case: AdapterCase,
) -> None:
    adapter = case.working()

    with pytest.raises(ValueError, match="timeout_seconds"):
        await adapter.model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=math.nan
        )

    assert adapter.sent() == 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_a_rate_limit_is_a_typed_error(case: AdapterCase) -> None:
    with pytest.raises(EmbeddingRateLimited):
        await embed(case.rate_limited().model, ["vacation policy"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_vectors_of_the_wrong_dimensions_are_refused(case: AdapterCase) -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        await embed(case.wrong_dimensions().model, ["vacation policy"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_cancellation_reaches_the_caller(case: AdapterCase) -> None:
    task = asyncio.create_task(embed(case.blocked().model, ["vacation policy"]))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.live
async def test_openai_live_embeddings_keep_the_contract() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    try:
        model = OpenAIEmbeddingModel(
            client, OpenAIEmbeddingSettings(embedding_dimensions=256)
        )
        texts = ["How early must I request vacation?", "Як замовити ноутбук?"]
        batch = await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=30.0)
    finally:
        await client.close()

    assert len(batch.vectors) == 2
    assert all(len(vector) == 256 for vector in batch.vectors)
    assert batch.usage.input_tokens > 0
```

Create `tests/test_openai_embeddings.py`:

```python
"""Tests for the OpenAI EmbeddingModel adapter, against a stubbed client."""

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import openai
import pytest
from openai.types import CreateEmbeddingResponse
from pydantic import ValidationError

from knowledge_service.embeddings import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
)
from knowledge_service.errors import RetryOwner, classify_failure, policy_for
from knowledge_service.openai_embeddings import OpenAIEmbeddingSettings
from openai_stubs import (
    URL,
    StubEmbeddings,
    openai_embedding_model,
    response_for,
    status_error,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000023")
SEEDED_TEXT = "the salary of Olena is 9000"


def everything_shown(error: BaseException) -> str:
    """Everything a log line could print about an error and its chain."""
    parts = [str(error), repr(error), *map(str, error.args)]
    parts += getattr(error, "__notes__", [])
    for chained in (error.__cause__, error.__context__):
        if chained is not None:
            parts.append(everything_shown(chained))
    return " ".join(parts)


async def test_a_request_carries_the_configured_model_dimensions_and_deadline() -> None:
    embeddings = StubEmbeddings()
    settings = OpenAIEmbeddingSettings(
        embedding_model="text-embedding-3-large",
        embedding_dimensions=256,
        embedding_timeout_seconds=5.0,
        embedding_price_id="openai-embedding-large",
    )
    model, client = openai_embedding_model(embeddings, settings)

    batch = await model.embed(
        ["vacation policy", "laptop setup"], request_id=REQUEST_ID, timeout_seconds=30.0
    )

    (call,) = embeddings.calls
    assert call["model"] == "text-embedding-3-large"
    assert call["dimensions"] == 256
    assert call["encoding_format"] == "float"
    assert call["input"] == ["vacation policy", "laptop setup"]
    assert call["timeout"] == 5.0
    assert call["extra_headers"] == {"X-Request-ID": str(REQUEST_ID)}
    assert client.options == {"max_retries": 0}
    assert (
        model.identity.provider,
        model.identity.model,
        model.identity.dimensions,
    ) == (
        "openai",
        "text-embedding-3-large",
        256,
    )
    assert batch.usage.input_tokens == 6
    assert batch.usage.price_id == "openai-embedding-large"


async def test_the_shorter_of_the_two_deadlines_is_used() -> None:
    embeddings = StubEmbeddings()
    model, _ = openai_embedding_model(embeddings)

    await model.embed(["vacation policy"], request_id=REQUEST_ID, timeout_seconds=0.5)

    assert embeddings.calls[0]["timeout"] == 0.5


async def test_a_provider_slower_than_the_deadline_times_out() -> None:
    model, _ = openai_embedding_model(StubEmbeddings(delay=1.0))

    with pytest.raises(EmbeddingTimeout):
        await model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=0.05
        )


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", URL))


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(httpx2.Request("POST", URL))


def unreadable_response() -> openai.APIResponseValidationError:
    request = httpx2.Request("POST", URL)
    return openai.APIResponseValidationError(
        httpx2.Response(200, request=request), body=None
    )


@pytest.mark.parametrize(
    ("make_error", "expected", "retry_owner"),
    [
        (timeout_error, EmbeddingTimeout, RetryOwner.SERVICE),
        (connection_error, EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(500), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(503), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(408), EmbeddingTimeout, RetryOwner.SERVICE),
        (lambda: status_error(409), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(429), EmbeddingRateLimited, RetryOwner.CALLER),
        (lambda: status_error(400), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(401), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(403), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(404), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(413), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(422), EmbeddingRequestRefused, RetryOwner.NONE),
        (unreadable_response, EmbeddingMalformedResponse, RetryOwner.SERVICE),
    ],
    ids=[
        "timeout",
        "connection",
        "500",
        "503",
        "408",
        "409",
        "429",
        "400",
        "401",
        "403",
        "404",
        "413",
        "422",
        "unreadable",
    ],
)
async def test_each_provider_failure_becomes_a_port_error_with_a_retry_owner(
    make_error: Callable[[], BaseException],
    expected: type[EmbeddingError],
    retry_owner: RetryOwner,
) -> None:
    error = make_error()
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(expected) as raised:
        await model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=2.0
        )

    assert policy_for(classify_failure(raised.value)).retry_owner is retry_owner


async def test_a_provider_error_never_carries_the_request_text() -> None:
    # The SDK error keeps the HTTP request, whose body holds every input.
    error = status_error(400, body_text=SEEDED_TEXT)
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(EmbeddingRequestRefused) as raised:
        await model.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "Olena" not in everything_shown(raised.value)


def items(*indexes: int) -> CreateEmbeddingResponse:
    response = response_for(["a", "b", "c"], 1536, reverse=False)
    data = [
        item.model_copy(update={"index": i})
        for item, i in zip(response.data, indexes, strict=False)
    ]
    return response.model_copy(update={"data": data})


@pytest.mark.parametrize(
    "indexes",
    [(0, 1), (0, 1, 1), (0, 1, 3), (-1, 0, 1)],
    ids=["missing", "duplicate", "out-of-range", "negative"],
)
async def test_vectors_that_cannot_be_matched_to_inputs_are_refused(
    indexes: tuple[int, ...],
) -> None:
    model, _ = openai_embedding_model(StubEmbeddings(response=items(*indexes)))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.embed(["a", "b", "c"], request_id=REQUEST_ID, timeout_seconds=2.0)


def usage(prompt_tokens: object) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt_tokens)


VALID = response_for(["a"], 1536)


@pytest.mark.parametrize(
    "response",
    [
        "not json at all",
        [1, 2],
        SimpleNamespace(),
        SimpleNamespace(data=None, usage=usage(1)),
        SimpleNamespace(data=VALID.data),
        SimpleNamespace(data=VALID.data, usage=None),
        SimpleNamespace(data=VALID.data, usage=usage(-1)),
        SimpleNamespace(data=VALID.data, usage=usage("3")),
        SimpleNamespace(data=VALID.data, usage=usage(True)),
    ],
    ids=[
        "text",
        "list",
        "empty",
        "no-data",
        "no-usage",
        "null-usage",
        "negative-tokens",
        "string-tokens",
        "boolean-tokens",
    ],
)
async def test_a_successful_status_with_a_malformed_body_is_refused(
    response: object,
) -> None:
    # The SDK does not validate a 200 body, so the adapter must.
    model, _ = openai_embedding_model(StubEmbeddings(response=response))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.embed(["a"], request_id=REQUEST_ID, timeout_seconds=2.0)


async def test_a_body_that_is_not_json_is_refused_without_chaining() -> None:
    error = json.JSONDecodeError("Expecting value", "not json", 0)
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(EmbeddingMalformedResponse) as raised:
        await model.embed(["a"], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert raised.value.__context__ is None


async def test_an_empty_batch_sends_nothing() -> None:
    embeddings = StubEmbeddings()
    model, _ = openai_embedding_model(embeddings)

    batch = await model.embed([], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert batch.vectors == ()
    assert embeddings.calls == []


def test_token_counts_are_utf8_bytes_an_upper_bound() -> None:
    model, _ = openai_embedding_model(StubEmbeddings())

    assert model.count_tokens("vacation") == 8
    assert model.count_tokens("відпустка") == 18
    assert model.limits.max_inputs == 2048
    assert model.limits.max_input_tokens == 8192
    assert model.limits.max_batch_tokens == 300_000


async def test_each_call_logs_counts_and_outcome_but_never_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model, _ = openai_embedding_model(StubEmbeddings())
    failing, _ = openai_embedding_model(StubEmbeddings(error=status_error(429)))

    await model.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)
    with pytest.raises(EmbeddingRateLimited):
        await failing.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)
    with pytest.raises(EmbeddingInputRejected):
        await model.embed(
            [SEEDED_TEXT, " "], request_id=REQUEST_ID, timeout_seconds=2.0
        )

    ours = [
        record
        for record in caplog.records
        if record.name == "knowledge_service.openai_embeddings"
    ]
    # `extra` fields become attributes, which a type checker cannot see.
    fields: list[dict[str, Any]] = [record.__dict__["embedding"] for record in ours]
    assert [record.levelno for record in ours] == [
        logging.INFO,
        logging.WARNING,
        logging.WARNING,
    ]
    assert [f["outcome"] for f in fields] == [
        "succeeded",
        "EmbeddingRateLimited",
        "EmbeddingInputRejected",
    ]
    assert (fields[0]["inputs"], fields[0]["input_tokens"]) == (1, 3)
    assert all("Olena" not in str(vars(r)) for r in caplog.records)


@pytest.mark.parametrize(
    "field",
    [
        {"embedding_dimensions": 0},
        {"embedding_timeout_seconds": 0},
        {"embedding_max_inputs": 0},
        {"embedding_model": ""},
    ],
)
def test_impossible_settings_are_refused(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbeddingSettings.model_validate(field)
```

**Check:** `uv run pytest tests/test_embedding_model_contract.py tests/test_openai_embeddings.py -q` gives 51 passed and 1 deselected (the live test).

Then break the adapter on purpose, one change at a time, and undo each change:

| Break | Test that must fail |
|---|---|
| Call `client.with_options(max_retries=0)` but keep `self._client = client` | every OpenAI test (the stub refuses the unconfigured client) |
| Return vectors in response order, ignoring `index` | `test_each_vector_belongs_to_its_input[openai]` |
| Raise `_translated(error) from error` inside the `except` block | `test_a_provider_error_never_carries_the_request_text` |
| Remove the `isinstance(data, list)` check | `test_a_successful_status_with_a_malformed_body_is_refused[no-data]` |
| Remove the `math.isnan` guard | `test_a_deadline_that_is_not_a_number_is_refused_before_sending[openai]` |

**Optional live check.** With your own key, `OPENAI_API_KEY=... UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m live tests/test_embedding_model_contract.py -q` makes one real request: a few dozen tokens, a tiny fraction of a cent. Never write the key into a file.

### Step 7 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_embedding_model_contract.py tests/test_openai_embeddings.py tests/test_errors.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. Answer these reflection questions in your own words:

1. What does the adapter hide from its callers? List what a caller never sees about OpenAI.
2. Which promise would be easiest to break in a later speed-up? For example, what goes wrong if someone turns the SDK's retries back on to "make it more reliable"?
3. Why is a 401 not retried, but a 409 is? What would retrying a 401 cost?
4. Why must the port error not be raised `from` the SDK error? Where does the input text sit in the SDK error?
5. Counting bytes over-estimates tokens. What measurement would show whether that wastes too much batch space for Ukrainian text, and what would you change then?
6. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the adapter in your words.

## Completion checklist

- [ ] `EmbeddingRequestRefused` exists and is classified `INTERNAL` (no retry).
- [ ] `OpenAIEmbeddingModel` sends one request per call with the configured model and dimensions, the shorter deadline, the request ID, and the SDK's retries off.
- [ ] Every SDK failure becomes one port error with the right retry owner, and no error or chain carries input text.
- [ ] Malformed responses (wrong dimensions, unmatched indexes, malformed bodies) become `EmbeddingMalformedResponse`.
- [ ] Each call leaves one log record with counts and the outcome, and no text; `migrations/env.py` no longer disables the application's loggers.
- [ ] The contract suite passes for both the fake and the OpenAI adapter.
- [ ] Format, lint, types, the deterministic suite, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design, verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
