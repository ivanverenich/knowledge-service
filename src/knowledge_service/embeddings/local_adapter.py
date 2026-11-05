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
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory. A
  compressed answer is refused unread, because it could unpack to far more.
- **Sparse weights, as a prototype.** `encode_sparse` asks the same server
  for BGE-M3's sparse term weights (see `embeddings.sparse`), with the same
  limits, provenance check, size cap, and errors. Only the M05-T10
  comparison uses them.
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
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
SPARSE_PATH = "/v1/sparse"
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
    vocabulary_size: int = Field(default=250_002, gt=0)
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
        self._sparse_identity = SparseModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            vocabulary_size=settings.vocabulary_size,
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
    def sparse_identity(self) -> SparseModelIdentity:
        return self._sparse_identity

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

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return BGE-M3's sparse term weights for each text, in order.

        Checked and refused exactly like `embed`; the answer must also name
        the configured model, revision, and vocabulary size.
        """
        deadline = self._deadline(timeout_seconds)
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error, operation="sparse")
            raise
        if not texts:
            return ()
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                SPARSE_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            vectors = self._sparse_from(status, content, texts)
        except EmbeddingError as error:
            _log(
                request_id,
                self._identity,
                len(texts),
                _since(started_at),
                error,
                operation="sparse",
            )
            raise
        _log(
            request_id,
            self._identity,
            len(texts),
            _since(started_at),
            None,
            operation="sparse",
        )
        return vectors

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
        """Send one request; return its status and its bytes, up to the cap.

        The answer must not be compressed: a small compressed body can unpack
        to far more than the cap before the cap is checked.
        """
        status = 0
        content = bytearray()
        encoded = False
        failure: EmbeddingError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers={"Accept-Encoding": "identity", **(headers or {})},
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                coding = response.headers.get("Content-Encoding", "identity")
                encoded = coding.strip().lower() != "identity"
                # An encoded body is never read, so nothing is ever unpacked.
                if not encoded:
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
        if encoded and failure is None:
            failure = EmbeddingMalformedResponse("the response is encoded")
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

    def _sparse_from(
        self, status: int, content: bytes, texts: Sequence[str]
    ) -> tuple[SparseVector, ...]:
        _check_status(status)
        body = _json_object(content)
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        size = body.get("vocabulary_size")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            or type(size) is not int
            or size != self._settings.vocabulary_size
        ):
            # Weights from another vocabulary would match the wrong tokens.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or vocabulary"
            )
        vectors = checked_sparse_vectors(
            body.get("weights"),
            expected=len(texts),
            vocabulary_size=self._settings.vocabulary_size,
        )
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            # A text has at most one weight per token; more would swell the index.
            if len(vector) > self.count_tokens(text):
                raise EmbeddingMalformedResponse(
                    f"sparse vector {index} has more weights than its text has tokens"
                )
        return vectors

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
    *,
    operation: str | None = None,
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
    if operation is not None:
        fields["operation"] = operation
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "embedding request finished", extra={"embedding": fields})
