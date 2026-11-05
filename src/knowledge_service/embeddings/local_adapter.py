"""Local BGE-M3 implementation of the provider-neutral EmbeddingModel port.

The model itself runs in a separate Python 3.12 process, the BGE-M3 server
in `ml/bge_m3_server/`, because ML libraries follow a different Python
release cadence than this application (ADR 0009). This adapter is the
application's side of that seam: one call to `embed` is one HTTP request to
the server. It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-m3@<revision>`, so vectors from other weights never
  share an Embedding Space. Every response must name the same model,
  revision, and dimensions; any other answer raises
  `EmbeddingRequestRefused` and no vector is returned, because storing it
  would mix two models in one space.
- **A safe token bound without a tokenizer.** `count_tokens` returns an
  upper bound, so a batch the server would refuse is refused here first,
  before any text leaves the process. The server counts exactly and refuses
  rather than truncating.
- **Retry classification.** A refused connection or a 5xx becomes
  `EmbeddingUnavailable`, a 429 `EmbeddingRateLimited`, a timeout
  `EmbeddingTimeout`, and any other refusal `EmbeddingRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory.
- **Safe logging.** One log record per call with counts, timing, and the
  outcome; never text. Port errors are raised outside the `except` block,
  because an HTTP error holds the request, and the request holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.embeddings.port import (
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

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
# The BGE-M3 weights this application was evaluated with (Hugging Face commit).
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# 64 vectors of 1024 numbers as JSON take about 2 MB; far more means a fault.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds before the text.
_ADDED_TOKENS = 3


class LocalEmbeddingSettings(BaseSettings):
    """Settings for the local BGE-M3 adapter, read from `LOCAL_EMBEDDING_*`.

    The limits must not exceed the server's: 8192 tokens per input, as the
    model allows, and at most 64 inputs and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_EMBEDDING_")

    url: str = Field(default="http://127.0.0.1:8765", min_length=1)
    model: str = Field(default="BAAI/bge-m3", min_length=1)
    revision: str = Field(default=BGE_M3_REVISION, min_length=1)
    dimensions: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_inputs: int = Field(default=32, gt=0)
    max_input_tokens: int = Field(default=8192, gt=0)
    max_batch_tokens: int = Field(default=16_384, gt=0)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        # Checked here, so a typo fails at start-up, not inside a request.
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            url = None
        if url is None or url.scheme not in ("http", "https") or not url.host:
            raise ValueError("url must be an http or https URL")
        return value.rstrip("/")


class LocalEmbeddingModel:
    """Translate EmbeddingModel calls to the local BGE-M3 server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalEmbeddingSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = EmbeddingModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            dimensions=settings.dimensions,
        )
        self._limits = EmbeddingLimits(
            max_inputs=settings.max_inputs,
            max_input_tokens=settings.max_input_tokens,
            max_batch_tokens=settings.max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        """Return an upper bound on the text's BGE-M3 tokens.

        The tokenizer first normalizes the text, and every token then covers
        at least one normalized character or the word-start marker. Its
        normalization composes some characters, as NFKC does, but leaves
        others apart, such as Korean typed as separate letters, so the
        longer of the NFKC and NFKD forms is used. The server's tests check
        this bound against every Unicode character with the real tokenizer.
        """
        longest = max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )
        return longest + _ADDED_TOKENS

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        deadline = self._deadline(timeout_seconds)
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
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                EMBEDDINGS_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            latency_ms = _since(started_at)
            batch = self._batch_from(status, content, len(texts), latency_ms)
        except EmbeddingError as error:
            _log(request_id, self._identity, len(texts), _since(started_at), error)
            raise
        _log(request_id, self._identity, len(texts), latency_ms, None, batch.usage)
        return batch

    async def verify_server(self, *, timeout_seconds: float) -> None:
        """Check, sending no text, that the server runs the configured model.

        Raises `EmbeddingUnavailable` or `EmbeddingTimeout` when the server
        cannot be reached, and `EmbeddingRequestRefused` when it serves other
        weights. Call it before a long run, so a stopped server or a wrong
        revision fails at once instead of after the first batch.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        self._check_provenance(_json_object(content))

    def _deadline(self, timeout_seconds: float) -> float:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        return min(timeout_seconds, self._settings.timeout_seconds)

    async def _send(
        self,
        method: str,
        path: str,
        deadline: float,
        *,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Send one request; return its status and its bytes, up to the cap."""
        status = 0
        content = bytearray()
        failure: EmbeddingError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers=headers,
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                async for piece in response.aiter_bytes():
                    content += piece
                    if len(content) > MAX_RESPONSE_BYTES:
                        break
        except TimeoutError, httpx.TimeoutException:
            failure = EmbeddingTimeout()
        except httpx.HTTPError:
            failure = EmbeddingUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = EmbeddingMalformedResponse("the response is too large")
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _batch_from(
        self, status: int, content: bytes, expected: int, latency_ms: int
    ) -> EmbeddingBatch:
        """Read the response without trusting its status, shape, or provenance."""
        _check_status(status)
        body = self._check_provenance(_json_object(content))
        tokens = body.get("input_tokens")
        if type(tokens) is not int or tokens < 0:
            raise EmbeddingMalformedResponse("the response has no token usage")
        return checked_batch(
            self._identity,
            body.get("vectors"),
            EmbeddingUsage(input_tokens=tokens, latency_ms=latency_ms),
            expected=expected,
        )

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        dimensions = body.get("dimensions")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            # 1024.0 == 1024 in Python, so the type is checked as well.
            or type(dimensions) is not int
            or dimensions != self._settings.dimensions
        ):
            # Another model's vectors would be stored in this model's space.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or size"
            )
        return body


def _check_status(status: int) -> None:
    if status == 429:
        raise EmbeddingRateLimited()
    if status in (408, 504):
        raise EmbeddingTimeout()
    if status >= 500:
        raise EmbeddingUnavailable()
    if status != 200:
        raise EmbeddingRequestRefused(
            f"the local model refused the request with status {status}"
        )


def _json_object(content: bytes) -> dict[str, object] | None:
    try:
        body: object = json.loads(content)
    except ValueError, RecursionError:
        # RecursionError: JSON nested deeper than the parser allows.
        return None
    if not isinstance(body, dict):
        return None
    return cast(dict[str, object], body)


def _since(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1_000)


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
