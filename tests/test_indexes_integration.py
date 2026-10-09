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
