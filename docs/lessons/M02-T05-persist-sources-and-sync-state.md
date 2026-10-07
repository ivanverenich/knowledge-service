# M02-T05 — Persist Sources and synchronization state

Source task: [M02-T05](../curriculum/milestones/M02-domain-and-persistence/M02-T05-persist-sources-and-sync-state.md)

## What you will build

This lesson includes coding. You will add the first two real tables to the
project — `sources` and `synchronization_runs` — and the domain values that
describe them. A **Source** is an independently configured origin of
organizational content, such as a directory or a Confluence site. A
**Synchronization Run** is one auditable attempt to bring that Source's content
up to date.

Two rules shape the whole lesson:

- **Secrets never live in a row.** A Source stores the *name* of the credential
  its adapter needs, never the credential itself. The
  [AWS Secrets Manager best practices](https://docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html)
  rule "do not bake or log secrets" applied to a table that gets backed up,
  copied to a test database, and read by anyone with a `SELECT` grant.
- **A Source has one identity.** Two rows must never claim the same origin, or
  later synchronization would maintain two competing copies of the same
  documents. The database enforces this rather than trusting callers.

M02-T04 gave you a migration environment that connects through the
application's own async runtime and a single no-op baseline revision. This
lesson adds the first revision that creates something.

## Before you begin

The pieces you already have:

- `src/knowledge_service/identifiers.py` defines `SourceId` and
  `SynchronizationRunId`. You do not create new identifier types.
- `src/knowledge_service/documents.py` is the shape to copy: frozen dataclasses
  with `slots=True`, validation in `__post_init__`, one typed `ValueError`
  subclass, and a `register`/`reconcile` style of named constructors.
- `migrations/env.py` runs revisions through
  `create_database_runtime(Settings())`, and `target_metadata` stays `None`, so
  **you write every revision by hand**. There is no `--autogenerate`.
- `migrations/versions/20c8d8dcd117_baseline.py` is the revision you will
  follow with `down_revision`.

One architectural rule matters here: domain values must not import FastAPI,
SQLAlchemy, or any provider SDK — see
[architecture.md](../architecture.md). That is why the new module imports
nothing but the standard library and `identifiers.py`, while the tables live in
a migration.

For background, the task points to the [task execution guide](../curriculum/TASK-GUIDE.md),
[architecture overview](../architecture.md), [domain language](../../CONTEXT.md),
and [primary-source map](../research/primary-sources.md).
[ADR 0005](../adr/0005-incremental-at-least-once-synchronization.md) explains
why a Source carries a resumable checkpoint at all.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Decide what may change a Source's row and what must never be
stored there, before any table exists.

**Decision:** Keep this task's notes in `docs/evidence/M02-T05-sources-and-sync-state.md`,
the evidence location named by the task. Create it with the starting text below
and fill in the two sections after reading the prerequisite evidence.

**Action:** Read
[`M02-T04 evidence`](../evidence/M02-T04-alembic-migrations.md) and
[`M02-T02 evidence`](../evidence/M02-T02-document-version-invariants.md). Then
create `docs/evidence/M02-T05-sources-and-sync-state.md`:

```markdown
# M02-T05 — Sources and synchronization state evidence

Completed: pending

## Invariant

Write down what may change a Source's configuration and state, and what must
never be stored in a row.

## Prediction

Write what you expect before adding the tables.

## Verification

Pending.

## Reflection

Pending.
```

Then confirm nothing to be created already exists:

```bash
rg --files src migrations | rg 'sources|synchronization'
```

**Check:** The evidence file contains your invariant and prediction, and the
file search shows no `sources.py` and no revision beyond the baseline. Do not
add code or edit the task or progress files in this step.

### Step 2 — Coding step: model a Source without storing secrets

**Purpose:** Give the rest of the system one object that already knows what a
valid Source is, so no caller has to re-check the same fields.

**Decision:** Put the domain values in a new module,
`src/knowledge_service/sources.py`, with no SQLAlchemy import. Model the
Source's origin as `SourceConfiguration`, and separate the *secret* from the
*reference* to it: `credential_reference` holds a name such as
`KNOWLEDGE_SERVICE_HANDBOOK_TOKEN`, and the value stays in the environment. The
pair `(kind, location)` is what makes an origin unique — the same directory
opened as a local directory and as a Confluence location would be two different
Sources.

**Action:** Create `src/knowledge_service/sources.py` with this first half:

```python
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
    credential_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise InvalidSource("source location must not be empty")
        if self.credential_reference is not None and (
            not self.credential_reference.strip()
        ):
            raise InvalidSource("credential reference must not be empty when provided")


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
        return replace(self, enabled=enabled, updated_at=self._advance(observed_at))

    def record_checkpoint(self, *, checkpoint: str, observed_at: datetime) -> Source:
        """Store the cursor that a later Synchronization Run resumes from."""
        if not checkpoint.strip():
            raise InvalidSource("checkpoint must not be empty")
        return replace(
            self, checkpoint=checkpoint, updated_at=self._advance(observed_at)
        )

    def _advance(self, observed_at: datetime) -> datetime:
        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidSource("observed_at must not be earlier than updated_at")
        return observed_at
```

Three details worth noticing. Dataclasses are frozen, so
`record_checkpoint` returns a **new** `Source` and leaves the old one untouched;
that is what lets the evidence in Step 5 assert that the previous checkpoint is
still reachable. `_advance` keeps every timestamp monotonic, so a source that
has already synchronized cannot be updated by a stale observation. And
`registered_at` is a parameter rather than `datetime.now()`, which keeps the
tests deterministic.

**Check:** The line below reports no errors, and confirms the module does not
import SQLAlchemy:

```bash
rg -n 'sqlalchemy|fastapi' src/knowledge_service/sources.py
```

### Step 3 — Coding step: model a Synchronization Run that can only finish once

**Purpose:** Record each attempt at reconciliation and its outcome, and make
the illegal sequences — finishing a finished run, or calling a run "finished"
with no finish time — impossible to represent.

**Decision:** Three states only: `running`, `succeeded`, `failed`. A finished
run is immutable: `succeed` and `fail` refuse to act on a run that already left
`running`. The fields and the states are chosen so the database can enforce the
same rule in Step 4, instead of the database and the code disagreeing.

**Action:** Append this to `src/knowledge_service/sources.py`:

```python
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
        return self._finish(
            status=RunStatus.SUCCEEDED, finished_at=finished_at, detail=None
        )

    def fail(self, *, finished_at: datetime, detail: str) -> SynchronizationRun:
        """Close the run as failed, recording why in non-secret terms."""
        if not detail.strip():
            raise InvalidSource("a failed run must record a non-empty detail")
        return self._finish(
            status=RunStatus.FAILED, finished_at=finished_at, detail=detail
        )

    def _finish(
        self,
        *,
        status: RunStatus,
        finished_at: datetime,
        detail: str | None,
    ) -> SynchronizationRun:
        if self.status is not RunStatus.RUNNING:
            raise InvalidSource(f"a {self.status} run cannot change state")
        return replace(self, status=status, finished_at=finished_at, detail=detail)
```

`detail` describes a failure in non-secret terms — "source returned 403", not a
token or a request body — because run records are durable and may be copied into
evidence and telemetry.

**Check:** The module imports and both values construct:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "import knowledge_service.sources as s; print(sorted(s.RunStatus))"
```

Expected: `['failed', 'running', 'succeeded']`.

### Step 4 — Coding step: create both tables in one reviewed revision

**Purpose:** Make the two tables exist in a real database through the migration
path you built in M02-T04, with the identity and state rules enforced by
PostgreSQL rather than only by Python.

**Decision:** One revision creates both tables, because a run cannot exist
without a Source. The same-origin rule becomes a `UNIQUE (kind, location)`
constraint, and the run lifecycle becomes two `CHECK` constraints: one for the
allowed statuses, one asserting that `finished_at` is set exactly when the run
is no longer running. Hand-write the revision: `target_metadata` is still
`None`, so Alembic has nothing to autogenerate from, and a reviewer should be
able to read every column here.

**Action:** Generate an empty revision, then write it by hand. This command
only writes a file; it does not need a database:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "sources and synchronization runs"
```

Open the new file under `migrations/versions/`. Keep the identifier Alembic
generated and the `down_revision` it filled in (it should be the baseline,
`20c8d8dcd117`). Remove the generated `from typing import Sequence, Union` line
and the `branch_labels`/`depends_on` lines, exactly as you did for the baseline
revision, and make `upgrade` and `downgrade` read:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<the identifier Alembic generated>"
down_revision: str | Sequence[str] | None = "20c8d8dcd117"


def upgrade() -> None:
    """Create Source configuration and Synchronization Run records."""
    op.create_table(
        "sources",
        sa.Column("source_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("credential_reference", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
    )
    op.create_table(
        "synchronization_runs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name="ck_synchronization_runs_status",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at is null)",
            name="ck_synchronization_runs_finished_at",
        ),
    )


def downgrade() -> None:
    """Remove Synchronization Run and Source records."""
    op.drop_table("synchronization_runs")
    op.drop_table("sources")
```

`sa.Uuid()` stores a real PostgreSQL `uuid`, so an internal identifier keeps its
type in the database instead of degrading into text. `DateTime(timezone=True)`
matches the UTC-aware rule the domain values already enforce. `downgrade` drops
the runs table first because it holds the foreign key — the reverse of creation
order. There is deliberately no `credential_value` column: the reference is a
name, and the secret stays in the environment. The
[Alembic operations reference](https://alembic.sqlalchemy.org/en/latest/ops.html)
documents `create_table` and constraint naming, and the
[PostgreSQL constraints chapter](https://www.postgresql.org/docs/18/ddl-constraints.html)
explains which failures `UNIQUE` and `CHECK` catch.

**Check:** Alembic sees two revisions in one chain and marks the new head:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
```

You should see the baseline followed by your revision, with only one head. Do
not run `upgrade` against a shared database — Steps 6 and 8 use the disposable
one.

### Step 5 — Coding step: unit-test the invariants

**Purpose:** Lock down the domain rules so a later refactor cannot quietly drop
them, and so the failure messages stay stable.

**Decision:** Test through the public constructors only. The successful case
proves a new Source starts enabled with no checkpoint; the edge case proves
recording a checkpoint returns a new value and leaves the old one untouched;
the typed failure proves a naive timestamp is rejected. Every test builds its
input from a fixed `REGISTERED_AT`, so nothing depends on the clock.

**Action:** Create `tests/test_sources.py`:

```python
"""Tests for Source configuration and Synchronization Run invariants."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.sources import (
    InvalidSource,
    RunStatus,
    Source,
    SourceConfiguration,
    SourceKind,
    SynchronizationRun,
)

REGISTERED_AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")


def make_source() -> Source:
    return Source.register(
        source_id=SOURCE_ID,
        configuration=SourceConfiguration(
            kind=SourceKind.LOCAL_DIRECTORY,
            location="/srv/knowledge/handbook",
            credential_reference="KNOWLEDGE_SERVICE_HANDBOOK_TOKEN",
        ),
        display_name="Engineering handbook",
        registered_at=REGISTERED_AT,
    )


def test_registered_source_starts_enabled_without_checkpoint() -> None:
    source = make_source()

    assert source.enabled is True
    assert source.checkpoint is None
    assert source.created_at == REGISTERED_AT


def test_recording_a_checkpoint_keeps_earlier_checkpoints_reachable() -> None:
    source = make_source()

    advanced = source.record_checkpoint(
        checkpoint="page-42", observed_at=REGISTERED_AT + timedelta(minutes=5)
    )

    assert advanced.checkpoint == "page-42"
    assert source.checkpoint is None
    assert advanced.updated_at == REGISTERED_AT + timedelta(minutes=5)


def test_disabling_an_already_disabled_source_is_a_no_op() -> None:
    disabled = make_source().set_enabled(
        enabled=False, observed_at=REGISTERED_AT + timedelta(minutes=1)
    )

    assert (
        disabled.set_enabled(
            enabled=False, observed_at=REGISTERED_AT + timedelta(minutes=9)
        )
        is disabled
    )


def test_run_can_only_finish_once() -> None:
    run = SynchronizationRun.start(
        run_id=SynchronizationRunId.parse("00000000-0000-0000-0000-000000000011"),
        source_id=SOURCE_ID,
        started_at=REGISTERED_AT,
    )

    succeeded = run.succeed(finished_at=REGISTERED_AT + timedelta(minutes=3))

    assert succeeded.status is RunStatus.SUCCEEDED
    with pytest.raises(InvalidSource, match="cannot change state"):
        succeeded.fail(
            finished_at=REGISTERED_AT + timedelta(minutes=4), detail="late failure"
        )


def test_naive_timestamp_raises_typed_failure() -> None:
    source = make_source()

    with pytest.raises(InvalidSource, match="UTC-aware"):
        source.record_checkpoint(
            checkpoint="page-43", observed_at=datetime(2026, 10, 6, 9, 30)
        )


def test_empty_location_raises_typed_failure() -> None:
    with pytest.raises(InvalidSource, match="location"):
        SourceConfiguration(kind=SourceKind.CONFLUENCE, location="   ")
```

**Check:** Run the narrow test:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_sources.py -vv
```

Expected: 6 passed, without a database server.

### Step 6 — Coding step: prove the constraints in PostgreSQL

**Purpose:** The domain values and the database must agree. A test that only
checks the Python object cannot prove that PostgreSQL actually rejects a
duplicate origin or a run that claims to be finished without a finish time.

**Decision:** One integration test against the disposable database. It applies
the migrations, clears the two tables so it can run repeatedly, then asserts the
two rules: the same `(kind, location)` is rejected while the same `location`
under a different `kind` is accepted, and the run lifecycle constraints reject a
finished status with no `finished_at` and an unknown status. Each insert runs in
its own transaction, so a rejected insert cannot leave the connection in an
aborted state.

**Action:** Create `tests/test_sources_integration.py`:

```python
"""Real PostgreSQL tests for the Source and Synchronization Run tables."""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, :kind, :location, 'Handbook',"
    " true, now(), now())"
)

INSERT_RUN = text(
    "INSERT INTO synchronization_runs (run_id, source_id, status, started_at,"
    " finished_at) VALUES (:run_id, :source_id, :status, now(), :finished_at)"
)


@pytest.mark.integration
def test_constraints_reject_ambiguous_identity_and_invalid_run_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")

    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM synchronization_runs"))
            connection.execute(text("DELETE FROM sources"))

        source_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": source_id,
                    "kind": "local_directory",
                    "location": "/srv/handbook",
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": uuid.uuid4(),
                    "kind": "local_directory",
                    "location": "/srv/handbook",
                },
            )

        with engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": uuid.uuid4(),
                    "kind": "confluence",
                    "location": "/srv/handbook",
                },
            )

        with engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "running",
                    "finished_at": None,
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "succeeded",
                    "finished_at": None,
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "abandoned",
                    "finished_at": None,
                },
            )
    finally:
        engine.dispose()
```

The test reads only `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` and temporarily maps
it onto the application setting, so migrations still run through
`create_database_runtime(Settings())` and no production URL is involved.

**Check:** Run it against your disposable database, as you did in M02-T04:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_sources_integration.py -vv
```

Expected: 1 passed. Do not paste the URL into the evidence file or commit it.

### Step 7 — Coding step: keep the earlier baseline test honest

**Purpose:** Your new revision changes what "the last revision" means, and that
silently invalidates an assumption inside the M02-T04 test.

**Decision:** `tests/test_migrations_integration.py` currently calls
`ScriptDirectory.from_config(config).get_current_head()` and treats that as the
baseline, then asserts the database contains nothing but `alembic_version`.
After this lesson the head is your new revision and upgrading to it creates
both tables, so the test would fail — but only when a database URL is set,
because otherwise it is skipped. Fix the assumption rather than the symptom:
find the root revision by following `down_revision` to `None`, and upgrade to
that revision explicitly.

**Action:** In `tests/test_migrations_integration.py`, add this helper below
`PROJECT_ROOT`:

```python
def baseline_revision(config: Config) -> str:
    """Return the root revision identifier, which new revisions may follow."""
    scripts = ScriptDirectory.from_config(config)
    for revision in scripts.walk_revisions():
        if revision.down_revision is None:
            return revision.revision
    raise AssertionError("the migration chain has no baseline revision")
```

Then inside the test, replace the two head-based lines

```python
    baseline_id = ScriptDirectory.from_config(config).get_current_head()
```

with

```python
    baseline_id = baseline_revision(config)
```

and replace every `command.upgrade(config, "head")` with
`command.upgrade(config, baseline_id)`. Also add one line at the start of the
`try:` block, so the test begins from a known empty schema no matter what a
previous run left behind:

```python
        command.downgrade(config, "base")
```

**Check:** Run the migration tests together, in both orders, against the
disposable database — then run the second command again to prove the pair can be
repeated:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_migrations_integration.py tests/test_sources_integration.py -vv
```

Expected: 2 passed, twice in a row.

### Step 8 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole and leave M02-T06 a schema it
can extend.

**Decision:** Run the focused tests first, then the repository's configured
format, lint, type, test, documentation, and hook checks. The opt-in PostgreSQL
tests are skipped by the normal gate, so run them separately and record both
results.

**Action:** From the repository root:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_sources.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_migrations_integration.py tests/test_sources_integration.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T05-sources-and-sync-state.md`, replace the placeholders
with the date, your invariant, the files you changed, the exact observed results,
the PostgreSQL version, and the answers below. Do not include credentials, a
connection URL, or private content.

**Reflection — answer in your own words:**

1. Callers now pass a `Source` or a `SynchronizationRun` and get validation for
   free. Name the specific checks those two objects make on their behalf, and
   say which one you think a caller would most likely forget to write.
2. We could have stored the credential value itself in the `sources` row instead
   of the name of a reference. Describe what gets harder later if we did that —
   think about database backups, test databases, and rotating a leaked secret.
3. The integration test asserts that a duplicate `(kind, location)` is rejected.
   If we deleted that test and also deleted the `UNIQUE` constraint, what would
   go wrong in production that might not be noticed until users complain about
   the answers?

**Check:** The unit tests and both integration tests pass; every repository gate
passes; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `src/knowledge_service/sources.py` models Source configuration, enabled
  state, checkpoints, and run transitions without importing SQLAlchemy.
- [ ] No credential value is stored in a domain value, a table column, or the
  evidence.
- [ ] One hand-written revision creates `sources` and `synchronization_runs`
  with a `UNIQUE (kind, location)` constraint and both run-state `CHECK`
  constraints, and its `downgrade` reverses them.
- [ ] Unit tests cover the successful case, the checkpoint edge case, and typed
  failures.
- [ ] An integration test proves PostgreSQL rejects an ambiguous identity and an
  invalid run state, and accepts a valid one.
- [ ] `tests/test_migrations_integration.py` finds the baseline by revision
  chain rather than by head, and the migration tests still pass.
- [ ] Repository checks pass and the evidence records real results.

When you have finished these steps, share your implementation or any error you
hit and I will review it — I can help you work through a failure without taking
over the implementation.

## Official references

- [Alembic operations reference](https://alembic.sqlalchemy.org/en/latest/ops.html)
- [Alembic migration environment](https://alembic.sqlalchemy.org/en/latest/tutorial.html#the-migration-environment)
- [PostgreSQL constraints](https://www.postgresql.org/docs/18/ddl-constraints.html)
- [SQLAlchemy table metadata](https://docs.sqlalchemy.org/en/21/core/metadata.html)
- [Python `str` enums](https://docs.python.org/3.14/library/enum.html#enum.StrEnum)
- [AWS Secrets Manager best practices](https://docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html)
- [ADR 0005 — incremental at-least-once synchronization](../adr/0005-incremental-at-least-once-synchronization.md)
