"""The gate's protected operation over HTTP and real PostgreSQL.

`DELETE /v1/conversations/{conversation_id}` runs as the token's User, with
the real `DatabaseConversationStore`. It must fail closed at both seams:

- **Transport.** A caller without the role changes nothing; a stranger's
  delete answers exactly as a delete of a missing ID does.
- **Storage.** The owner is in the SQL, so another User's Conversation is
  never touched; the deletion, its purge Job, and its audit event commit
  together, so a deletion that cannot be audited does not happen.

People come from token claims through `authorization_scenarios`, as in the
M07-T15 suite.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import Person
from knowledge_service.answering.conversation_store import DatabaseConversationStore
from knowledge_service.app import create_app
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    Transcript,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ConversationId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.persistence.conversations import (
    add_turn,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import audit_events, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
LIMITS = ConversationLimits(retention=timedelta(days=30), max_messages=10)
REQUEST_ID = UUID("0190a3a0-0000-7000-8000-0000000000bb")
MARKER = "GATE-MARKER-8817"
ALICE = Person("alice", ("/engineering",))
BOB = Person("bob", ("/engineering",))
MANAGER = Person("morgan", ("/engineering",))
CALLERS = {
    "Bearer alice": Caller(
        reader=ALICE.context(), roles=frozenset({ApplicationRole.USER})
    ),
    "Bearer bob": Caller(reader=BOB.context(), roles=frozenset({ApplicationRole.USER})),
    "Bearer morgan": Caller(
        reader=MANAGER.context(), roles=frozenset({ApplicationRole.CONTENT_MANAGER})
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def alices_conversation(runtime: DatabaseRuntime) -> ConversationId:
    """A Conversation of alice's, with one answered turn holding `MARKER`."""
    owner = ALICE.context().user_id
    async with runtime.transaction() as connection:
        conversation = await start_conversation(
            connection, owner_id=owner, now=NOW, limits=LIMITS
        )
        await add_turn(
            connection,
            owner_id=owner,
            conversation_id=conversation.conversation_id,
            question=f"What does {MARKER} say?",
            reply=f"It says {MARKER}.",
            outcome=MessageOutcome.ABSTAINED,
            now=NOW,
            limits=LIMITS,
        )
    return conversation.conversation_id


async def delete(
    runtime: DatabaseRuntime, person: Person, conversation_id: ConversationId
) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        conversations=DatabaseConversationStore(runtime, clock=lambda: LATER),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.delete(
            f"/v1/conversations/{conversation_id.serialize()}",
            headers={
                "Authorization": f"Bearer {person.name}",
                "X-Request-ID": str(REQUEST_ID),
            },
        )


async def alices_view(
    runtime: DatabaseRuntime, conversation_id: ConversationId
) -> Transcript | None:
    async with runtime.transaction() as connection:
        return await read_conversation(
            connection,
            owner_id=ALICE.context().user_id,
            conversation_id=conversation_id,
            now=LATER,
        )


async def purge_jobs(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(jobs.c.kind == "purge_conversation")
        )
    return int(count or 0)


async def audit_rows(runtime: DatabaseRuntime) -> list[dict[str, Any]]:
    async with runtime.transaction() as connection:
        records = (
            (await connection.execute(sa.select(audit_events).order_by("occurred_at")))
            .mappings()
            .all()
        )
    return [dict(record) for record in records]


def logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        json.dumps(vars(record), default=str, ensure_ascii=False)
        for record in caplog.records
    )


def same_response(first: httpx.Response, second: httpx.Response) -> bool:
    """Status, body, and headers, apart from the ones that vary by time."""

    def shown(response: httpx.Response) -> tuple[Any, ...]:
        headers = sorted(
            (name, value) for name, value in response.headers.items() if name != "date"
        )
        return response.status_code, response.content, headers

    return shown(first) == shown(second)


# --- Success: the owner deletes, and it is audited with the change -----------------


async def test_the_owner_deletes_and_the_deletion_is_audited_with_it(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id = await alices_conversation(runtime)

    response = await delete(runtime, ALICE, conversation_id)

    assert response.status_code == 204
    assert await alices_view(runtime, conversation_id) is None
    assert await purge_jobs(runtime) == 1
    [event] = await audit_rows(runtime)
    assert event["action"] == "conversation.deleted"
    assert event["target_id"] == conversation_id.value
    assert event["actor_id"] == ALICE.context().user_id.value
    assert (event["decision"], event["outcome"]) == ("allowed", "succeeded")
    assert event["request_id"] == REQUEST_ID


async def test_a_second_delete_reads_the_same_and_queues_nothing_more(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id = await alices_conversation(runtime)

    first = await delete(runtime, ALICE, conversation_id)
    second = await delete(runtime, ALICE, conversation_id)

    assert same_response(first, second)
    assert await purge_jobs(runtime) == 1
    decisions = [row["decision"] for row in await audit_rows(runtime)]
    assert decisions == ["allowed", "denied"]


# --- Highest-risk edge: a stranger's delete ------------------------------------------


async def test_a_strangers_delete_reads_like_a_missing_one_and_changes_nothing(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    conversation_id = await alices_conversation(runtime)

    strangers = await delete(runtime, BOB, conversation_id)
    missing = await delete(runtime, BOB, ConversationId.new())

    assert strangers.status_code == 204
    assert same_response(strangers, missing)
    kept = await alices_view(runtime, conversation_id)
    assert kept is not None
    assert len(kept.messages) == 2
    assert await purge_jobs(runtime) == 0
    rows = await audit_rows(runtime)
    assert [(row["decision"], row["outcome"]) for row in rows] == [
        ("denied", "failed"),
        ("denied", "failed"),
    ]
    assert {row["actor_id"] for row in rows} == {BOB.context().user_id.value}
    # The attempt is recorded, never what the Conversation holds.
    assert MARKER not in json.dumps(rows, default=str)
    assert MARKER not in logged(caplog)


# --- Typed denial: no role, no change, no record ------------------------------------


async def test_a_caller_without_the_role_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id = await alices_conversation(runtime)

    response = await delete(runtime, MANAGER, conversation_id)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert await alices_view(runtime, conversation_id) is not None
    assert await purge_jobs(runtime) == 0
    assert await audit_rows(runtime) == []


# --- Storage fails closed: no audit, no deletion -------------------------------------


async def test_a_deletion_that_cannot_be_audited_does_not_happen(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id = await alices_conversation(runtime)
    # A real trigger, so it is PostgreSQL that rolls the deletion back.
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "CREATE FUNCTION refuse_audit() RETURNS trigger LANGUAGE plpgsql AS"
                " $$ BEGIN RAISE EXCEPTION 'audit refused'; END $$"
            )
        )
        await connection.execute(
            sa.text(
                "CREATE TRIGGER refuse_audit BEFORE INSERT ON audit_events"
                " FOR EACH ROW EXECUTE FUNCTION refuse_audit()"
            )
        )
    try:
        response = await delete(runtime, ALICE, conversation_id)
    finally:
        # The schema outlives this test: later tests reuse the database.
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.text("DROP TRIGGER IF EXISTS refuse_audit ON audit_events")
            )
            await connection.execute(sa.text("DROP FUNCTION IF EXISTS refuse_audit()"))

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert await alices_view(runtime, conversation_id) is not None
    assert await purge_jobs(runtime) == 0
