# M02-T10 — Persist jobs, audit, and Evaluation Runs

Source task: [M02-T10](../curriculum/milestones/M02-domain-and-persistence/M02-T10-persist-jobs-audit-and-evaluation-runs.md)

## What you will build

This lesson includes coding. It stores three kinds of durable metadata: **Jobs** for queued work, **Audit Events** for what happened, and **Evaluation Runs** for a scoring pass over a versioned dataset.

A **Job** holds:

- what it was queued to do, and what it acts on;
- where it is in its life, and how many attempts it has used;
- when it was queued, started, and finished, and a short error code if it failed.

An **Audit Event** holds the actor, the action, the target, the time, and a small bag of short labels. It holds no content.

An **Evaluation Run** holds the dataset and version it scored, the fingerprint of the configuration it ran, where its artifacts were written, and where it is in its life.

| Property | What it means |
|---|---|
| Two state machines | A Job and an Evaluation Run move only where their transitions allow. |
| No raw content | Audit metadata values are capped, and the database refuses an oversized bag. |
| No payload, no raw error text | A Job carries a target reference and a short error code, never a message. |
| Artifacts only on success | A succeeded run has an artifact location; a failed or pending one has none. |

## Before you begin

- `src/knowledge_service/identifiers.py` has `JobId` and `EvaluationRunId`. It has no identifier for an audit event or an evaluation dataset, so you add two.
- `src/knowledge_service/sources.py` already models a `SynchronizationRun` with `start()`, `succeed()`, and `fail()`. Follow that shape for the two state machines here.
- `src/knowledge_service/persistence.py` holds the tables and functions built so far. This lesson adds to that module.
- Integration tests apply migrations in a **synchronous** fixture, because `migrations/env.py` calls `asyncio.run()` and cannot run inside an async test.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix what the three record families must guarantee before writing any table.

**Decision:** Keep notes in `docs/evidence/M02-T10-jobs-audit-and-evaluation-runs.md`.

**Action:** Read the M02-T09 evidence file under `docs/evidence/`. Create `docs/evidence/M02-T10-jobs-audit-and-evaluation-runs.md`:

```markdown
# M02-T10 — Jobs, audit, and Evaluation Runs evidence

Completed: pending

## Invariant

Write down which job transitions are legal, which run transitions are legal, what
an audit record may and may not hold, and what ties an Evaluation Run to the
dataset and configuration it used.

## Prediction

Write what you expect before adding the tables.

## Verification

Pending.

## Reflection

Pending.
```

Confirm nothing to be created exists:

```bash
find src tests migrations -name '*job*' -o -name '*audit*' -o -name '*evaluation*' | grep -v __pycache__
```

**Check:** The evidence file holds your invariant and prediction, and the search shows no `jobs.py`, no `audit.py`, no `evaluation.py`, and no new table in `persistence.py`. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: add the two missing identifiers

**Purpose:** Give an audit event and an evaluation dataset a typed identity, like every other entity.

**Decision:** Add `AuditEventId` next to `EvaluationRunId`, and `EvaluationDatasetId` beside them. Both use the shared `StableIdentifier` base.

**Action:** In `src/knowledge_service/identifiers.py`, add before `EvaluationRunId`:

```python
class EvaluationDatasetId(StableIdentifier):
    """Internal identity of an Evaluation Dataset."""


class AuditEventId(StableIdentifier):
    """Internal identity of a recorded audit event."""
```

Then in `tests/test_indentifiers.py`, add both names to the import list and to `ID_TYPES`, so the existing parametrized tests cover them.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -q
```

### Step 3 — Coding step: model a Job and its transitions

**Purpose:** Make an illegal job transition impossible to express, and keep a queue row free of payload and raw error text.

**Decision:** Private `_ALLOWED` map plus `_check_transition()`, so the state machine is one readable table rather than conditions scattered through the methods. `request()` queues, `start()` claims, `succeed()` and `fail()` finish, and `retry()` requeues while attempts remain — raising `JobNotRetryable` once they do not. `job_id` carries a typed `JobId`, but `target_id` is a bare `UUID`: the `kind` column is what gives it meaning, and a typed column per kind would leave most of them null forever. `fail()` caps the error code at 64 characters, so a raw exception message cannot be stored as if it were a code.

**Action:** Create `src/knowledge_service/jobs.py`:

```python
"""Job values: durable metadata for one piece of queued work.

A Job records what should happen and how far it got. It carries no payload and
no raw error text, so a queue row stays small and safe to read.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from knowledge_service.identifiers import JobId

MAX_ERROR_CODE_LENGTH = 64


class InvalidJob(ValueError):
    """A Job value violates a domain invariant."""


class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job has already used every attempt it is allowed."""


class JobKind(StrEnum):
    """What a Job was queued to do."""

    SYNCHRONIZE_SOURCE = "synchronize_source"
    PURGE_CONVERSATION = "purge_conversation"


class JobStatus(StrEnum):
    """Where a Job is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.SUCCEEDED: frozenset(),
}


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidJob(f"{field_name} must be a UTC-aware timestamp")


def _check_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _ALLOWED[current]:
        raise IllegalJobTransition(f"a {current} job cannot become {target}")


@dataclass(frozen=True, slots=True)
class Job:
    """One piece of durable queued work."""

    job_id: JobId
    kind: JobKind
    target_id: UUID
    status: JobStatus
    attempt: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
        if type(self.attempt) is not int or not 0 <= self.attempt <= self.max_attempts:
            raise InvalidJob("attempt must be between zero and max attempts")
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
            if self.started_at < self.queued_at:
                raise InvalidJob("started_at must not be earlier than queued_at")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if (self.status is JobStatus.QUEUED) == (self.started_at is not None):
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")

    @classmethod
    def request(
        cls,
        *,
        job_id: JobId,
        kind: JobKind,
        target_id: UUID,
        queued_at: datetime,
        max_attempts: int = 3,
    ) -> Job:
        """Queue one piece of work."""
        return cls(
            job_id=job_id,
            kind=kind,
            target_id=target_id,
            status=JobStatus.QUEUED,
            attempt=0,
            max_attempts=max_attempts,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
        )

    def start(self, *, started_at: datetime) -> Job:
        """Claim the Job and begin one attempt."""
        _check_transition(self.status, JobStatus.RUNNING)
        return replace(self, status=JobStatus.RUNNING, started_at=started_at)

    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(self, *, error_code: str, finished_at: datetime) -> Job:
        """Finish the Job unsuccessfully, recording a short error code."""
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
                f"an error code must be at most {MAX_ERROR_CODE_LENGTH} characters"
            )
        return replace(
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
        )
```

`fail()` increments `attempt`, and `retry()` clears the started, finished, and error fields while keeping the count, so a requeued Job looks exactly like a fresh one except for how many attempts it has used.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import Job, JobKind
now = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
job = Job.request(job_id=JobId.new(), kind=JobKind.SYNCHRONIZE_SOURCE, target_id=uuid4(), queued_at=now)
print(job.status, job.start(started_at=now + timedelta(seconds=1)).status)
"
```

### Step 4 — Coding step: model an audit event with no room for content

**Purpose:** Make "no raw content" a rule the value enforces, not a habit every caller keeps.

**Decision:** Three limits. A value must be a short string, an integer, or a boolean — so no list, no nested mapping, and no float. A string value is capped at 64 characters, which is long enough for a kind, a status, or an id and far too short for a Question or an Answer. The bag holds at most 8 entries. `MappingProxyType` freezes the bag after construction. `actor_id` is optional, because the service itself acts too.

**Action:** Create `src/knowledge_service/audit.py`:

```python
"""Audit event values: what happened, without the content it happened to.

An event names an actor, an action, and a target, and it may carry a small bag
of short scalar labels. The bag is capped, so Question text, Answer text, or
Document text cannot be recorded here by accident.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from knowledge_service.identifiers import AuditEventId, UserId

MAX_METADATA_VALUE_LENGTH = 64
MAX_METADATA_ENTRIES = 8

AuditValue = str | int | bool


class InvalidAuditEvent(ValueError):
    """An audit event violates a domain invariant."""


class AuditAction(StrEnum):
    """What an actor did."""

    SOURCE_REGISTERED = "source.registered"
    DOCUMENT_AVAILABILITY_CHANGED = "document.availability_changed"
    CONVERSATION_DELETED = "conversation.deleted"
    JOB_QUEUED = "job.queued"
    JOB_FAILED = "job.failed"
    EVALUATION_RUN_STARTED = "evaluation_run.started"


class AuditTargetKind(StrEnum):
    """What an action was done to."""

    SOURCE = "source"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    JOB = "job"
    EVALUATION_RUN = "evaluation_run"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidAuditEvent(f"{field_name} must be a UTC-aware timestamp")


def _checked_metadata(
    metadata: Mapping[str, AuditValue],
) -> Mapping[str, AuditValue]:
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise InvalidAuditEvent(
            f"metadata holds at most {MAX_METADATA_ENTRIES} entries"
        )

    checked: dict[str, AuditValue] = {}
    for key, value in metadata.items():
        if not key.strip():
            raise InvalidAuditEvent("metadata keys must not be empty")
        if type(value) is str:
            if not value.strip():
                raise InvalidAuditEvent("metadata values must not be empty")
            if len(value) > MAX_METADATA_VALUE_LENGTH:
                raise InvalidAuditEvent(
                    "metadata values must be at most"
                    f" {MAX_METADATA_VALUE_LENGTH} characters"
                )
        elif type(value) is not int and type(value) is not bool:
            raise InvalidAuditEvent(
                "metadata values must be short strings, integers, or booleans"
            )
        checked[key] = value
    return MappingProxyType(checked)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action, with no raw content."""

    event_id: AuditEventId
    occurred_at: datetime
    actor_id: UserId | None
    action: AuditAction
    target_kind: AuditTargetKind
    target_id: UUID
    metadata: Mapping[str, AuditValue]

    def __post_init__(self) -> None:
        _require_utc(self.occurred_at, "occurred_at")
        object.__setattr__(self, "metadata", _checked_metadata(self.metadata))
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from datetime import UTC, datetime
from uuid import uuid4
from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind, InvalidAuditEvent
try:
    AuditEvent(event_id=__import__('knowledge_service.identifiers', fromlist=['AuditEventId']).AuditEventId.new(), occurred_at=datetime(2026, 10, 8, 9, 0, tzinfo=UTC), actor_id=None, action=AuditAction.JOB_QUEUED, target_kind=AuditTargetKind.JOB, target_id=uuid4(), metadata={'question': 'How do I deploy?' * 20})
except InvalidAuditEvent as error:
    print('refused:', error)
"
```

### Step 5 — Coding step: model an Evaluation Run over a dataset version

**Purpose:** Tie a run to the dataset version and configuration it used, and to the artifacts it produced.

**Decision:** The run stores `dataset_id` with `dataset_version`, the `configuration_fingerprint`, and an `artifact_location` that is set only on success. `plan()`, `start()`, `succeed()`, and `fail()` are the same shape as the Job, with two differences: a run has no retry, because rerunning the same configuration over the same dataset version is a fresh run rather than a continuation; and `succeed()` requires the artifact location, so a successful run cannot be recorded without saying where its output went.

**Action:** Create `src/knowledge_service/evaluation.py`:

```python
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
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from datetime import UTC, datetime, timedelta
from knowledge_service.evaluation import EvaluationRun
from knowledge_service.identifiers import EvaluationDatasetId, EvaluationRunId
now = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
run = EvaluationRun.plan(run_id=EvaluationRunId.new(), dataset_id=EvaluationDatasetId.new(), dataset_version=3, configuration_fingerprint='config-a', created_at=now)
print(run.status, run.start(started_at=now).status)
"
```

### Step 6 — Coding step: create the three tables

**Purpose:** Store the records where SQL can select and constrain them.

**Decision:** Every rule the values enforce gets a CHECK that says the same thing, so a direct `INSERT` cannot create a row the domain would refuse. The audit table adds one rule the domain cannot express on its own: `pg_column_size(metadata) <= 1024`, which refuses an oversized bag even when it arrives as raw SQL.

**Action:** Generate an empty revision, then write it by hand:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "jobs, audit events, and evaluation runs"
```

Keep the generated identifier and the `down_revision` Alembic fills in — `4c3e640575eb`. Remove the generated `typing` and `branch_labels` lines as before, and replace `<the identifier Alembic generated>` with the real identifier.

```python
"""jobs, audit events, and evaluation runs

Revision ID: a7d91c0e5f42
Revises: 4c3e640575eb
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a7d91c0e5f42"
down_revision: str | Sequence[str] | None = "4c3e640575eb"


def upgrade() -> None:
    """Create queued work, the audit trail, and Evaluation Runs."""
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "kind in ('synchronize_source', 'purge_conversation')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 and attempt >= 0 and attempt <= max_attempts",
            name="ck_jobs_attempt",
        ),
        sa.CheckConstraint(
            "(status in ('succeeded', 'failed')) = (finished_at is not null)",
            name="ck_jobs_finished_at",
        ),
        sa.CheckConstraint(
            "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (last_error_code is not null)",
            name="ck_jobs_last_error_code",
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_kind", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.CheckConstraint(
            "action in ('source.registered', 'document.availability_changed',"
            " 'conversation.deleted', 'job.queued', 'job.failed',"
            " 'evaluation_run.started')",
            name="ck_audit_events_action",
        ),
        sa.CheckConstraint(
            "target_kind in ('source', 'document', 'conversation', 'job',"
            " 'evaluation_run')",
            name="ck_audit_events_target_kind",
        ),
        sa.CheckConstraint(
            "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
        ),
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("artifact_location", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evaluation_runs_status",
        ),
        sa.CheckConstraint("dataset_version >= 1", name="ck_evaluation_runs_dataset"),
        sa.CheckConstraint(
            "(status = 'succeeded') = (artifact_location is not null)",
            name="ck_evaluation_runs_artifact_location",
        ),
        sa.CheckConstraint(
            "(status in ('succeeded', 'failed')) = (finished_at is not null)",
            name="ck_evaluation_runs_finished_at",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (started_at is null)",
            name="ck_evaluation_runs_started_at",
        ),
    )


def downgrade() -> None:
    """Remove Evaluation Runs, the audit trail, and queued work."""
    op.drop_table("evaluation_runs")
    op.drop_table("audit_events")
    op.drop_table("jobs")
```

Then add the matching tables to `src/knowledge_service/persistence.py`, after `message_feedback`:

```python
jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("job_id", sa.Uuid(), primary_key=True),
    sa.Column("kind", sa.String(length=32), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("max_attempts", sa.Integer(), nullable=False),
    sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation')", name="ck_jobs_kind"
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
        "max_attempts >= 1 and attempt >= 0 and attempt <= max_attempts",
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
    ),
    sa.CheckConstraint(
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    sa.Column("event_id", sa.Uuid(), primary_key=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("actor_id", sa.Uuid(), nullable=True),
    sa.Column("action", sa.String(length=48), nullable=False),
    sa.Column("target_kind", sa.String(length=24), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.CheckConstraint(
        "action in ('source.registered', 'document.availability_changed',"
        " 'conversation.deleted', 'job.queued', 'job.failed',"
        " 'evaluation_run.started')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
        "target_kind in ('source', 'document', 'conversation', 'job',"
        " 'evaluation_run')",
        name="ck_audit_events_target_kind",
    ),
    sa.CheckConstraint(
        "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
    ),
)

evaluation_runs = sa.Table(
    "evaluation_runs",
    metadata,
    sa.Column("run_id", sa.Uuid(), primary_key=True),
    sa.Column("dataset_id", sa.Uuid(), nullable=False),
    sa.Column("dataset_version", sa.Integer(), nullable=False),
    sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("artifact_location", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "status in ('pending', 'running', 'succeeded', 'failed')",
        name="ck_evaluation_runs_status",
    ),
    sa.CheckConstraint("dataset_version >= 1", name="ck_evaluation_runs_dataset"),
    sa.CheckConstraint(
        "(status = 'succeeded') = (artifact_location is not null)",
        name="ck_evaluation_runs_artifact_location",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_evaluation_runs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'pending') = (started_at is null)",
        name="ck_evaluation_runs_started_at",
    ),
)
```

**Check:** Seven revisions, one head, and both definitions agree:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from knowledge_service.persistence import audit_events, evaluation_runs, jobs
for table in (jobs, audit_events, evaluation_runs):
    print(table.name, sorted(c.name for c in table.constraints if c.name))
"
```

### Step 7 — Coding step: read and write the three records

**Purpose:** Provide one way to create each record, one way to read one back, and one way to store a status that has moved.

**Decision:** Three pairs plus an append-only writer for the audit trail. There is no function that updates or deletes an audit event — that is what makes the trail a trail. `save_job` and `save_evaluation_run` write the row the domain value already produced, so persistence never decides a transition itself. A Job's status is not part of its `ON CONFLICT` handling; the row is updated by id, and the database CHECKs refuse an inconsistent combination.

**Action:** In `src/knowledge_service/persistence.py`, extend the imports:

```python
"""PostgreSQL persistence for the stored domain records.

Domain values stay free of SQLAlchemy. This module owns the stored table shapes,
the translation between a domain value and a row, and the statements that read
and write them.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import (
    AccessGrant,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.chunks import Chunk, StaleChunkWrite, ordered_chunks
from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.evaluation import (
    EvaluationRun,
    EvaluationStatus,
    InvalidEvaluationRun,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    AuditEventId,
    ChunkId,
    ConversationId,
    DocumentId,
    EvaluationDatasetId,
    EvaluationRunId,
    GroupId,
    JobId,
    MessageId,
    SourceId,
    UserId,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
```

Add the row helpers next to the conversation ones:

```python
def _job_row(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id.value,
        "kind": job.kind.value,
        "target_id": job.target_id,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_error_code": job.last_error_code,
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidJob("stored job kind or status is not a known value") from None

    return Job(
        job_id=JobId(cast(UUID, record["job_id"])),
        kind=kind,
        target_id=cast(UUID, record["target_id"]),
        status=status,
        attempt=cast(int, record["attempt"]),
        max_attempts=cast(int, record["max_attempts"]),
        queued_at=cast(datetime, record["queued_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
        last_error_code=cast("str | None", record["last_error_code"]),
    )


def _audit_row(event: AuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id.value,
        "occurred_at": event.occurred_at,
        "actor_id": event.actor_id.value if event.actor_id is not None else None,
        "action": event.action.value,
        "target_kind": event.target_kind.value,
        "target_id": event.target_id,
        "metadata": dict(event.metadata),
    }


def _audit_from(record: Mapping[str, object]) -> AuditEvent:
    try:
        action = AuditAction(cast(str, record["action"]))
        target_kind = AuditTargetKind(cast(str, record["target_kind"]))
    except ValueError:
        raise InvalidAuditEvent(
            "stored audit action or target kind is not a known value"
        ) from None

    actor_id = cast("UUID | None", record["actor_id"])
    stored = cast("dict[str, object]", record["metadata"])
    return AuditEvent(
        event_id=AuditEventId(cast(UUID, record["event_id"])),
        occurred_at=cast(datetime, record["occurred_at"]),
        actor_id=UserId(actor_id) if actor_id is not None else None,
        action=action,
        target_kind=target_kind,
        target_id=cast(UUID, record["target_id"]),
        metadata=cast("dict[str, AuditValue]", stored),
    )


def _evaluation_row(run: EvaluationRun) -> dict[str, object]:
    return {
        "run_id": run.run_id.value,
        "dataset_id": run.dataset_id.value,
        "dataset_version": run.dataset_version,
        "configuration_fingerprint": run.configuration_fingerprint,
        "status": run.status.value,
        "artifact_location": run.artifact_location,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _evaluation_from(record: Mapping[str, object]) -> EvaluationRun:
    try:
        status = EvaluationStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidEvaluationRun("stored run status is not a known value") from None

    return EvaluationRun(
        run_id=EvaluationRunId(cast(UUID, record["run_id"])),
        dataset_id=EvaluationDatasetId(cast(UUID, record["dataset_id"])),
        dataset_version=cast(int, record["dataset_version"]),
        configuration_fingerprint=cast(str, record["configuration_fingerprint"]),
        status=status,
        artifact_location=cast("str | None", record["artifact_location"]),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
    )


```

Then add the functions at the end of the module:

```python
async def enqueue_job(connection: AsyncConnection, job: Job) -> JobId:
    """Store a queued Job."""
    await connection.execute(sa.insert(jobs).values(_job_row(job)))
    return job.job_id


async def load_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
    """Read a stored Job, if it exists."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached."""
    statement = (
        sa.update(jobs).where(jobs.c.job_id == job.job_id.value).values(_job_row(job))
    )
    await connection.execute(statement)
    return job


async def record_audit_event(
    connection: AsyncConnection, event: AuditEvent
) -> AuditEventId:
    """Append one audit event. There is no function that updates or deletes one."""
    await connection.execute(sa.insert(audit_events).values(_audit_row(event)))
    return event.event_id


async def load_audit_events(
    connection: AsyncConnection, *, target_id: UUID, limit: int
) -> tuple[AuditEvent, ...]:
    """Read the trail recorded against one target, oldest first."""
    statement = (
        sa.select(audit_events)
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_audit_from(dict(record)) for record in records)


async def create_evaluation_run(
    connection: AsyncConnection, run: EvaluationRun
) -> EvaluationRunId:
    """Store a planned Evaluation Run."""
    await connection.execute(sa.insert(evaluation_runs).values(_evaluation_row(run)))
    return run.run_id


async def load_evaluation_run(
    connection: AsyncConnection, *, run_id: EvaluationRunId
) -> EvaluationRun | None:
    """Read a stored Evaluation Run, if it exists."""
    statement = sa.select(evaluation_runs).where(
        evaluation_runs.c.run_id == run_id.value
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _evaluation_from(dict(record))


async def save_evaluation_run(
    connection: AsyncConnection, *, run: EvaluationRun
) -> EvaluationRun:
    """Store the status a run has reached."""
    statement = (
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.run_id == run.run_id.value)
        .values(_evaluation_row(run))
    )
    await connection.execute(statement)
    return run
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 8 — Coding step: test the two state machines without a database

**Purpose:** Pin down the transitions, the retry limit, and the content rules, which are pure logic.

**Decision:** One test file per module. Each covers the successful path, the most important edge case — retrying until attempts run out, a metadata value that is too long, an artifact-less success — and a typed failure.

**Action:** Create `tests/test_jobs.py`:

```python
"""Tests for the Job state machine."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
)

QUEUED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
TARGET_ID = uuid4()


def requested_job(*, max_attempts: int = 2) -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=TARGET_ID,
        queued_at=QUEUED_AT,
        max_attempts=max_attempts,
    )


def test_a_job_runs_and_succeeds() -> None:
    job = requested_job()
    started = job.start(started_at=QUEUED_AT + timedelta(seconds=1))
    finished = started.succeed(finished_at=QUEUED_AT + timedelta(seconds=5))

    assert job.status is JobStatus.QUEUED
    assert started.status is JobStatus.RUNNING
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.attempt == 0
    assert finished.finished_at == QUEUED_AT + timedelta(seconds=5)


def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=2)
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=5)
    )

    assert first.attempt == 1
    assert second.status is JobStatus.QUEUED
    assert second.attempt == 1
    assert second.started_at is None
    assert second.last_error_code is None
    assert second_failure.attempt == 2

    with pytest.raises(JobNotRetryable):
        second_failure.retry(queued_at=QUEUED_AT + timedelta(seconds=6))


def test_a_queued_job_cannot_succeed() -> None:
    with pytest.raises(IllegalJobTransition, match="queued"):
        requested_job().succeed(finished_at=QUEUED_AT + timedelta(seconds=1))


def test_a_raw_error_message_is_rejected() -> None:
    running = requested_job().start(started_at=QUEUED_AT + timedelta(seconds=1))

    with pytest.raises(InvalidJob, match="at most"):
        running.fail(error_code="x" * 65, finished_at=QUEUED_AT + timedelta(seconds=2))
```

Then `tests/test_audit.py`:

```python
"""Tests for content-free audit events."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.identifiers import AuditEventId, UserId

OCCURRED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")


def make_event(**metadata: object) -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=USER_ID,
        action=AuditAction.SOURCE_REGISTERED,
        target_kind=AuditTargetKind.SOURCE,
        target_id=uuid4(),
        metadata=cast("dict[str, AuditValue]", metadata),
    )


def test_an_event_records_short_labels() -> None:
    event = make_event(kind="local_directory", enabled=True, documents=12)

    assert event.metadata == {
        "kind": "local_directory",
        "enabled": True,
        "documents": 12,
    }
    assert event.actor_id == USER_ID


def test_an_actor_may_be_the_service_itself() -> None:
    event = AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=None,
        action=AuditAction.JOB_FAILED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        metadata={},
    )

    assert event.actor_id is None


def test_a_long_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event(question="How do I deploy the service?" * 10)


def test_a_structured_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="short strings"):
        make_event(candidates=["chunk-1", "chunk-2"])


def test_an_empty_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="must not be empty"):
        make_event(reason="")


def test_a_metadata_bag_has_a_ceiling() -> None:
    too_many = {f"key{index}": index for index in range(9)}

    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event(**too_many)


def test_a_naive_event_time_raises_typed_failure() -> None:
    with pytest.raises(InvalidAuditEvent, match="UTC-aware"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=datetime(2026, 10, 8, 9, 0),
            actor_id=USER_ID,
            action=AuditAction.CONVERSATION_DELETED,
            target_kind=AuditTargetKind.CONVERSATION,
            target_id=uuid4(),
            metadata={},
        )


def test_metadata_is_frozen_after_construction() -> None:
    event = make_event(kind="local_directory")

    with pytest.raises(TypeError):
        cast("dict[str, AuditValue]", event.metadata)["kind"] = "other"
```

Then `tests/test_evaluation.py`:

```python
"""Tests for the Evaluation Run state machine."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.evaluation import (
    EvaluationRun,
    EvaluationStatus,
    IllegalRunTransition,
    InvalidEvaluationRun,
)
from knowledge_service.identifiers import EvaluationDatasetId, EvaluationRunId

CREATED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
DATASET_ID = EvaluationDatasetId.parse("00000000-0000-0000-0000-000000000060")


def planned_run(*, dataset_version: int = 3) -> EvaluationRun:
    return EvaluationRun.plan(
        run_id=EvaluationRunId.new(),
        dataset_id=DATASET_ID,
        dataset_version=dataset_version,
        configuration_fingerprint="config-a",
        created_at=CREATED_AT,
    )


def test_a_run_starts_and_records_its_artifact() -> None:
    run = planned_run()
    running = run.start(started_at=CREATED_AT + timedelta(seconds=1))
    finished = running.succeed(
        artifact_location="s3://evaluations/run-1",
        finished_at=CREATED_AT + timedelta(minutes=5),
    )

    assert run.status is EvaluationStatus.PENDING
    assert finished.status is EvaluationStatus.SUCCEEDED
    assert finished.artifact_location == "s3://evaluations/run-1"
    assert finished.configuration_fingerprint == "config-a"
    assert finished.dataset_version == 3


def test_a_failed_run_keeps_no_artifact() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))
    failed = running.fail(finished_at=CREATED_AT + timedelta(minutes=1))

    assert failed.status is EvaluationStatus.FAILED
    assert failed.artifact_location is None


def test_a_succeeded_run_cannot_start_again() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))
    finished = running.succeed(
        artifact_location="s3://evaluations/run-1",
        finished_at=CREATED_AT + timedelta(minutes=2),
    )

    with pytest.raises(IllegalRunTransition, match="succeeded"):
        finished.start(started_at=CREATED_AT + timedelta(minutes=3))


def test_a_succeeded_run_needs_an_artifact_location() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))

    with pytest.raises(InvalidEvaluationRun, match="artifact"):
        EvaluationRun(
            run_id=running.run_id,
            dataset_id=running.dataset_id,
            dataset_version=running.dataset_version,
            configuration_fingerprint=running.configuration_fingerprint,
            status=EvaluationStatus.SUCCEEDED,
            artifact_location=None,
            created_at=running.created_at,
            started_at=running.started_at,
            finished_at=CREATED_AT + timedelta(minutes=1),
        )
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_jobs.py tests/test_audit.py tests/test_evaluation.py -vv
```

Expected: 20 passed, without a database.

### Step 9 — Coding step: prove the records survive their state machines

**Purpose:** A pure test cannot show that a status survives a round trip, that the trail reads back in order, or that the database refuses what the domain refuses.

**Decision:** One async integration test with a synchronous migration fixture. It walks a Job from queued through failure to retried, writes two audit events against one target, and records an Evaluation Run that succeeds with an artifact. Then it makes two raw inserts the domain would never produce: an audit bag far past the size limit, and a succeeded run with no artifact.

**Action:** Create `tests/test_records_integration.py`:

```python
"""Real PostgreSQL tests for Jobs, audit events, and Evaluation Runs."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind
from knowledge_service.evaluation import EvaluationRun, EvaluationStatus
from knowledge_service.identifiers import (
    AuditEventId,
    EvaluationDatasetId,
    EvaluationRunId,
    JobId,
    UserId,
)
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence import (
    create_evaluation_run,
    enqueue_job,
    load_audit_events,
    load_evaluation_run,
    load_job,
    record_audit_event,
    save_evaluation_run,
    save_job,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
DATASET_ID = EvaluationDatasetId.parse("00000000-0000-0000-0000-000000000060")

INSERT_RAW_AUDIT = text(
    "INSERT INTO audit_events (event_id, occurred_at, actor_id, action, target_kind,"
    " target_id, metadata) VALUES (:event_id, now(), NULL, 'job.queued', 'job',"
    " :target_id, CAST(:metadata AS jsonb))"
)

INSERT_RAW_RUN = text(
    "INSERT INTO evaluation_runs (run_id, dataset_id, dataset_version,"
    " configuration_fingerprint, status, artifact_location, created_at, started_at,"
    " finished_at) VALUES (:run_id, :dataset_id, 1, 'config-a', 'succeeded', NULL,"
    " now(), now(), now())"
)


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


@pytest.mark.integration
async def test_records_survive_their_state_machines_and_refuse_raw_content(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM evaluation_runs"))
            await connection.execute(text("DELETE FROM audit_events"))
            await connection.execute(text("DELETE FROM jobs"))

        job = Job.request(
            job_id=JobId.new(),
            kind=JobKind.SYNCHRONIZE_SOURCE,
            target_id=uuid4(),
            queued_at=OBSERVED_AT,
            max_attempts=2,
        )

        async with engine.begin() as connection:
            await enqueue_job(connection, job)
            running = job.start(started_at=OBSERVED_AT + timedelta(seconds=1))
            await save_job(connection, job=running)
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
            )
            await save_job(connection, job=failed)

        async with engine.begin() as connection:
            stored_job = await load_job(connection, job_id=job.job_id)

        assert stored_job is not None
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
            await save_job(connection, job=retried)
            reloaded_job = await load_job(connection, job_id=job.job_id)

        assert reloaded_job is not None
        assert reloaded_job.status is JobStatus.QUEUED
        assert reloaded_job.attempt == 1
        assert reloaded_job.started_at is None
        assert reloaded_job.last_error_code is None

        target_id = uuid4()
        async with engine.begin() as connection:
            await record_audit_event(
                connection,
                AuditEvent(
                    event_id=AuditEventId.new(),
                    occurred_at=OBSERVED_AT,
                    actor_id=USER_ID,
                    action=AuditAction.JOB_QUEUED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"kind": "synchronize_source"},
                ),
            )
            await record_audit_event(
                connection,
                AuditEvent(
                    event_id=AuditEventId.new(),
                    occurred_at=OBSERVED_AT + timedelta(seconds=1),
                    actor_id=None,
                    action=AuditAction.JOB_FAILED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"attempt": 1},
                ),
            )

        async with engine.begin() as connection:
            trail = await load_audit_events(connection, target_id=target_id, limit=10)

        assert [event.action for event in trail] == [
            AuditAction.JOB_QUEUED,
            AuditAction.JOB_FAILED,
        ]
        assert trail[0].metadata == {"kind": "synchronize_source"}
        assert trail[0].actor_id == USER_ID
        assert trail[1].actor_id is None

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_AUDIT,
                    {
                        "event_id": AuditEventId.new().value,
                        "target_id": uuid4(),
                        "metadata": json.dumps(
                            {"question": "How do I deploy the service?" * 40}
                        ),
                    },
                )

        run = EvaluationRun.plan(
            run_id=EvaluationRunId.new(),
            dataset_id=DATASET_ID,
            dataset_version=3,
            configuration_fingerprint="config-a",
            created_at=OBSERVED_AT,
        )

        async with engine.begin() as connection:
            await create_evaluation_run(connection, run)
            started_run = run.start(started_at=OBSERVED_AT + timedelta(seconds=1))
            await save_evaluation_run(connection, run=started_run)
            finished_run = started_run.succeed(
                artifact_location="s3://evaluations/run-1",
                finished_at=OBSERVED_AT + timedelta(minutes=1),
            )
            await save_evaluation_run(connection, run=finished_run)

        async with engine.begin() as connection:
            stored_run = await load_evaluation_run(connection, run_id=run.run_id)

        assert stored_run is not None
        assert stored_run.status is EvaluationStatus.SUCCEEDED
        assert stored_run.artifact_location == "s3://evaluations/run-1"
        assert stored_run.configuration_fingerprint == "config-a"
        assert stored_run.dataset_version == 3

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_RUN,
                    {
                        "run_id": EvaluationRunId.new().value,
                        "dataset_id": DATASET_ID.value,
                    },
                )
    finally:
        await engine.dispose()
```

The two raw inserts are the proof that the rules hold at the database and not only in Python. Both are refused with `IntegrityError`, by `ck_audit_events_metadata_size` and `ck_evaluation_runs_artifact_location`.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_records_integration.py -vv
```

Expected: 1 passed. Run it twice to confirm it repeats. Do not paste the URL into the evidence file or commit it.

### Step 10 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole before M02-T11 builds on it.

**Decision:** Run the focused tests first, then every repository gate. The normal gate skips the opt-in PostgreSQL tests, so run those separately and record both results.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_jobs.py tests/test_audit.py tests/test_evaluation.py tests/test_indentifiers.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T10-jobs-audit-and-evaluation-runs.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. `Job.fail()` caps the error code at 64 characters, and an audit event caps every string value the same way. Name what those two caps take off the caller's hands, and describe what a queue row and an audit row would look like a year later without them.
2. A run could have been retried like a job is. Describe what gets harder later if it were — think about comparing the same configuration over the same dataset version twice, and about which result a reader should trust.
3. The integration test asserts that a succeeded run with no artifact location is refused. If we removed that assertion, what production failure might go unnoticed until someone tried to reproduce a result?

**Check:** The unit tests and every integration test pass; all repository gates pass; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `AuditEventId` and `EvaluationDatasetId` exist in `identifiers.py` and are covered by the identifier tests.
- [ ] `src/knowledge_service/jobs.py` models a Job whose transitions are checked against one table, with a retry limit and a capped error code, and imports no SQLAlchemy.
- [ ] `src/knowledge_service/audit.py` models an event whose metadata cannot hold raw content, and imports no SQLAlchemy.
- [ ] `src/knowledge_service/evaluation.py` models a run over a dataset version with an artifact location only on success, and imports no SQLAlchemy.
- [ ] One hand-written revision creates `jobs`, `audit_events`, and `evaluation_runs` with a CHECK for every domain rule and a size guard on the audit bag.
- [ ] `enqueue_job` and `save_job`, `record_audit_event` and `load_audit_events`, and `create_evaluation_run` and `save_evaluation_run` work on the caller's connection; nothing updates or deletes an audit event.
- [ ] Repository checks pass and the evidence records real results.

Share your implementation or any error you hit and I will review it.
