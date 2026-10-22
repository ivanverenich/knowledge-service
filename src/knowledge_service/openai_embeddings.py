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
