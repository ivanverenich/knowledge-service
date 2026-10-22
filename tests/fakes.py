import asyncio
import hashlib
import math
from collections.abc import Sequence
from uuid import UUID

from knowledge_service.contracts import Answer, Question, Usage
from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingTimeout,
    EmbeddingUsage,
    check_inputs,
    checked_batch,
)
from knowledge_service.model import ChatModelUnavailable
from knowledge_service.sources import (
    SourceError,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)


class FakeChatModel:
    """Fake chat model for testing purposes."""

    def __init__(
        self,
        raise_error: bool = False,
        wait_for: asyncio.Event | None = None,
    ) -> None:
        self.raise_error = raise_error
        self.wait_for = wait_for

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        if self.raise_error:
            raise ChatModelUnavailable()
        if self.wait_for is not None:
            await self.wait_for.wait()
        return Answer(
            text=f"Fake answer to: {question.text}",
            summary="A short fake summary.",
            request_id=request_id,
            usage=Usage(
                provider="OpenAI",
                model="gpt4o-mini",
                price_id=None,
                latency_ms=50,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
        )


class SlowChatModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned_up = asyncio.Event()

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        self.started.set()

        try:
            await self.release.wait()
            return Answer(
                text=f"Fake answer to: {question.text}",
                summary="A short fake summary.",
                request_id=request_id,
                usage=Usage(
                    provider="OpenAI",
                    model="gpt4o-mini",
                    price_id=None,
                    latency_ms=50,
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        finally:
            self.cleaned_up.set()


class FakeSource:
    """Deterministic in-memory SourcePort over an explicit list of pages."""

    def __init__(
        self,
        pages: Sequence[Sequence[SourceRecord]],
        *,
        fail_on_call: int | None = None,
        failure: SourceError | None = None,
    ) -> None:
        self._pages = tuple(tuple(page) for page in pages)
        self._fail_on_call = fail_on_call
        self._failure = (
            failure if failure is not None else SourceUnavailable("fake source failed")
        )
        self.checkpoints: list[str | None] = []

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        self.checkpoints.append(checkpoint)
        if (
            self._fail_on_call is not None
            and len(self.checkpoints) == self._fail_on_call
        ):
            raise self._failure
        index = 0 if checkpoint is None else int(checkpoint)
        if index >= len(self._pages):
            return SourcePage(records=(), next_checkpoint=None)
        following = index + 1
        next_checkpoint = None if following >= len(self._pages) else str(following)
        return SourcePage(records=self._pages[index], next_checkpoint=next_checkpoint)


class FakeEmbeddingModel:
    """Deterministic EmbeddingModel: the same text always gets the same vector.

    A token is a whitespace-separated word. Each vector is derived from a
    SHAKE-256 hash of its text and scaled to length 1, so it is stable across
    calls, batches, and processes. `batches` records every batch that reached
    the "provider", so tests can check what was sent.
    """

    def __init__(
        self,
        *,
        dimensions: int = 8,
        limits: EmbeddingLimits | None = None,
        error: EmbeddingError | None = None,
        wait_for: asyncio.Event | None = None,
        returned_dimensions: int | None = None,
    ) -> None:
        self._identity = EmbeddingModelIdentity(
            provider="fake", model="fake-embedding", dimensions=dimensions
        )
        self._limits = limits or EmbeddingLimits(
            max_inputs=16, max_input_tokens=64, max_batch_tokens=256
        )
        self._error = error
        self._wait_for = wait_for
        # Pretend the provider answered with vectors of another length.
        self._returned_dimensions = returned_dimensions or dimensions
        self.batches: list[tuple[str, ...]] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        tokens = check_inputs(texts, self._limits, self.count_tokens)
        if not texts:
            return checked_batch(
                self._identity,
                [],
                EmbeddingUsage(input_tokens=0, latency_ms=0),
                expected=0,
            )
        self.batches.append(tuple(texts))
        if self._wait_for is not None:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._wait_for.wait()
            except TimeoutError:
                raise EmbeddingTimeout() from None
        if self._error is not None:
            raise self._error
        return checked_batch(
            self._identity,
            [self._vector(text) for text in texts],
            EmbeddingUsage(input_tokens=tokens, latency_ms=0),
            expected=len(texts),
        )

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.shake_256(text.encode()).digest(self._returned_dimensions)
        raw = [byte / 127.5 - 1.0 for byte in digest]
        norm = math.sqrt(sum(value * value for value in raw))
        return [value / norm for value in raw]
