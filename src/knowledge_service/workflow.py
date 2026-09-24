from uuid import UUID

from knowledge_service.contracts import Answer, Question
from knowledge_service.model import ChatModel


class AnswerWorkflow:
    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
    ) -> Answer:
        return await self._model.answer(question, request_id=request_id)
