"""Evaluation Run values: one reproducible execution over a versioned dataset.

A run names the dataset and version it scored, the fingerprint of the
configuration it ran, and where its artifacts were written once it finished.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import EvaluationDatasetId, EvaluationRunId


class InvalidEvaluationRun(ValueError):
    """An Evaluation Run value violates a domain invariant."""


class IllegalRunTransition(InvalidEvaluationRun):
    """A caller asked a run to move to a status it cannot reach from here."""


class EvaluationStatus(StrEnum):
    """Where an Evaluation Run is in its life."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_ALLOWED: Mapping[EvaluationStatus, frozenset[EvaluationStatus]] = {
    EvaluationStatus.PENDING: frozenset({EvaluationStatus.RUNNING}),
    EvaluationStatus.RUNNING: frozenset(
        {EvaluationStatus.SUCCEEDED, EvaluationStatus.FAILED}
    ),
    EvaluationStatus.SUCCEEDED: frozenset(),
    EvaluationStatus.FAILED: frozenset(),
}


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidEvaluationRun(f"{field_name} must be a UTC-aware timestamp")


def _check_transition(current: EvaluationStatus, target: EvaluationStatus) -> None:
    if target not in _ALLOWED[current]:
        raise IllegalRunTransition(f"a {current} run cannot become {target}")


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """One reproducible execution of a configuration over a dataset version."""

    run_id: EvaluationRunId
    dataset_id: EvaluationDatasetId
    dataset_version: int
    configuration_fingerprint: str
    status: EvaluationStatus
    artifact_location: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if type(self.dataset_version) is not int or self.dataset_version < 1:
            raise InvalidEvaluationRun("dataset version must be a positive integer")
        if not self.configuration_fingerprint.strip():
            raise InvalidEvaluationRun("configuration fingerprint must not be empty")
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
            if self.started_at < self.created_at:
                raise InvalidEvaluationRun(
                    "started_at must not be earlier than created_at"
                )
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidEvaluationRun(
                    "finished_at must not be earlier than started_at"
                )
        if self.artifact_location is not None and not self.artifact_location.strip():
            raise InvalidEvaluationRun("artifact location must not be empty")
        if (self.status is EvaluationStatus.SUCCEEDED) != (
            self.artifact_location is not None
        ):
            raise InvalidEvaluationRun(
                "only a succeeded run carries an artifact location"
            )
        if (self.status in {EvaluationStatus.SUCCEEDED, EvaluationStatus.FAILED}) != (
            self.finished_at is not None
        ):
            raise InvalidEvaluationRun("only a finished run carries a finished time")
        if (self.status is EvaluationStatus.PENDING) == (self.started_at is not None):
            raise InvalidEvaluationRun(
                "only a run that is not pending carries a started time"
            )

    @classmethod
    def plan(
        cls,
        *,
        run_id: EvaluationRunId,
        dataset_id: EvaluationDatasetId,
        dataset_version: int,
        configuration_fingerprint: str,
        created_at: datetime,
    ) -> EvaluationRun:
        """Plan a run over one dataset version."""
        return cls(
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            configuration_fingerprint=configuration_fingerprint,
            status=EvaluationStatus.PENDING,
            artifact_location=None,
            created_at=created_at,
            started_at=None,
            finished_at=None,
        )

    def start(self, *, started_at: datetime) -> EvaluationRun:
        """Begin the run."""
        _check_transition(self.status, EvaluationStatus.RUNNING)
        return replace(self, status=EvaluationStatus.RUNNING, started_at=started_at)

    def succeed(
        self, *, artifact_location: str, finished_at: datetime
    ) -> EvaluationRun:
        """Finish the run, recording where its artifacts were written."""
        _check_transition(self.status, EvaluationStatus.SUCCEEDED)
        return replace(
            self,
            status=EvaluationStatus.SUCCEEDED,
            artifact_location=artifact_location,
            finished_at=finished_at,
        )

    def fail(self, *, finished_at: datetime) -> EvaluationRun:
        """Finish the run without artifacts."""
        _check_transition(self.status, EvaluationStatus.FAILED)
        return replace(self, status=EvaluationStatus.FAILED, finished_at=finished_at)
