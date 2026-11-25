"""Pydantic models for the knowledge service."""

from datetime import datetime
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
    summary: str = Field(min_length=1, max_length=280)
    request_id: UUID
    usage: Usage
    citations: list[str] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured content returned by a chat model."""

    text: str = Field(min_length=1)
    summary: str = Field(
        min_length=1,
        max_length=280,
        description="A concise summary of the answer.",
    )


class ErrorResponse(BaseModel):
    """An error response in the knowledge service."""

    code: str
    message: str
    request_id: UUID


class JobAttemptProgress(BaseModel):
    """How far one attempt of a Job got, and how it ended."""

    attempt: int = Field(ge=1)
    stage: str
    processed: int = Field(ge=0)
    changed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    warnings: list[str]
    started_at: datetime
    heartbeat_at: datetime
    outcome: str | None
    finished_at: datetime | None


class SynchronizationJobReport(BaseModel):
    """A synchronization Job and the progress of its newest attempt."""

    job_id: UUID
    source_id: UUID
    status: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None


class OperatorJob(BaseModel):
    """A Job as an operator decides about it: what it is, and how it ended."""

    job_id: UUID
    kind: str
    target_id: UUID
    status: str
    condition: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    finished_at: datetime | None
    error_code: str | None
    # What an operator may decide now: "retry", "cancel", both, or neither.
    decisions: list[str]


class JobsNeedingDecision(BaseModel):
    """A page of Jobs that failed for good and no newer Job replaced.

    Newest first. `next_cursor` starts the next page; None on the last one.
    """

    items: list[OperatorJob]
    next_cursor: str | None
