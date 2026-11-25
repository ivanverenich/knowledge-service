"""Page sizes and cursors: only what the API wrote comes back in.

A cursor travels through the client, so it is untrusted input. Each test
hands the decoder something `encode_cursor` would never write and expects
`InvalidPage`, never another exception or a position.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import JobDecisions, create_app
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import decision_cursor, decision_position
from knowledge_service.jobs import Job, JobKind
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    InvalidPage,
    decode_cursor,
    encode_cursor,
    page_size,
)
from knowledge_service.roles import ApplicationRole

KEYS = frozenset({"finished_at", "job_id"})
LIST = "job-decisions"
FINISHED_AT = datetime(2026, 11, 24, 9, 30, 15, 123456, tzinfo=UTC)


def raw_cursor(document: Any) -> str:
    """A cursor holding any JSON, encoded the way `encode_cursor` encodes."""
    text = json.dumps(document)
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def raw_text(text: str) -> str:
    """A cursor holding exactly `text`."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def failed_job(finished_at: datetime = FINISHED_AT) -> Job:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=finished_at - timedelta(minutes=2),
    )
    started = queued.start(started_at=finished_at - timedelta(minutes=1))
    return started.fail(error_code="database_rejected", finished_at=finished_at)


@pytest.mark.parametrize(
    ("raw", "size"), [(None, DEFAULT_PAGE_SIZE), ("1", 1), ("100", 100)]
)
def test_a_page_size_is_a_whole_number_from_1_to_100(
    raw: str | None, size: int
) -> None:
    assert page_size(raw) == size


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "00",
        "007",
        "101",
        "-1",
        "+5",
        " 5",
        "5 ",
        "5.0",
        "1e2",
        "0x10",
        "\u0665",
        "1000",
    ],
)
def test_any_other_page_size_is_refused(raw: str) -> None:
    with pytest.raises(InvalidPage):
        page_size(raw)


def test_a_cursor_gives_back_the_position_it_was_made_from() -> None:
    position = {"finished_at": "2026-11-24T09:30:15+00:00", "job_id": str(uuid4())}

    assert (
        decode_cursor(encode_cursor(LIST, position), listing=LIST, keys=KEYS)
        == position
    )


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "a" * (MAX_CURSOR_CHARACTERS + 1),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]) + "=",
        "not+base64/",
        "%%%",
        "A",
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        raw_cursor("just text"),
        raw_cursor([2, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([True, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1.0, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y", "more": "z"}]),
        raw_cursor([1, LIST, {"finished_at": 5, "job_id": "y"}]),
        raw_cursor([1, LIST, ["finished_at", "job_id"]]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}, "extra"]),
        raw_cursor([[[[[[[[[[1]]]]]]]]]]),
        raw_cursor([1, "sources", {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"job_id": "y", "finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]),
        '[1,"job-decisions",{"finished_at":"x","finished_at":"x","job_id":"y"}]',
    ],
)
def test_a_cursor_the_api_did_not_write_is_refused(cursor: str) -> None:
    if cursor.startswith("["):
        cursor = raw_text(cursor)
    with pytest.raises(InvalidPage):
        decode_cursor(cursor, listing=LIST, keys=KEYS)


@given(st.text(max_size=600))
def test_any_text_as_a_cursor_is_a_position_or_invalid_page(cursor: str) -> None:
    try:
        position = decode_cursor(cursor, listing=LIST, keys=KEYS)
    except InvalidPage:
        return
    assert set(position) == KEYS


@given(st.binary(max_size=400))
def test_any_bytes_behind_a_cursor_are_a_position_or_invalid_page(raw: bytes) -> None:
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    try:
        decision_position(cursor)
    except InvalidPage:
        return


def test_spare_base64_bits_do_not_make_a_second_spelling() -> None:
    cursor = encode_cursor(LIST, {"finished_at": "x", "job_id": "y"})
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    others = [cursor[:-1] + letter for letter in alphabet if letter != cursor[-1]]

    accepted: list[str] = []
    for other in others:
        try:
            decode_cursor(other, listing=LIST, keys=KEYS)
        except InvalidPage:
            continue
        accepted.append(other)
    assert accepted == []


def test_a_decision_cursor_starts_after_its_job() -> None:
    job = failed_job()

    assert decision_position(decision_cursor(job)) == (FINISHED_AT, job.job_id)


@pytest.mark.parametrize(
    "position",
    [
        {"finished_at": "2026-11-24T10:30:15.123456+01:00"},
        {"finished_at": "2026-11-24T09:30:15.123456"},
        {"finished_at": "2026-11-24T09:30:15.123456Z"},
        {"finished_at": "2026-11-24 09:30:15.123456+00:00"},
        {"finished_at": "yesterday"},
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "000000000000000000000000000000001"},
        {"job_id": "not an id"},
    ],
)
def test_a_decision_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    valid = {
        "finished_at": FINISHED_AT.isoformat(),
        "job_id": "00000000-0000-0000-0000-00000000000a",
    }

    with pytest.raises(InvalidPage):
        decision_position(encode_cursor("job-decisions", {**valid, **position}))


# The decision list over HTTP, with a fake store.


class Decisions:
    """Holds failed Jobs newest first, and records what each list asked for."""

    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs
        self.calls: list[dict[str, Any]] = []

    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        self.calls.append({"limit": limit, "after": after})
        start = 0
        if after is not None:
            keys = [(job.finished_at, job.job_id.value) for job in self.jobs]
            start = next(
                (i for i, key in enumerate(keys) if key < (after[0], after[1].value)),
                len(keys),
            )
        return tuple(self.jobs[start : start + limit])


class Administrator:
    """Gives every request an administrator's caller."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=READER, roles=frozenset({ApplicationRole.ADMINISTRATOR}))


async def list_decisions(decisions: Decisions, query: str) -> httpx.Response:
    app = create_app(
        authenticator=Administrator(), job_decisions=cast(JobDecisions, decisions)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/job-decisions{query}", headers={"Authorization": "Bearer t"}
        )


async def test_pages_of_decisions_follow_each_other_to_the_end() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n // 2)) for n in range(5)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (await list_decisions(decisions, query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [call["limit"] for call in decisions.calls] == [3, 3, 3]


async def test_a_full_last_page_says_no_page_follows() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n)) for n in range(4)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)

    first = (await list_decisions(decisions, "?limit=2")).json()
    cursor = first["next_cursor"]
    second = (await list_decisions(decisions, f"?limit=2&cursor={cursor}")).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


async def test_an_empty_list_has_no_next_page() -> None:
    page = (await list_decisions(Decisions([]), "")).json()

    assert page == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=101",
        "?limit=x",
        "?limit=1&limit=2",
        "?cursor=%%",
        "?cursor=a&cursor=b",
        "?Limit=5",
        "?offset=10",
    ],
)
async def test_a_bad_limit_or_cursor_is_refused_before_the_store_is_asked(
    query: str,
) -> None:
    decisions = Decisions([failed_job()])

    response = await list_decisions(decisions, query)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert decisions.calls == []


@pytest.mark.parametrize("query", ["?limit=0", "?cursor=%%", "?Limit=5"])
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_limit_or_cursor_is_read(
    query: str, headers: dict[str, str], status: int
) -> None:
    decisions = Decisions([failed_job()])
    app = create_app(authenticator=TRUSTED, job_decisions=cast(JobDecisions, decisions))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.get(f"/v1/job-decisions{query}", headers=headers)

    assert response.status_code == status
    assert decisions.calls == []


async def test_a_cursor_for_one_job_id_is_read_as_that_id() -> None:
    job = failed_job()
    decisions = Decisions([job])

    await list_decisions(decisions, f"?cursor={decision_cursor(job)}")

    assert decisions.calls[0]["after"] == (FINISHED_AT, job.job_id)
    assert isinstance(decisions.calls[0]["after"][1].value, UUID)
