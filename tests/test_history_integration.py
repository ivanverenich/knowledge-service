"""History in PostgreSQL: stored references, revocation, deletion, ownership."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.history import History, load_history
from knowledge_service.conversations import (
    ConversationFull,
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.conversations import (
    add_turn,
    delete_conversation,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000057")
OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e4")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e5")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f2")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
LIMITS = ConversationLimits()
MARKER = "PLANTED-MARKER-7719"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    text: str,
    *,
    readers: tuple[Subject, ...] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> MessageReference:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=name, modified_at=observed_at, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=observed_at
    )
    document = reconciled.document
    async with runtime.transaction() as connection:
        chunk_id = await connection.scalar(
            sa.select(chunks.c.chunk_id).where(
                chunks.c.document_id == document.document_id.value
            )
        )
    return MessageReference(
        chunk_id=ChunkId(cast(UUID, chunk_id)),
        document_id=DocumentId(document.document_id.value),
        content_version=ContentVersion(document.content_version.number),
    )


async def conversation(
    runtime: DatabaseRuntime,
    *turns: tuple[str, str, MessageOutcome, tuple[MessageReference, ...]],
    owner: UserId = READER,
) -> ConversationId:
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=owner, now=OBSERVED_AT, limits=LIMITS
        )
        for question, answer, outcome, references in turns:
            await add_turn(
                connection,
                owner_id=owner,
                conversation_id=started.conversation_id,
                question=question,
                reply=answer,
                outcome=outcome,
                references=references,
                now=OBSERVED_AT,
                limits=LIMITS,
            )
    return started.conversation_id


async def history(
    runtime: DatabaseRuntime,
    conversation_id: ConversationId,
    context: AuthorizationContext = CONTEXT,
) -> History | None:
    async with runtime.transaction() as connection:
        return await load_history(
            connection,
            context,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )


async def leave_and_carry(
    runtime: DatabaseRuntime,
) -> tuple[ConversationId, MessageReference, MessageReference]:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    carry = await publish(runtime, "carry.txt", "Unused leave carries over 5 days.")
    conversation_id = await conversation(
        runtime,
        ("How many days?", f"24 days. {MARKER}", MessageOutcome.ANSWERED, (leave,)),
        ("Do they carry over?", "Up to 5 days.", MessageOutcome.ANSWERED, (carry,)),
        ("And next year?", "I could not answer.", MessageOutcome.FAILED, ()),
    )
    return conversation_id, leave, carry


async def test_answers_the_reader_may_still_see_are_kept_with_their_references(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    conversation_id, leave, carry = await leave_and_carry(runtime)

    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.answer for t in built.turns] == [
        f"24 days. {MARKER}",
        "Up to 5 days.",
        None,
    ]
    assert [t.references for t in built.turns] == [(leave,), (carry,), ()]
    assert built.withheld == 1
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)


async def test_stored_outcomes_and_references_read_back_as_written(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id, leave, _ = await leave_and_carry(runtime)

    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert [m.outcome for m in transcript.messages] == [
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.FAILED,
    ]
    assert transcript.messages[1].references == (leave,)


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 24 days.",
        readers=(Subject.user(OTHER),),
        observed_at=LATER,
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 26 days.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone],
    ids=["access-revoked", "new-version", "tombstoned"],
)
async def test_an_answer_whose_document_changed_is_withheld(
    runtime: DatabaseRuntime,
    change: Callable[[DatabaseRuntime], Awaitable[None]],
) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)

    await change(runtime)
    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.question for t in built.turns] == [
        "How many days?",
        "Do they carry over?",
        "And next year?",
    ]
    # The second Answer was written with the first in its History.
    assert [t.answer for t in built.turns] == [None, None, None]
    assert [t.references for t in built.turns] == [(), (), ()]
    assert built.withheld == 3


async def test_a_deleted_conversation_has_no_history(runtime: DatabaseRuntime) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)
    async with runtime.transaction() as connection:
        await delete_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert await history(runtime, conversation_id) is None


async def test_another_users_conversation_has_no_history_for_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    foreign = await conversation(
        runtime,
        ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,)),
        owner=OTHER,
    )

    assert await history(runtime, foreign) is None
    other = AuthorizationContext(user_id=OTHER, group_ids=frozenset({TEAM}))
    assert await history(runtime, foreign, other) is not None


# --- Review regressions -------------------------------------------------------------


async def test_turns_stored_at_once_never_interleave(runtime: DatabaseRuntime) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(runtime)

    async def ask(question: str, reply: str) -> None:
        async with runtime.transaction() as connection:
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question=question,
                reply=reply,
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=LIMITS,
            )

    await asyncio.gather(*(ask(f"Q{n}?", f"A{n}.") for n in range(6)))
    built = await history(runtime, conversation_id)

    assert built is not None
    assert len(built.turns) == 6
    assert all(t.answer == f"A{t.question[1:-1]}." for t in built.turns)


async def test_a_turn_that_does_not_fit_leaves_no_half_behind(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(
        runtime, ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,))
    )
    three = ConversationLimits(max_messages=3)

    async with runtime.transaction() as connection:
        with pytest.raises(ConversationFull):
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question="And next year?",
                reply="26 days.",
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=three,
            )
    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert len(transcript.messages) == 2
