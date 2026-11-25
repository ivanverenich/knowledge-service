"""Pydantic models for the knowledge service."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from knowledge_service.source_administration import (
    MAX_CREDENTIALS_REFERENCE,
    MAX_DISPLAY_NAME,
    MAX_LOCATION,
    MAX_PRINCIPAL,
    MAX_PRINCIPALS,
)


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


class SynchronizationHistory(BaseModel):
    """A page of a Source's synchronization Jobs, newest first.

    `next_cursor` starts the next page; None on the last one.
    """

    items: list[SynchronizationJobReport]
    next_cursor: str | None


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


# A request body that names a field the API does not know, or gives a value
# of another JSON type, is refused, never coerced or ignored.
_STRICT_BODY = ConfigDict(extra="forbid", strict=True)
Principal = Annotated[str, Field(max_length=MAX_PRINCIPAL)]


class SourceRegistration(BaseModel):
    """What registers a Source: what it is, where, and who may read its items.

    `credentials_reference` names a secret in the service's secret store; the
    secret itself never passes through the API. `default_public` and
    `default_principals` say who may read the items of a Source that carries
    no access of its own, such as a local folder; by default, nobody.
    """

    model_config = _STRICT_BODY

    kind: str = Field(max_length=32)
    location: str = Field(max_length=MAX_LOCATION)
    display_name: str = Field(max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool = False
    default_principals: list[Principal] = Field(
        default_factory=list, max_length=MAX_PRINCIPALS
    )


class SourceChanges(BaseModel):
    """The settings a change may set; a field left out keeps its value.

    `kind` and `location` are what a Source is, so they never change: a
    different folder is a different Source.
    """

    model_config = _STRICT_BODY

    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool | None = None
    default_principals: list[Principal] | None = Field(
        default=None, max_length=MAX_PRINCIPALS
    )


class SourceView(BaseModel):
    """A Source as an operator configures it; never a secret."""

    source_id: UUID
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # A null names nobody: the Source needs repair before it can synchronize.
    default_principals: list[str | None]
    created_at: datetime
    updated_at: datetime


class SourcePage(BaseModel):
    """A page of Sources, newest first; `next_cursor` is None on the last one."""

    items: list[SourceView]
    next_cursor: str | None
