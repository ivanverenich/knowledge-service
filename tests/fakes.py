import asyncio
from uuid import UUID

from knowledge_service.contracts import Answer, Question, Usage
from knowledge_service.model import ChatModelUnavailable


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
    ) -> Answer:
        if self.raise_error:
            raise ChatModelUnavailable()
        if self.wait_for is not None:
            await self.wait_for.wait()
        return Answer(
            text=f"Fake answer to: {question.text}",
            request_id=request_id,
            usage=Usage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
        )
