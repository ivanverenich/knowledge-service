"""Why a Job attempt failed, and whether and when to try it again.

The stored Job is the one layer that retries. Dramatiq retries nothing, model
adapters keep their SDK retries off, and repository code never loops, so a
failure is retried only by scheduling the Job's next attempt here.

Every failure gets one short code. Only a dependency that may answer later
(a Source, the database, or a model that is unavailable) is retried, after a
growing, jittered delay, until the Job's attempts run out. Anything refused or
unknown is permanent: retrying a bad credential or a bug only repeats it.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelStructuredOutputUnsupported,
)

logger = logging.getLogger(__name__)


class JobFailure(StrEnum):
    """The error code a failed attempt stores: what failed, and how."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_REFUSED = "source_refused"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_REJECTED = "database_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"

    @property
    def transient(self) -> bool:
        """Whether the same attempt may work later without anyone fixing it."""
        return self in _TRANSIENT


_TRANSIENT: Final = frozenset(
    {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
)

_MODEL_REFUSALS: Final = (
    ChatModelRequestRefused,
    ChatModelRefused,
    ChatModelStructuredOutputUnsupported,
    EmbeddingRequestRefused,
    EmbeddingInputRejected,
)


def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
        return JobFailure.SOURCE_REFUSED
    if database_unavailable(error):
        return JobFailure.DATABASE_UNAVAILABLE
    if isinstance(error, database_errors.DBAPIError):
        # The server answered and said no, such as a broken constraint.
        return JobFailure.DATABASE_REJECTED
    if isinstance(error, _MODEL_REFUSALS):
        return JobFailure.MODEL_REFUSED
    if isinstance(error, (ChatModelError, EmbeddingError)):
        return JobFailure.MODEL_UNAVAILABLE
    return JobFailure.PROGRAMMING_ERROR


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long to wait before each retry: doubling, capped, and jittered.

    The wait before attempt n+1 is half of min(max_delay, base_delay * 2**(n-1))
    plus a random share of the other half, so it is never under half the
    backoff, and Jobs that failed together do not all come back together.
    """

    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(minutes=15)
    # A number from 0 (inclusive) to 1 (exclusive); tests pass a fixed one.
    jitter: Callable[[], float] = field(default=random.random)

    def __post_init__(self) -> None:
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must not be shorter than base_delay")

    def delay(self, failed_attempts: int) -> timedelta:
        """The wait after `failed_attempts` failures, at least one."""
        if type(failed_attempts) is not int or failed_attempts < 1:
            raise ValueError("failed_attempts must be a positive integer")
        share = self.jitter()
        if not 0.0 <= share < 1.0:
            raise ValueError("jitter must return a number from 0 up to 1")
        # Whole microseconds: Python integers cannot overflow, and flooring
        # keeps the result at least half the ceiling and below it.
        base = self.base_delay // timedelta(microseconds=1)
        cap = self.max_delay // timedelta(microseconds=1)
        ceiling = min(cap, base << min(failed_attempts - 1, cap.bit_length()))
        half = ceiling // 2
        return timedelta(microseconds=half + int((ceiling - half) * share))


DEFAULT_RETRY_POLICY: Final = RetryPolicy()


def fail_attempt(
    job: Job,
    error: Exception,
    *,
    now: datetime,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """`job` failed by `error`: classified, and retried later only if worth it.

    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    failure = classify_job_failure(error)
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
        retry_at = now + policy.delay(failed_attempts)
    logger.warning(
        "job %s attempt %d failed: %s (%s); %s",
        job.job_id.value,
        failed_attempts,
        failure.value,
        type(error).__name__,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
