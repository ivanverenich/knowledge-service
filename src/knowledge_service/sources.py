"""Source configuration and synchronization-run state.

Secrets never live in these values. A Source records the *name* of the
credential its adapter needs, never the credential itself.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import SourceId, SynchronizationRunId


class InvalidSource(ValueError):
    """A Source, checkpoint, or Synchronization Run violates an invariant."""


class SourceKind(StrEnum):
    """The kind of origin a Source reads from."""

    LOCAL_DIRECTORY = "local_directory"
    CONFLUENCE = "confluence"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidSource(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class SourceConfiguration:
    """Everything needed to reach a Source, excluding the secret itself."""

    kind: SourceKind
    location: str
    credentials_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise InvalidSource("source location must not be empty")
        if self.credentials_reference is not None and (
            not self.credentials_reference.strip()
        ):
            raise InvalidSource("credentials reference must not be empty when provided")


@dataclass(frozen=True, slots=True)
class Source:
    """Configuration, enabled state, and resume point for one Source."""

    source_id: SourceId
    configuration: SourceConfiguration
    display_name: str
    enabled: bool
    checkpoint: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise InvalidSource("display name must not be empty")
        if self.checkpoint is not None and not self.checkpoint.strip():
            raise InvalidSource("checkpoint must not be empty when provided")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise InvalidSource("updated_at must not be earlier than created_at")

    @classmethod
    def register(
        cls,
        *,
        source_id: SourceId,
        configuration: SourceConfiguration,
        display_name: str,
        registered_at: datetime,
    ) -> Source:
        """Create an enabled Source that has no checkpoint yet."""
        return cls(
            source_id=source_id,
            configuration=configuration,
            display_name=display_name,
            enabled=True,
            checkpoint=None,
            created_at=registered_at,
            updated_at=registered_at,
        )

    def set_enabled(self, *, enabled: bool, observed_at: datetime) -> Source:
        """Turn synchronization for this Source on or off."""
        if enabled is self.enabled:
            return self
        return replace(
            self,
            enabled=enabled,
            updated_at=self._advance(observed_at),
        )

    def record_checkpoint(self, *, checkpoint: str, observed_at: datetime) -> Source:
        """Store the cursor that a later Synchronization Run resumes from."""
        if not checkpoint.strip():
            raise InvalidSource("checkpoint must not be empty")
        return replace(
            self,
            checkpoint=checkpoint,
            updated_at=self._advance(observed_at),
        )

    def _advance(self, observed_at: datetime) -> datetime:
        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidSource("observed_at must not be earlier than updated_at")
        return observed_at


class RunStatus(StrEnum):
    """Lifecycle position of a Synchronization Run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SynchronizationRun:
    """One auditable attempt to reconcile a Source."""

    run_id: SynchronizationRunId
    source_id: SourceId
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
    detail: str | None

    def __post_init__(self) -> None:
        _require_utc(self.started_at, "started_at")
        if self.finished_at is None:
            if self.status is not RunStatus.RUNNING:
                raise InvalidSource("a finished run must record finished_at")
            return
        _require_utc(self.finished_at, "finished_at")
        if self.status is RunStatus.RUNNING:
            raise InvalidSource("a running run must not record finished_at")
        if self.finished_at < self.started_at:
            raise InvalidSource("finished_at must not be earlier than started_at")

    @classmethod
    def start(
        cls,
        *,
        run_id: SynchronizationRunId,
        source_id: SourceId,
        started_at: datetime,
    ) -> SynchronizationRun:
        """Open a run that has not finished yet."""
        return cls(
            run_id=run_id,
            source_id=source_id,
            status=RunStatus.RUNNING,
            started_at=started_at,
            finished_at=None,
            detail=None,
        )

    def succeed(self, *, finished_at: datetime) -> SynchronizationRun:
        """Close the run as successful."""
        return self._update(
            status=RunStatus.SUCCEEDED,
            finished_at=finished_at,
            detail=None,
        )

    def fail(
        self,
        *,
        finished_at: datetime,
        detail: str,
    ) -> SynchronizationRun:
        """Close the run as failed, recording why in non-secret terms."""
        if not detail.strip():
            raise InvalidSource("a failed run must record a non-empty detail")
        return self._update(
            status=RunStatus.FAILED,
            finished_at=finished_at,
            detail=detail,
        )

    def _update(
        self,
        *,
        status: RunStatus,
        finished_at: datetime,
        detail: str | None,
    ) -> SynchronizationRun:
        if self.status is not RunStatus.RUNNING:
            raise InvalidSource(f"a {self.status} run cannot change state")
        return replace(
            self,
            status=status,
            finished_at=finished_at,
            detail=detail,
        )
