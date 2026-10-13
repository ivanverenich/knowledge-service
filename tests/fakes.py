import asyncio
from collections.abc import Sequence
from uuid import UUID

from knowledge_service.contracts import Answer, Question, Usage
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
