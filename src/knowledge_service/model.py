from typing import Protocol
from uuid import UUID

from knowledge_service.contracts import Answer, Question


class ChatModelError(Exception):
    """Base error for model failures."""


class ChatModelUnavailable(ChatModelError):
    """The selected model cannot currently serve the request."""


class ChatModel(Protocol):
    """A chat model that can answer questions."""

    async def answer(self, question: Question, *, request_id: UUID) -> Answer:
        """Generate an answer for a validated question."""
        ...
