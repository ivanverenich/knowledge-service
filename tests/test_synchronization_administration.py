"""Synchronization administration over HTTP, with fake stores.

A start answers 202 when it queued a Job and 200 when the Source already had
an open one, and both name the Job. The history is a Source's Jobs a page
at a time. Every refusal of the five operations, start, status, history,
retry, and cancel, has one fixed status, code, and message: a client may
branch on them, so a test pins each one.
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import (
    JobDecisions,
    JobReports,
    Synchronizations,
    create_app,
)
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import JobIntentAlreadyOpen, JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.pagination import InvalidPage, encode_cursor
from knowledge_service.roles import ApplicationRole
from knowledge_service.source_administration import SourceDisabled, SourceNotFound
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)

MARKER = "SECRET-MARKER-7f3a"
QUEUED_AT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.new()


def queued(queued_at: datetime = QUEUED_AT) -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=queued_at,
    )


class Synchronizing:
    """Holds a Source's Jobs newest first; records each request; can refuse."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs = jobs or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.open: Job | None = None

    async def start(self, **request: Any) -> SynchronizationStart:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.open is not None:
            return SynchronizationStart(JobReport(self.open, None), queued=False)
        self.open = queued()
        return SynchronizationStart(JobReport(self.open, None), queued=True)

    async def history(
        self,
        *,
        source_id: SourceId,
        limit: int,
        after: JobId | None = None,
    ) -> tuple[JobReport, ...]:
        self.calls.append({"source_id": source_id, "limit": limit, "after": after})
        if self.error is not None:
            raise self.error
        start = 0
        if after is not None:
            start = next(
                (i for i, j in enumerate(self.jobs) if j.job_id.value < after.value),
                len(self.jobs),
            )
        return tuple(JobReport(job, None) for job in self.jobs[start : start + limit])


class Refusing:
    """A Job store whose every answer is the error a test sets."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def load(self, **_request: Any) -> None:
        if not isinstance(self.error, JobNotFound):
            raise self.error

    async def retry(self, **_request: Any) -> Job:
        raise self.error

    async def cancel(self, **_request: Any) -> Job:
        raise self.error


class Staff:
    """Gives every request a caller with every role but `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(
            reader=READER,
            roles=frozenset(
                {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
            ),
        )


async def call(
    method: str,
    path: str,
    *,
    synchronizations: Synchronizing | None = None,
    jobs: Refusing | None = None,
) -> httpx.Response:
    app = create_app(
        authenticator=Staff(),
        synchronizations=cast(Synchronizations, synchronizations or Synchronizing()),
        job_reports=cast(JobReports, jobs or Refusing(JobNotFound("x"))),
        job_decisions=cast(JobDecisions, jobs or Refusing(JobNotFound("x"))),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(method, path, headers={"Authorization": "Bearer t"})


START = f"/v1/sources/{SOURCE_ID.value}/synchronizations"


async def test_a_start_that_queues_a_job_answers_202_with_where_it_lives() -> None:
    synchronizations = Synchronizing()

    response = await call("POST", START, synchronizations=synchronizations)

    body = response.json()
    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/jobs/{body['job_id']}"
    assert (body["status"], body["source_id"]) == ("queued", str(SOURCE_ID.value))
    assert synchronizations.calls[0]["operator"] == READER.user_id


async def test_a_second_start_answers_200_with_the_same_job() -> None:
    synchronizations = Synchronizing()

    first = await call("POST", START, synchronizations=synchronizations)
    second = await call("POST", START, synchronizations=synchronizations)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json() == first.json()
    assert second.headers["location"] == first.headers["location"]


@pytest.mark.parametrize(
    "source_id", ["not-an-id", "{00000000-0000-0000-0000-000000000001}", "0" * 32]
)
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_an_id_in_another_spelling_is_refused_before_the_store(
    method: str, source_id: str
) -> None:
    synchronizations = Synchronizing()

    response = await call(
        method,
        f"/v1/sources/{source_id}/synchronizations",
        synchronizations=synchronizations,
    )

    assert response.status_code == 422
    assert synchronizations.calls == []


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_a_store_failure_names_nothing_to_the_client_or_the_log(
    method: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    synchronizations = Synchronizing()
    synchronizations.error = RuntimeError(f"query failed for {MARKER}")

    response = await call(method, START, synchronizations=synchronizations)

    assert response.status_code == 500
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("POST", "/v1/sources/not-an-id/synchronizations"),
        ("GET", "/v1/sources/not-an-id/synchronizations"),
        ("GET", START + "?limit=0"),
        ("GET", START + "?cursor=%25%25"),
    ],
)
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_the_id_or_page_is_read(
    method: str, target: str, headers: dict[str, str], status: int
) -> None:
    synchronizations = Synchronizing()
    app = create_app(
        authenticator=TRUSTED,
        synchronizations=cast(Synchronizations, synchronizations),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.request(method, target, headers=headers)

    assert response.status_code == status
    assert synchronizations.calls == []


# The history.


def history(n: int) -> list[Job]:
    """`n` Jobs, the newest made first, as the store orders them."""
    return sorted(
        (queued() for _ in range(n)), key=lambda j: j.job_id.value, reverse=True
    )


async def test_pages_of_history_follow_each_other_to_the_end() -> None:
    jobs = history(5)
    synchronizations = Synchronizing(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (
            await call("GET", START + query, synchronizations=synchronizations)
        ).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [c["limit"] for c in synchronizations.calls] == [3, 3, 3]


async def test_a_full_last_page_of_history_says_no_page_follows() -> None:
    synchronizations = Synchronizing(history(4))

    first = (
        await call("GET", START + "?limit=2", synchronizations=synchronizations)
    ).json()
    cursor = first["next_cursor"]
    second = (
        await call(
            "GET", f"{START}?limit=2&cursor={cursor}", synchronizations=synchronizations
        )
    ).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=2",
        "?cursor=" + encode_cursor("sources", {"created_at": "x", "source_id": "y"}),
    ],
)
async def test_a_bad_page_request_is_refused_before_the_store(query: str) -> None:
    synchronizations = Synchronizing()

    response = await call("GET", START + query, synchronizations=synchronizations)

    assert response.status_code == 422
    assert synchronizations.calls == []


def test_a_history_cursor_starts_after_its_job() -> None:
    job = queued()

    assert history_position(history_cursor(job)) == job.job_id


@pytest.mark.parametrize(
    "position",
    [
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "not an id"},
        {"job_id": str(uuid4()), "queued_at": "2026-11-27T09:00:00+00:00"},
    ],
)
def test_a_history_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    with pytest.raises(InvalidPage):
        history_position(encode_cursor("synchronizations", position))


# Stable error responses: what a client may branch on, pinned.

JOB = f"/v1/jobs/{uuid4()}"
ERRORS = [
    (
        "start, missing Source",
        "POST",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "start, disabled Source",
        "POST",
        START,
        "synchronizations",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "history, missing Source",
        "GET",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "status, missing Job",
        "GET",
        JOB,
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such synchronization job"),
    ),
    (
        "retry, missing Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "retry, another Job open",
        "POST",
        JOB + "/retry",
        "jobs",
        JobIntentAlreadyOpen("x"),
        (409, "job_already_open", "Another job for this work is open"),
    ),
    (
        "retry, finished Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotRetryable("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
    (
        "retry, disabled Source",
        "POST",
        JOB + "/retry",
        "jobs",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "cancel, missing Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "cancel, running Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobStillRunning("x"),
        (409, "job_running", "The job is still running"),
    ),
    (
        "cancel, finished Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        IllegalJobTransition("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
]


@pytest.mark.parametrize(
    ("method", "path", "store", "error", "expected"),
    [case[1:] for case in ERRORS],
    ids=[case[0] for case in ERRORS],
)
async def test_each_refusal_has_one_fixed_status_code_and_message(
    method: str,
    path: str,
    store: str,
    error: Exception,
    expected: tuple[int, str, str],
) -> None:
    synchronizations = Synchronizing()
    if store == "synchronizations":
        synchronizations.error = error

    response = await call(
        method,
        path,
        synchronizations=synchronizations,
        jobs=Refusing(error) if store == "jobs" else None,
    )

    body = response.json()
    assert (response.status_code, body["code"], body["message"]) == expected
    assert set(body) == {"code", "message", "request_id"}
