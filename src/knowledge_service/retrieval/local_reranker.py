"""Local bge-reranker-v2-m3 implementation of the Reranker port.

The model runs in a separate Python 3.12 process, the reranker server in
`ml/bge_m3_server/reranker_server.py` (ADR 0009). This adapter is the
application's side of that seam: one call to `score` is one HTTP request.
It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-reranker-v2-m3@<revision>`. Every answer must name the
  same model and revision; any other answer raises `RerankerRequestRefused`
  and no score is returned, because scores of two models do not compare.
- **A safe token bound without a tokenizer.** `count_pair_tokens` returns an
  upper bound, the same bound as `LocalEmbeddingModel.count_tokens` for each
  text: the model shares BGE-M3's tokenizer, and a pair adds no more
  special tokens than the two texts would alone. A request the server would
  refuse is refused here first, before any text leaves the process.
- **Retry classification.** A refused connection or a 5xx becomes
  `RerankerUnavailable`, a timeout or a 408 or 504 `RerankerTimeout`, and
  any other refusal `RerankerRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`; a compressed answer is refused unread.
- **Safe logging.** One log record per call with counts, timing, device,
  and the outcome; never text. Errors are raised outside the `except`
  block, because an HTTP error holds the request, which holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import re
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankLimits,
    check_rerank_inputs,
    checked_scores,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
RERANK_PATH = "/v1/rerank"
MODEL_PATH = "/v1/model"
# The reranker weights this application was evaluated with (Hugging Face commit).
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
# 64 scores as JSON take about 2 KB; far more means a fault.
MAX_RESPONSE_BYTES = 64 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds to one text.
_ADDED_TOKENS = 3
# A device name is logged and printed in Markdown reports, so it stays plain.
_DEVICE = re.compile(r"[a-z0-9:_-]{1,16}")


class LocalRerankerSettings(BaseSettings):
    """Settings for the local reranker adapter, read from `LOCAL_RERANKER_*`.

    The limits cannot exceed the server's: 64 passages, 8192 tokens per
    pair, and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_RERANKER_")

    url: str = Field(default="http://127.0.0.1:8767", min_length=1)
    model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    revision: str = Field(default=RERANKER_REVISION, min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_passages: int = Field(default=16, gt=0, le=64)
    max_pair_tokens: int = Field(default=8192, gt=0, le=8192)
    max_request_tokens: int = Field(default=16_384, gt=0, le=16_384)

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


class LocalReranker:
    """Translate Reranker calls to the local reranker server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalRerankerSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = RerankerIdentity(
            provider=PROVIDER, model=f"{settings.model}@{settings.revision}"
        )
        self._limits = RerankLimits(
            max_passages=settings.max_passages,
            max_pair_tokens=settings.max_pair_tokens,
            max_request_tokens=settings.max_request_tokens,
        )

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def limits(self) -> RerankLimits:
        return self._limits

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Return an upper bound on the pair's tokens: each text's bound, added.

        A pair is `<s> query </s></s> passage </s>`: four special tokens and
        two word-start markers, the same six the two texts add alone.
        """
        return _upper_bound(query) + _upper_bound(passage)

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        deadline = self._deadline(timeout_seconds)
        try:
            check_rerank_inputs(query, passages, self._limits, self.count_pair_tokens)
        except RerankInputRejected as error:
            _log(request_id, self._identity, len(passages), 0, error)
            raise
        if not passages:
            return ()
        started_at = time.perf_counter()
        device: str | None = None
        try:
            status, content = await self._send(
                "POST",
                RERANK_PATH,
                deadline,
                body={"query": query, "passages": list(passages)},
                headers={"X-Request-ID": str(request_id)},
            )
            _check_status(status)
            body = self._check_provenance(_json_object(content))
            device = _device(body)
            scores = checked_scores(body.get("scores"), expected=len(passages))
        except RerankerError as error:
            _log(request_id, self._identity, len(passages), _since(started_at), error)
            raise
        _log(
            request_id,
            self._identity,
            len(passages),
            _since(started_at),
            None,
            device=device,
        )
        return scores

    async def verify_server(self, *, timeout_seconds: float) -> str:
        """Check, sending no text, that the server runs the configured model.

        Returns the device the server scores on, such as `cpu` or `mps`.
        Raises `RerankerUnavailable` or `RerankerTimeout` when the server
        cannot be reached, and `RerankerRequestRefused` when it serves other
        weights.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        device = _device(self._check_provenance(_json_object(content)))
        if device is None:
            raise RerankerMalformedResponse("the reranker did not name its device")
        return device

    def _deadline(self, timeout_seconds: float) -> float:
        # bool is an int, and NaN compares false with everything; neither is.
        if (
            type(timeout_seconds) not in (int, float)
            or math.isnan(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise InvalidRerankRequest("timeout_seconds must be a positive number")
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
        encoded = False
        failure: RerankerError | None = None
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
            failure = RerankerTimeout()
        except httpx.HTTPError, httpx.StreamError, RuntimeError:
            # RuntimeError: a client the caller already closed.
            failure = RerankerUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = RerankerMalformedResponse("the response is too large")
        if encoded and failure is None:
            failure = RerankerMalformedResponse("the response is encoded")
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise RerankerMalformedResponse("the reranker response was not JSON")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
        ):
            # Scores from another model do not compare with this model's.
            raise RerankerRequestRefused(
                "the reranker is not the configured model or revision"
            )
        return body


def _upper_bound(text: str) -> int:
    # As in `LocalEmbeddingModel.count_tokens`: every token covers at least
    # one normalized character, and the tokenizer may leave either form.
    longest = max(
        len(unicodedata.normalize("NFKC", text)),
        len(unicodedata.normalize("NFKD", text)),
    )
    return longest + _ADDED_TOKENS


def _device(body: dict[str, object]) -> str | None:
    device = body.get("device")
    if type(device) is str and _DEVICE.fullmatch(device):
        return device
    return None


def _check_status(status: int) -> None:
    if status in (408, 504):
        raise RerankerTimeout()
    if status >= 500:
        raise RerankerUnavailable()
    if status != 200:
        raise RerankerRequestRefused(
            f"the reranker refused the request with status {status}"
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
    identity: RerankerIdentity,
    passages: int,
    latency_ms: int,
    error: RerankerError | None,
    *,
    device: str | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "passages": passages,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if device is not None:
        fields["device"] = device
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "rerank request finished", extra={"rerank": fields})
