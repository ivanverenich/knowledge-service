"""Owner-scoped Conversations in real PostgreSQL: ownership, order, deletion."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from knowledge_service.conversations import (
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, UserId
from knowledge_service.persistence.conversations import (
    add_message,
    delete_conversation,
    load_conversation,
    load_messages,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import jobs

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
OWNER = UserId.parse("00000000-0000-0000-0000-000000000060")
STRANGER = UserId.parse("00000000-0000-0000-0000-000000000061")
LIMITS = ConversationLimits(retention=timedelta(days=30), max_messages=10)
MARKER = "PLANTED-MARKER-7719"


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def start(engine: AsyncEngine, owner: UserId = OWNER) -> ConversationId:
    async with engine.begin() as connection:
        conversation = await start_conversation(
            connection, owner_id=owner, now=NOW, limits=LIMITS
        )
    return conversation.conversation_id


async def add(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    text: str = "How many days of leave do I get?",
    *,
    owner: UserId = OWNER,
    role: MessageRole = MessageRole.USER,
    now: datetime = NOW,
    limits: ConversationLimits = LIMITS,
) -> Message:
    async with engine.begin() as connection:
        return await add_message(
            connection,
            owner_id=owner,
            conversation_id=conversation_id,
            role=role,
            text=text,
            now=now,
            limits=limits,
        )


async def read(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    *,
    owner: UserId = OWNER,
    now: datetime = NOW,
) -> Transcript | None:
    async with engine.begin() as connection:
        return await read_conversation(
            connection, owner_id=owner, conversation_id=conversation_id, now=now
        )


async def delete(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    *,
    owner: UserId = OWNER,
    now: datetime = NOW,
) -> bool:
    async with engine.begin() as connection:
        return await delete_conversation(
            connection, owner_id=owner, conversation_id=conversation_id, now=now
        )


async def purge_jobs(engine: AsyncEngine, conversation_id: ConversationId) -> int:
    async with engine.begin() as connection:
        return int(
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(
                    jobs.c.kind == "purge_conversation",
                    jobs.c.target_id == conversation_id.value,
                )
            )
            or 0
        )


# --- Order ------------------------------------------------------------------------


async def test_messages_get_the_next_ordinal_and_read_back_in_order(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id, "How many days of leave do I get?")
    await add(
        engine,
        conversation_id,
        "You get 24 days.",
        role=MessageRole.ASSISTANT,
        now=NOW + timedelta(seconds=5),
    )
    await add(
        engine, conversation_id, "And carry over?", now=NOW + timedelta(minutes=1)
    )

    transcript = await read(engine, conversation_id, now=NOW + timedelta(minutes=2))

    assert transcript is not None
    assert [m.ordinal for m in transcript.messages] == [0, 1, 2]
    assert [m.role for m in transcript.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert transcript.conversation.last_message_at == NOW + timedelta(minutes=1)


async def test_concurrent_messages_take_turns_for_the_next_ordinal(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)

    added = await asyncio.gather(
        *(add(engine, conversation_id, f"question {n}") for n in range(8))
    )

    assert sorted(m.ordinal for m in added) == list(range(8))


async def test_concurrent_messages_never_pass_the_limit(engine: AsyncEngine) -> None:
    conversation_id = await start(engine)
    small = ConversationLimits(max_messages=3)

    results = await asyncio.gather(
        *(add(engine, conversation_id, f"q {n}", limits=small) for n in range(8)),
        return_exceptions=True,
    )

    assert sum(isinstance(r, Message) for r in results) == 3
    assert sum(isinstance(r, ConversationFull) for r in results) == 5


async def test_a_clock_running_backwards_never_moves_time_back(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    first = await add(engine, conversation_id, now=NOW + timedelta(minutes=5))

    second = await add(engine, conversation_id, now=NOW + timedelta(minutes=1))

    assert second.ordinal == 1
    assert second.created_at == first.created_at


# --- Ownership ----------------------------------------------------------------------


async def test_another_user_can_neither_read_add_nor_delete(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)

    assert await read(engine, conversation_id, owner=STRANGER) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id, owner=STRANGER)
    assert await delete(engine, conversation_id, owner=STRANGER) is False

    transcript = await read(engine, conversation_id)
    assert transcript is not None
    assert len(transcript.messages) == 1
    assert await purge_jobs(engine, conversation_id) == 0


async def test_missing_foreign_deleted_and_expired_look_the_same(
    engine: AsyncEngine,
) -> None:
    missing = ConversationId.new()
    foreign = await start(engine, STRANGER)
    deleted = await start(engine)
    await delete(engine, deleted)
    expired = await start(engine)
    # Each one is checked while only its own reason holds.
    cases = [
        (missing, NOW),
        (foreign, NOW),
        (deleted, NOW),
        (expired, NOW + LIMITS.retention),
    ]

    refusals: list[str] = []
    for conversation_id, now in cases:
        assert await read(engine, conversation_id, now=now) is None
        with pytest.raises(ConversationNotFound) as caught:
            await add(engine, conversation_id, now=now)
        refusals.append(str(caught.value))

    assert len(set(refusals)) == 1


# --- Deletion and retention ---------------------------------------------------------


async def test_deletion_is_immediate_and_queues_one_purge(engine: AsyncEngine) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)

    assert await delete(engine, conversation_id, now=NOW + timedelta(seconds=1))
    assert await read(engine, conversation_id) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id)
    assert await delete(engine, conversation_id) is False
    assert await purge_jobs(engine, conversation_id) == 1
    # Logical: the rows stay for the purge Job to remove.
    async with engine.begin() as connection:
        assert (
            len(await load_messages(connection, conversation_id=conversation_id)) == 1
        )


async def test_a_delete_racing_a_message_leaves_no_message_after_it(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)

    results = await asyncio.gather(
        *(add(engine, conversation_id, f"q {n}") for n in range(4)),
        delete(engine, conversation_id),
        return_exceptions=True,
    )

    added = [r for r in results if isinstance(r, Message)]
    assert results[-1] is True
    assert all(isinstance(r, (Message, ConversationNotFound)) for r in results[:-1])
    async with engine.begin() as connection:
        stored = await load_messages(connection, conversation_id=conversation_id)
    assert len(stored) == len(added)


async def test_a_conversation_past_its_deadline_reads_as_missing(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)
    deadline = NOW + LIMITS.retention

    assert await read(engine, conversation_id, now=deadline - timedelta(seconds=1))
    assert await read(engine, conversation_id, now=deadline) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id, now=deadline)
    # Its owner may still delete it before the retention sweep does.
    assert await delete(engine, conversation_id, now=deadline) is True


async def test_a_full_conversation_refuses_the_next_message(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    two = ConversationLimits(max_messages=2)
    await add(engine, conversation_id, limits=two)
    await add(
        engine, conversation_id, "24 days.", role=MessageRole.ASSISTANT, limits=two
    )

    with pytest.raises(ConversationFull):
        await add(engine, conversation_id, limits=two)


@pytest.mark.parametrize(
    "text",
    [f"{MARKER}{chr(0)}", f"{MARKER}{chr(0xD800)}", MARKER * 2_000, "   "],
)
async def test_text_postgresql_cannot_store_is_refused_quietly(
    engine: AsyncEngine, text: str
) -> None:
    conversation_id = await start(engine)

    with pytest.raises(InvalidConversation) as caught:
        await add(engine, conversation_id, text)

    assert MARKER not in str(caught.value)
    assert caught.value.__context__ is None
    transcript = await read(engine, conversation_id)
    assert transcript is not None
    assert transcript.messages == ()


# --- Review regressions -------------------------------------------------------------


async def test_reading_then_writing_in_one_transaction_cannot_deadlock(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    both_read = asyncio.Barrier(2)

    async def read_then_add(text: str) -> Message:
        async with engine.begin() as connection:
            await read_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )
            await both_read.wait()
            return await add_message(
                connection,
                owner_id=OWNER,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                text=text,
                now=NOW,
                limits=LIMITS,
            )

    added = await asyncio.gather(read_then_add("first"), read_then_add("second"))

    assert sorted(m.ordinal for m in added) == [0, 1]


async def test_reading_then_deleting_in_one_transaction_cannot_deadlock(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    both_read = asyncio.Barrier(2)

    async def read_then_delete() -> bool:
        async with engine.begin() as connection:
            await read_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )
            await both_read.wait()
            return await delete_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )

    results = await asyncio.gather(read_then_delete(), read_then_delete())

    assert sorted(results) == [False, True]
    assert await purge_jobs(engine, conversation_id) == 1


async def test_a_deletion_is_never_earlier_than_the_last_message(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    last = await add(engine, conversation_id, now=NOW + timedelta(seconds=13))

    await delete(engine, conversation_id, now=NOW)

    async with engine.begin() as connection:
        stored = await load_conversation(connection, conversation_id=conversation_id)
    assert stored is not None
    assert stored.deleted_at == last.created_at


@pytest.mark.parametrize(
    "zone",
    [ZoneInfo("Europe/London"), timezone(timedelta(hours=2))],
)
async def test_any_time_zone_gives_the_same_deadline_in_utc(
    engine: AsyncEngine, zone: tzinfo
) -> None:
    # Winter in London, so the 180 days run into summer time.
    local = NOW.astimezone(zone)
    limits = ConversationLimits(retention=timedelta(days=180))
    async with engine.begin() as connection:
        conversation = await start_conversation(
            connection, owner_id=OWNER, now=local, limits=limits
        )

    assert conversation.created_at == NOW
    assert conversation.created_at.tzinfo is UTC
    assert conversation.retention_deadline == NOW + timedelta(days=180)
