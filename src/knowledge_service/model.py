from typing import Protocol
from uuid import UUID

from knowledge_service.contracts import Answer, Question


class ChatModelError(Exception):
    """Base error for model failures."""


class ChatModelUnavailable(ChatModelError):
    """The selected model cannot currently serve the request."""


class ChatModelMalformedResponse(ChatModelError):
    """The selected model missing usage data"""

    def __init__(self, message: str) -> None:
        self.message = message


class ChatModelTimeout(ChatModelError):
    """The selected model timed out during request"""


class ChatModelRateLimited(ChatModelError):
    """The selected model has reachted request limit"""


class ChatModel(Protocol):
    """A chat model that can answer questions."""

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        """Generate an answer for a validated question."""
        ...
