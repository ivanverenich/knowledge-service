"""Statements over Sources, their synchronization runs, their fence, and schedule."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.jobs import JobKind
from knowledge_service.persistence.jobs import open_job
from knowledge_service.persistence.schema import jobs, sources, synchronization_runs


@dataclass(frozen=True, slots=True)
class SourceRow:
    """A stored Source as a synchronization Job needs it, as plain values."""

    source_id: SourceId
    kind: str
    location: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # PostgreSQL arrays may hold NULL elements; the caller decides.
    default_principals: tuple[str | None, ...]


async def load_source_row(
    connection: AsyncConnection, *, source_id: SourceId
) -> SourceRow | None:
    """Read one stored Source, if it exists."""
    row = (
        await connection.execute(
            sa.select(
                sources.c.kind,
                sources.c.location,
                sources.c.credentials_reference,
                sources.c.enabled,
                sources.c.default_public,
                sources.c.default_principals,
            ).where(sources.c.source_id == source_id.value)
        )
    ).one_or_none()
    if row is None:
        return None
    return SourceRow(
        source_id=source_id,
        kind=row.kind,
        location=row.location,
        credentials_reference=row.credentials_reference,
        enabled=row.enabled,
        default_public=row.default_public,
        default_principals=tuple(row.default_principals),
    )


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A stored Source as an operator configures it, as plain values."""

    source_id: SourceId
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # PostgreSQL arrays may hold NULL elements; such a Source needs repair.
    default_principals: tuple[str | None, ...]
    created_at: datetime
    updated_at: datetime


_RECORD_COLUMNS = (
    sources.c.source_id,
    sources.c.kind,
    sources.c.location,
    sources.c.display_name,
    sources.c.credentials_reference,
    sources.c.enabled,
    sources.c.default_public,
    sources.c.default_principals,
    sources.c.created_at,
    sources.c.updated_at,
)


def _source_record(row: sa.RowMapping) -> SourceRecord:
    return SourceRecord(
        source_id=SourceId(cast(UUID, row["source_id"])),
        kind=row["kind"],
        location=row["location"],
        display_name=row["display_name"],
        credentials_reference=row["credentials_reference"],
        enabled=row["enabled"],
        default_public=row["default_public"],
        default_principals=tuple(row["default_principals"]),
        # The session's time zone decides the offset psycopg returns.
        created_at=cast(datetime, row["created_at"]).astimezone(UTC),
        updated_at=cast(datetime, row["updated_at"]).astimezone(UTC),
    )


async def insert_source_record(
    connection: AsyncConnection, record: SourceRecord
) -> bool:
    """Store a new Source; False, storing nothing, when its kind and location exist.

    The unique constraint decides, so two registrations of the same folder
    at the same moment store one Source, never two.
    """
    inserted = await connection.scalar(
        postgresql.insert(sources)
        .values(
            source_id=record.source_id.value,
            kind=record.kind,
            location=record.location,
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        .on_conflict_do_nothing(constraint="uq_sources_kind_location")
        .returning(sources.c.source_id)
    )
    return inserted is not None


async def load_source_record(
    connection: AsyncConnection, *, source_id: SourceId, for_update: bool = False
) -> SourceRecord | None:
    """One stored Source; with `for_update`, locked until the transaction ends."""
    statement = sa.select(*_RECORD_COLUMNS).where(
        sources.c.source_id == source_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else _source_record(row)


async def save_source_record(connection: AsyncConnection, record: SourceRecord) -> None:
    """Store what an operator may change of an existing Source."""
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == record.source_id.value)
        .values(
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            updated_at=record.updated_at,
        )
    )


async def load_source_page(
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, those after that one."""
    statement = sa.select(*_RECORD_COLUMNS)
    if after is not None:
        created_at, source_id = after
        statement = statement.where(
            sa.tuple_(sources.c.created_at, sources.c.source_id)
            < sa.tuple_(sa.literal(created_at), sa.literal(source_id.value))
        )
    statement = statement.order_by(
        sources.c.created_at.desc(), sources.c.source_id.desc()
    ).limit(limit)
    rows = (await connection.execute(statement)).mappings().all()
    return tuple(_source_record(row) for row in rows)


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at


async def load_due_source_ids(
    connection: AsyncConnection, *, now: datetime, interval: timedelta, limit: int
) -> tuple[SourceId, ...]:
    """Enabled Sources with no open synchronization Job whose last one is old.

    A Source is due when its newest synchronization Job, counted from when it
    finished, or was queued if it never finished, is at least `interval`
    before `now`, or when it never had one. However long ago that was, it is
    due once: a missed schedule is not caught up run by run. A time later
    than `now`, from a clock that ran ahead, is ignored: a finish time in the
    future counts from when the Job was queued instead, and a Job queued in
    the future does not count. The longest-waiting Sources come first, so
    when more are due than `limit`, the rest come next pass.
    """
    of_source = sa.and_(
        jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
        jobs.c.target_id == sources.c.source_id,
    )
    # greatest() skips the NULL a future finish time becomes here.
    activity = sa.func.greatest(
        jobs.c.queued_at,
        sa.case((jobs.c.finished_at <= now, jobs.c.finished_at), else_=None),
    )
    candidates = (
        sa.select(
            sources.c.source_id,
            sa.select(sa.func.max(activity))
            .where(of_source, jobs.c.queued_at <= now)
            .scalar_subquery()
            .label("last_activity"),
        )
        .where(sources.c.enabled.is_(True), ~sa.exists().where(of_source, open_job()))
        # Materialized, so each Source's last activity is computed once; a
        # plain subquery is inlined and the planner repeats it per use.
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    statement = (
        sa.select(candidates.c.source_id)
        .where(
            sa.or_(
                candidates.c.last_activity.is_(None),
                candidates.c.last_activity <= now - interval,
            )
        )
        .order_by(
            candidates.c.last_activity.asc().nulls_first(), candidates.c.source_id
        )
        .limit(limit)
    )
    rows = await connection.scalars(statement)
    return tuple(SourceId(source_id) for source_id in rows)


async def lock_enabled_sources(
    connection: AsyncConnection, *, source_ids: Collection[SourceId]
) -> frozenset[SourceId]:
    """Those of `source_ids` that still exist and are enabled, share-locked.

    Disabling or deleting one of them now waits for this transaction, so a
    Job queued in it is queued before the change, never after.
    """
    if not source_ids:
        return frozenset()
    rows = await connection.scalars(
        sa.select(sources.c.source_id)
        .where(
            sources.c.source_id.in_([source_id.value for source_id in source_ids]),
            sources.c.enabled.is_(True),
        )
        .order_by(sources.c.source_id)
        .with_for_update(read=True)
    )
    return frozenset(SourceId(source_id) for source_id in rows)
