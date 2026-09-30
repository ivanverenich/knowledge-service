"""Pydantic models for the knowledge service."""

from uuid import UUID

from pydantic import BaseModel, Field


class Question(BaseModel):
    """A question in the knowledge service."""

    text: str = Field(
        min_length=1, max_length=4_000, description="The text of the user's question."
    )


class Usage(BaseModel):
    """Safe model usage metadata for one answer."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    price_id: str | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class Answer(BaseModel):
    """An answer in the knowledge service."""

    text: str
    request_id: UUID
    usage: Usage
    citations: list[str] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured content returned by a chat model."""

    text: str = Field(min_length=1)


class ErrorResponse(BaseModel):
    """An error response in the knowledge service."""

    code: str
    message: str
    request_id: UUID
