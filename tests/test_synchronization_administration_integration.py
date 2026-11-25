"""Synchronization administration on real PostgreSQL.

A start is idempotent while the Source has an open Job, even when several
arrive at once; once the Job ends, in any final state, the next start queues
a new one. A Source's history holds its own synchronization Jobs only. A
start commits before it publishes, so a queue that refuses the message
loses nothing.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import Person
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.job_decisions import DatabaseJobDecisions, retry_job
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.job_reporting import DatabaseJobReports
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.job_attempts import begin_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.source_administration import (
    SourceDefinition,
    SourceDisabled,
    SourceNotFound,
    register_source,
    set_source_enabled,
)
from knowledge_service.synchronization_administration import (
    DatabaseSynchronizations,
    start_synchronization,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
MARKER = "SECRET-MARKER-7f3a"
DANA = Person("dana", ())
OPERATOR = DANA.context().user_id
CALLERS = {
    "Bearer dana": Caller(
        reader=DANA.context(),
        roles=frozenset(
            {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
        ),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@dataclass
class Publisher:
    """Records each Job it sends; refuses all of them when told to."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({JobKind.SYNCHRONIZE_SOURCE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed(f"the queue is down near {MARKER}")
        self.sent.append(job.job_id)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def folder(runtime: DatabaseRuntime, name: str = "handbook") -> SourceId:
    record = await register_source(
        runtime,
        definition=SourceDefinition.of(
            kind="local_directory", location=f"/srv/{name}", display_name=name
        ),
        operator=OPERATOR,
        now=NOW,
    )
    return record.source_id


async def start(runtime: DatabaseRuntime, source_id: SourceId) -> Any:
    return await start_synchronization(
        runtime, source_id=source_id, operator=OPERATOR, now=NOW
    )


async def store(runtime: DatabaseRuntime, job: Job) -> None:
    async with runtime.transaction() as connection:
        await save_job(connection, job=job)


async def queued_events(runtime: DatabaseRuntime, job_id: JobId) -> list[Any]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(connection, target_id=job_id.value, limit=10)
    return [(e.action, e.actor_id, dict(e.metadata)) for e in events]


def app(runtime: DatabaseRuntime, publisher: Publisher | None = None) -> Any:
    return create_app(
        authenticator=Tokens(),
        synchronizations=DatabaseSynchronizations(
            runtime, publisher=publisher, clock=lambda: NOW
        ),
        job_reports=DatabaseJobReports(runtime),
        job_decisions=DatabaseJobDecisions(runtime, clock=lambda: NOW),
    )


async def over_http(
    runtime: DatabaseRuntime,
    method: str,
    path: str,
    publisher: Publisher | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(runtime, publisher)),
        base_url="http://t",
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer dana"}
        )


def path(source_id: SourceId) -> str:
    return f"/v1/sources/{source_id.value}/synchronizations"


async def test_a_start_queues_one_job_and_records_who_asked(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    response = await over_http(runtime, "POST", path(source_id))
    job_id = JobId.parse(response.json()["job_id"])

    assert response.status_code == 202
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=job_id)
    assert stored is not None
    assert (stored.kind, stored.target_id, stored.status) == (
        JobKind.SYNCHRONIZE_SOURCE,
        source_id.value,
        JobStatus.QUEUED,
    )
    assert await queued_events(runtime, job_id) == [
        (AuditAction.JOB_QUEUED, OPERATOR, {"kind": "synchronize_source"})
    ]


async def test_starts_at_once_queue_one_job_and_one_event(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    responses = await asyncio.gather(
        *(over_http(runtime, "POST", path(source_id)) for _ in range(4))
    )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 202]
    assert len({r.json()["job_id"] for r in responses}) == 1
    assert len({r.headers["location"] for r in responses}) == 1
    job_id = JobId.parse(responses[0].json()["job_id"])
    assert len(await queued_events(runtime, job_id)) == 1


async def test_a_repeated_start_over_http_answers_200_with_the_same_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    first = await over_http(runtime, "POST", path(source_id))
    second = await over_http(runtime, "POST", path(source_id))

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["job_id"] == first.json()["job_id"]


@pytest.mark.parametrize(
    "ending",
    ["running", "retry planned"],
)
async def test_an_open_job_is_returned_whatever_its_state(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    if ending == "running":
        await store(runtime, running)
    else:
        await store(
            runtime,
            running.fail(
                error_code="source_unavailable",
                finished_at=NOW + timedelta(seconds=2),
                retry_at=NOW + timedelta(minutes=1),
            ),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert (again.status_code, again.json()["job_id"]) == (200, str(job.job_id.value))
    assert again.json()["status"] == ("running" if ending == "running" else "failed")


@pytest.mark.parametrize("ending", ["succeeded", "failed for good", "cancelled"])
async def test_after_a_final_state_a_start_queues_a_new_job(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    later = NOW + timedelta(seconds=2)
    if ending == "cancelled":
        await store(runtime, job.cancel(finished_at=later))
    else:
        running = job.start(started_at=NOW + timedelta(seconds=1))
        await store(
            runtime,
            running.succeed(finished_at=later)
            if ending == "succeeded"
            else running.fail(error_code="source_gone", finished_at=later),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert again.status_code == 202
    assert again.json()["job_id"] != str(job.job_id.value)


async def test_retrying_a_failed_job_while_a_new_one_is_open_is_a_409(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    await store(
        runtime,
        job.start(started_at=NOW + timedelta(seconds=1)).fail(
            error_code="source_gone", finished_at=NOW + timedelta(seconds=2)
        ),
    )
    await start(runtime, source_id)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "job_already_open")


async def test_a_disabled_or_missing_source_queues_nothing(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    disabled = await over_http(runtime, "POST", path(source_id))
    missing = await over_http(runtime, "POST", path(SourceId.new()))

    assert (disabled.status_code, disabled.json()["code"]) == (409, "source_disabled")
    assert (missing.status_code, missing.json()["code"]) == (404, "source_not_found")
    with pytest.raises(SourceDisabled):
        await start(runtime, source_id)
    with pytest.raises(SourceNotFound):
        await start(runtime, SourceId.new())

    async with runtime.transaction() as connection:
        count = await connection.scalar(sa.select(sa.func.count()).select_from(jobs))
    assert count == 0


async def test_a_start_publishes_once_and_a_refused_send_loses_nothing(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    published, refused = await folder(runtime, "a"), await folder(runtime, "b")
    publisher, down = Publisher(), Publisher(refuse=True)

    first = await over_http(runtime, "POST", path(published), publisher)
    await over_http(runtime, "POST", path(published), publisher)
    stored = await over_http(runtime, "POST", path(refused), down)

    assert publisher.sent == [JobId.parse(first.json()["job_id"])]
    assert stored.status_code == 202
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=JobId.parse(stored.json()["job_id"]))
        unpublished = await connection.scalar(
            sa.select(jobs.c.published_at).where(
                jobs.c.job_id == JobId.parse(stored.json()["job_id"]).value
            )
        )
    assert job is not None
    assert job.status is JobStatus.QUEUED
    assert unpublished is None
    assert MARKER not in caplog.text


async def test_a_history_holds_only_its_sources_synchronization_jobs(
    runtime: DatabaseRuntime,
) -> None:
    mine, other = await folder(runtime, "mine"), await folder(runtime, "other")
    own = (await start(runtime, mine)).report.job
    await start(runtime, other)
    # Another kind of Job aimed at the same ID is not a synchronization.
    async with runtime.transaction() as connection:
        await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=mine.value,
                queued_at=NOW,
            ),
        )

    page = (await over_http(runtime, "GET", path(mine))).json()

    assert [item["job_id"] for item in page["items"]] == [str(own.job_id.value)]
    assert page["next_cursor"] is None


async def test_a_history_of_a_missing_source_is_a_404(
    runtime: DatabaseRuntime,
) -> None:
    response = await over_http(runtime, "GET", path(SourceId.new()))

    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


async def test_pages_of_history_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    # Older Jobs get newer IDs here, so only an order by ID passes.
    async with runtime.transaction() as connection:
        for n in range(6):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=source_id.value,
                queued_at=NOW - timedelta(hours=1 + n // 2),
            )
            await enqueue_job(connection, job)
            started = job.start(started_at=job.queued_at + timedelta(seconds=1))
            await save_job(
                connection,
                job=started.succeed(finished_at=job.queued_at + timedelta(seconds=2)),
            )
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A start between pages is newer: it starts no later page.
            await start(runtime, source_id)
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.target_id == source_id.value, jobs.c.status == "succeeded")
            .order_by(jobs.c.job_id.desc())
        )
        expected = [str(row.job_id) for row in rows]
    assert seen == expected


async def test_a_history_item_shows_its_jobs_newest_attempt(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    first = job.start(started_at=NOW + timedelta(seconds=1))
    async with runtime.transaction() as connection:
        await save_job(connection, job=first)
        await begin_attempt(connection, job=first)
        failed = first.fail(
            error_code="source_unavailable",
            finished_at=NOW + timedelta(seconds=2),
            retry_at=NOW + timedelta(seconds=3),
        )
        await save_job(connection, job=failed)
        second = failed.retry(queued_at=NOW + timedelta(seconds=3)).start(
            started_at=NOW + timedelta(seconds=4)
        )
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    page = (await over_http(runtime, "GET", path(source_id))).json()

    assert page["items"][0]["latest_attempt"]["attempt"] == 2


async def failed_for_good(runtime: DatabaseRuntime, source_id: SourceId) -> Job:
    """A new synchronization Job of `source_id`, claimed and failed for good."""
    job = (await start(runtime, source_id)).report.job
    failed = job.start(started_at=NOW + timedelta(seconds=1)).fail(
        error_code="source_unavailable", finished_at=NOW + timedelta(seconds=2)
    )
    await store(runtime, failed)
    return failed


async def test_a_job_retried_between_pages_is_still_listed_once(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = [await failed_for_good(runtime, source_id) for _ in range(4)]
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A retry queues the oldest Job again, at a later time.
            await retry_job(
                runtime,
                job_id=failed[0].job_id,
                operator=OPERATOR,
                now=NOW + timedelta(hours=1),
            )
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert sorted(seen) == sorted(str(job.job_id.value) for job in failed)
    assert len(seen) == len(set(seen))


async def test_a_retry_queues_nothing_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = await failed_for_good(runtime, source_id)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{failed.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "source_disabled")
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=failed.job_id)
    assert stored is not None
    assert stored.status is JobStatus.FAILED


async def test_a_retry_that_does_not_fit_the_job_says_so_even_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    await store(runtime, running.succeed(finished_at=NOW + timedelta(seconds=2)))
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (
        409,
        "decision_not_allowed",
    )


async def test_a_disable_waits_for_a_start_that_holds_the_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    async with runtime.transaction() as connection:
        # What a start holds between its check and its commit.
        await lock_enabled_sources(connection, source_ids={source_id})
        disabling = asyncio.ensure_future(
            set_source_enabled(
                runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
            )
        )
        await asyncio.sleep(0.3)
        waited = not disabling.done()
    disabled = await asyncio.wait_for(disabling, timeout=5)

    assert waited
    assert disabled.enabled is False


async def test_a_history_page_reads_an_index_in_job_order(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        definition = await connection.scalar(
            sa.text(
                "SELECT indexdef FROM pg_indexes"
                " WHERE indexname = 'ix_jobs_synchronization_target'"
            )
        )

    assert "(target_id, job_id)" in str(definition)
    assert "synchronize_source" in str(definition)
