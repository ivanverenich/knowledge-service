# M02-T13 — Add essential relational indexes

Source task: [M02-T13](../curriculum/milestones/M02-domain-and-persistence/M02-T13-add-essential-relational-indexes.md)

## What you will build

This lesson includes coding. It gives the queries the application actually runs an index to read through, and it proves with query plans that the planner uses them.

Four access patterns get one index each:

| Access pattern | Index |
|---|---|
| Conversations due for deletion, oldest first | `ix_conversations_retention_deadline`, partial on live rows |
| The audit trail recorded against one target | `ix_audit_events_target_occurred_at` |
| The recent Synchronization Runs of one Source | `ix_synchronization_runs_source_started_at` |
| The Documents one verified subject may reach | `ix_access_grants_subject_document` |

The rest of the schema needs nothing new, and the lesson shows why rather than asserting it. A foreign key such as `chunks.document_id` is already the leading column of `uq_chunks_document_ordinal`, so PostgreSQL keeps that index and an extra one would only cost writes.

## Before you begin

- `persistence.py` owns the declared table shapes. The retention sweep and the audit trail are the only queries with a filter that no index serves.
- `test_the_migrated_schema_matches_the_declared_tables` from M02-T12 compares the migrations against those tables. An index added in one place and not the other fails it, so both change together here.
- A planner chooses an index only when the data makes it worthwhile. Explaining these queries against a nearly empty table proves nothing, which is why the test seeds rows first.
- `tests/database_fixtures.py` gives every worker its own database, so the test can seed and truncate freely.

## Walkthrough

### Step 1 — No coding in this step: name the access patterns

**Purpose:** Decide which indexes to add from the queries that run, not from the columns that exist.

**Decision:** Keep notes in `docs/evidence/M02-T13-essential-relational-indexes.md`.

**Action:** Read the M02-T11 and M02-T12 evidence files under `docs/evidence/`. Then list what the persistence layer really filters on:

```bash
grep -n "order_by\|\.where(" src/knowledge_service/persistence.py | head -20
```

Every read that filters on a column already leading an index costs nothing extra. Create `docs/evidence/M02-T13-essential-relational-indexes.md`:

```markdown
# M02-T13 — Essential relational indexes evidence

Completed: pending

## Invariant

Write down what an index must not change, and which queries are allowed to ask
for one.

## Prediction

Write what you expect the query plans to show before you add anything.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** The evidence file names each access pattern you intend to serve and says which queries need no new index because a constraint already leads with the column they filter on. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: give each access pattern a name and an index

**Purpose:** Put the index and the statement it serves in the same place, so a test can explain the query the application runs instead of a copy of it.

**Decision:** Four public statement builders, each with the index that serves it, all in `persistence.py`. Two of them replace the statements inside the loaders that already used them, so the loader and the test explain the same SQL. The partial index on conversations covers only live rows, because a deleted conversation is never selected again.

**Action:** In `src/knowledge_service/persistence.py`, add `Any` to the `typing` import:

```python
from typing import Any, cast
```

Add the index to each of these four tables, after the constraints:

```python
    sa.Index(
        "ix_synchronization_runs_source_started_at",
        "source_id",
        "started_at",
    ),
```

```python
    sa.Index(
        "ix_access_grants_subject_document",
        "subject_kind",
        "subject_id",
        "document_id",
    ),
```

```python
    sa.Index(
        "ix_conversations_retention_deadline",
        "retention_deadline",
        postgresql_where=sa.text("deleted_at is null"),
    ),
```

```python
    sa.Index(
        "ix_audit_events_target_occurred_at",
        "target_id",
        "occurred_at",
        "event_id",
    ),
```

Then add the four statement builders and let the two existing loaders call theirs:

```python
def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
    return (
        sa.select(conversations)
        .where(
            conversations.c.deleted_at.is_(None),
            conversations.c.retention_deadline <= now,
        )
        .order_by(conversations.c.retention_deadline)
        .limit(limit)
    )
```

```python
def audit_trail_statement(target_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the trail recorded against one target, oldest first."""
    return (
        sa.select(audit_events)
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
        access_grants.c.subject_id == subject_id,
    )
```

`load_conversations_due_for_deletion` and `load_audit_events` each stop building their own statement and call the builder instead.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -q
```

### Step 3 — Coding step: create the indexes in a migration

**Purpose:** Make the change reach a database that already exists.

**Decision:** One revision creating the four indexes, and dropping them in reverse order on the way down.

**Action:** Create the revision and write the body:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "access pattern indexes"
```

```python
"""access pattern indexes

Revision ID: ecdc3ccf483b
Revises: 2553cef719b4
Create Date: 2026-10-09 09:06:29.862388

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ecdc3ccf483b"
down_revision: str | Sequence[str] | None = "2553cef719b4"


def upgrade() -> None:
    """Create the indexes the named access patterns read through."""
    op.create_index(
        "ix_conversations_retention_deadline",
        "conversations",
        ["retention_deadline"],
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.create_index(
        "ix_audit_events_target_occurred_at",
        "audit_events",
        ["target_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_synchronization_runs_source_started_at",
        "synchronization_runs",
        ["source_id", "started_at"],
    )
    op.create_index(
        "ix_access_grants_subject_document",
        "access_grants",
        ["subject_kind", "subject_id", "document_id"],
    )


def downgrade() -> None:
    """Remove the indexes, returning to sequential scans."""
    op.drop_index("ix_access_grants_subject_document", table_name="access_grants")
    op.drop_index(
        "ix_synchronization_runs_source_started_at",
        table_name="synchronization_runs",
    )
    op.drop_index(
        "ix_audit_events_target_occurred_at", table_name="audit_events"
    )
    op.drop_index(
        "ix_conversations_retention_deadline",
        table_name="conversations",
        postgresql_where=sa.text("deleted_at is null"),
    )
```

Then confirm the migration and the declared tables still agree:

```bash
KNOWLEDGE_SERVICE_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic upgrade head
KNOWLEDGE_SERVICE_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic check
```

**Check:** `alembic check` reports no new operations. Remove an index from `persistence.py` and it names that index as a new operation instead; put it back afterwards.

### Step 4 — Coding step: test the index set without a database

**Purpose:** Keep the set at the four the access patterns need, and keep every foreign key served.

**Decision:** Three assertions and one negative. The first two check that the declared indexes are exactly these four and that the retention one is partial. The third walks every foreign key, because a parent delete must not scan a child table to find the rows it owns. The negative proves that last check raises when a foreign key really is unindexed.

**Action:** Create `tests/test_indexes.py`:

```python
"""Tests for the indexes the named access patterns read through."""

import pytest
import sqlalchemy as sa

from knowledge_service.persistence import metadata

ACCESS_PATTERN_INDEXES = {
    "ix_access_grants_subject_document",
    "ix_audit_events_target_occurred_at",
    "ix_conversations_retention_deadline",
    "ix_synchronization_runs_source_started_at",
}


class ForeignKeyNotIndexed(AssertionError):
    """A foreign key does not start any index, so a parent delete scans the child."""


def leading_index_columns(table: sa.Table) -> set[str]:
    """Return the first column of every index that serves the table."""
    columns = {index.columns[0].name for index in table.indexes}
    for constraint in table.constraints:
        if isinstance(constraint, (sa.PrimaryKeyConstraint, sa.UniqueConstraint)):
            first = list(constraint.columns)
            if first:
                columns.add(first[0].name)
    return columns


def assert_every_foreign_key_leads_an_index(table: sa.Table) -> None:
    """Raise when a foreign key has no index to serve it."""
    leading = leading_index_columns(table)
    for column in table.columns:
        if column.foreign_keys and column.name not in leading:
            raise ForeignKeyNotIndexed(f"{table.name}.{column.name}")


def test_only_the_named_access_patterns_have_an_index() -> None:
    declared = {
        index.name for table in metadata.sorted_tables for index in table.indexes
    }

    assert declared == ACCESS_PATTERN_INDEXES


def test_every_foreign_key_leads_an_index() -> None:
    for table in metadata.sorted_tables:
        assert_every_foreign_key_leads_an_index(table)


def test_the_retention_index_covers_only_live_conversations() -> None:
    conversations = metadata.tables["conversations"]
    index = next(
        index
        for index in conversations.indexes
        if index.name == "ix_conversations_retention_deadline"
    )

    assert [column.name for column in index.columns] == ["retention_deadline"]
    assert str(index.dialect_options["postgresql"]["where"]) == "deleted_at is null"


def test_a_foreign_key_with_no_index_is_reported() -> None:
    unindexed = sa.Table(
        "probe_child",
        sa.MetaData(),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("probe_parent.parent_id")),
    )

    with pytest.raises(ForeignKeyNotIndexed, match=r"probe_child\.parent_id"):
        assert_every_foreign_key_leads_an_index(unindexed)
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indexes.py -vv
```

Expected: 4 passed. `test_every_foreign_key_leads_an_index` is the one that fails if a later table arrives with an unindexed foreign key.

### Step 5 — Coding step: prove the plans use the indexes

**Purpose:** An index nobody uses is a write cost with no read benefit.

**Decision:** Seed enough rows for the planner to have a reason to choose an index, then read the plan back as JSON and assert the index appears. The seed carries two hundred subjects across two thousand grants, because with nine subjects each query would match a tenth of the table and a sequential scan would be the correct plan.

**Action:** Create `tests/test_indexes_integration.py`:

```python
"""Real PostgreSQL tests for the indexes the access patterns read through."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from database_fixtures import SYNC_DRIVER
from knowledge_service.persistence import (
    audit_trail_statement,
    due_conversations_statement,
    source_runs_statement,
    subject_grants_statement,
)

SOURCE_ID = UUID("10000000-0000-0000-0000-000000000002")
SUBJECT_ID = UUID("20000000-0000-0000-0000-000000000002")
TARGET_ID = UUID("30000000-0000-0000-0000-000000000002")

SOURCE_PREFIX = "10000000-0000-0000-0000-"
SUBJECT_PREFIX = "20000000-0000-0000-0000-"
TARGET_PREFIX = "30000000-0000-0000-0000-"

SOURCE_COUNT = 9
SUBJECT_COUNT = 200
TABLE_ROWS = 2000

SEED: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
        " created_at, updated_at) SELECT (:prefix || lpad(i::text, 12, '0'))::uuid,"
        " 'local_directory', '/srv/' || i, 'Source ' || i, true, now(), now()"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": SOURCE_PREFIX, "rows": SOURCE_COUNT},
    ),
    (
        "INSERT INTO synchronization_runs (run_id, source_id, status, started_at,"
        " finished_at) SELECT gen_random_uuid(),"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid, 'succeeded',"
        " now() - (i || ' seconds')::interval, now() - (i || ' seconds')::interval"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": SOURCE_PREFIX, "sources": SOURCE_COUNT, "rows": TABLE_ROWS},
    ),
    (
        "INSERT INTO documents (document_id, source_id, external_id, content_version,"
        " content_fingerprint, authorization_version, authorization_fingerprint,"
        " availability, created_at, updated_at) SELECT gen_random_uuid(),"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid,"
        " 'doc-' || i, 1,"
        " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now()"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": SOURCE_PREFIX, "sources": SOURCE_COUNT, "rows": TABLE_ROWS},
    ),
    (
        "INSERT INTO access_grants (grant_id, document_id, subject_kind, subject_id,"
        " granted_at) SELECT gen_random_uuid(), document_id, 'group',"
        " (:prefix || lpad("
        "  (mod(row_number() OVER (), :subjects) + 1)::text, 12, '0'))::uuid,"
        " now() FROM documents",
        {"prefix": SUBJECT_PREFIX, "subjects": SUBJECT_COUNT},
    ),
    (
        "INSERT INTO conversations (conversation_id, owner_id, created_at,"
        " last_message_at, retention_deadline, deleted_at) SELECT gen_random_uuid(),"
        " gen_random_uuid(), now() - interval '400 days', now() - interval '400 days',"
        " now() - (i || ' hours')::interval, NULL"
        " FROM generate_series(1, :rows) AS i",
        {"rows": TABLE_ROWS},
    ),
    (
        "INSERT INTO audit_events (event_id, occurred_at, actor_id, action,"
        " target_kind, target_id, metadata) SELECT gen_random_uuid(),"
        " now() - (i || ' seconds')::interval, NULL, 'job.queued', 'job',"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid, '{}'::jsonb"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": TARGET_PREFIX, "sources": SOURCE_COUNT, "rows": TABLE_ROWS},
    ),
    ("ANALYZE", {}),
)


class IndexNotUsed(AssertionError):
    """The planner did not use the index a named access pattern relies on."""


async def seed_rows(connection: AsyncConnection) -> None:
    """Fill the tables so the planner has a reason to choose an index."""
    for sql, parameters in SEED:
        await connection.execute(sa.text(sql), parameters)


def plan_indexes(plan: dict[str, Any]) -> Iterator[str]:
    """Yield every index name in one EXPLAIN (FORMAT JSON) plan tree."""
    if "Index Name" in plan:
        yield str(plan["Index Name"])
    for child in plan.get("Plans", []):
        yield from plan_indexes(child)


def plan_node_types(plan: dict[str, Any]) -> Iterator[str]:
    """Yield every node type in one EXPLAIN (FORMAT JSON) plan tree."""
    yield str(plan["Node Type"])
    for child in plan.get("Plans", []):
        yield from plan_node_types(child)


async def explained_plan(
    connection: AsyncConnection, statement: sa.Select[Any]
) -> dict[str, Any]:
    """Ask PostgreSQL how it would run one statement."""
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    records = (
        (await connection.execute(sa.text(f"EXPLAIN (FORMAT JSON) {sql}")))
        .scalars()
        .all()
    )
    plan: dict[str, Any] = records[0][0]["Plan"]
    return plan


def assert_index_used(plan: dict[str, Any], index: str) -> None:
    """Raise when the plan does not read through the expected index."""
    if index not in set(plan_indexes(plan)):
        raise IndexNotUsed(f"the plan does not use {index}")


def statements_by_index() -> dict[str, sa.Select[Any]]:
    """Pair each index with the statement of the access pattern it serves."""
    return {
        "ix_conversations_retention_deadline": due_conversations_statement(
            datetime.now(UTC), 10
        ),
        "ix_audit_events_target_occurred_at": audit_trail_statement(TARGET_ID, 10),
        "ix_synchronization_runs_source_started_at": source_runs_statement(
            SOURCE_ID, 5
        ),
        "ix_access_grants_subject_document": subject_grants_statement(
            "group", SUBJECT_ID
        ),
    }


@pytest.mark.integration
async def test_each_named_access_pattern_reads_through_its_index(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername=SYNC_DRIVER)
    )
    try:
        async with engine.begin() as connection:
            await seed_rows(connection)

        async with engine.connect() as connection:
            for index, statement in statements_by_index().items():
                plan = await explained_plan(connection, statement)
                assert_index_used(plan, index)
                assert "Seq Scan" not in set(plan_node_types(plan))
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_a_plan_without_its_index_is_reported(
    migrated_database: str,
) -> None:
    """Prove the check can fail, without leaving the index gone."""
    engine = create_async_engine(
        make_url(migrated_database).set(drivername=SYNC_DRIVER)
    )
    try:
        async with engine.begin() as connection:
            await seed_rows(connection)

        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                sa.text("DROP INDEX ix_audit_events_target_occurred_at")
            )

            plan = await explained_plan(
                connection, audit_trail_statement(TARGET_ID, 10)
            )
            with pytest.raises(IndexNotUsed, match="ix_audit_events"):
                assert_index_used(plan, "ix_audit_events_target_occurred_at")

            await transaction.rollback()

        async with engine.connect() as connection:
            plan = await explained_plan(
                connection, audit_trail_statement(TARGET_ID, 10)
            )
            assert_index_used(plan, "ix_audit_events_target_occurred_at")
    finally:
        await engine.dispose()
```

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_indexes_integration.py -vv
```

Expected: 2 passed. Run it a few times: a plan test that passes once and fails on the next run is telling you the data does not justify the index.

### Step 6 — No coding in this step: record the plans and run the gates

**Purpose:** The task asks for query-plan evidence, and the evidence is what the planner actually chose.

**Decision:** Record the plan for each access pattern, and the plan that shows an already-covered column is served by its constraint.

**Action:** Explain the patterns against a seeded database and keep the output:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test -tAc \
  "EXPLAIN (COSTS OFF) SELECT conversation_id FROM conversations
   WHERE deleted_at IS NULL AND retention_deadline <= now()
   ORDER BY retention_deadline LIMIT 10"
```

Then run the gates:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T13-essential-relational-indexes.md`, replace the placeholders with the date, your invariant, the files you changed, the plan output for each access pattern, the exact test results, the PostgreSQL version, and the answers below. Add no credential or connection URL.

**Reflection — answer in your own words:**

1. The four indexes are chosen from queries that already run, and two of the statement builders have no caller outside a test. Say what the builders buy you, and what you would lose by inlining the SQL in the test instead.
2. The foreign-key indexes were left out because a unique constraint already leads with the same column. Explain what makes that true for `chunks.document_id` but false for `synchronization_runs.source_id`.
3. The plan test seeds two thousand rows before it explains anything, and the ACL seed spreads grants across two hundred subjects. What production failure would a plan test on an empty table report as success?

**Check:** The unit tests and both integration tests pass, the full suite passes, `alembic check` reports no drift, the evidence records the real plans, and no connection secret is committed.

## Completion checklist

- [ ] Each of the four access patterns has a named statement in `persistence.py`.
- [ ] The four indexes exist in the declared tables and in a migration, and `alembic check` reports no drift.
- [ ] The retention index is partial and covers only live Conversations.
- [ ] Every foreign key is the leading column of an index, and the set holds no index no query asks for.
- [ ] A query-plan test asserts each plan reads through its intended index, and fails when the index is missing.
- [ ] Repository checks pass and the evidence records the plans and the exact results.

Share your implementation or any error you hit and I will review it.
