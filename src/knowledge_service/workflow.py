import asyncio
import math
from uuid import UUID

from knowledge_service.contracts import Answer, Question
from knowledge_service.errors import AnswerDeadlineExceeded
from knowledge_service.model import ChatModel


class AnswerWorkflow:
    def __init__(self, model: ChatModel, *, timeout_seconds: float = 30.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
    ) -> Answer:
        deadline = asyncio.timeout(self._timeout_seconds)
        try:
            async with deadline:
                return await self._model.answer(
                    question,
                    request_id=request_id,
                    timeout_seconds=self._timeout_seconds,
                )
        except TimeoutError as error:
            if not deadline.expired():
                raise
            raise AnswerDeadlineExceeded from error
