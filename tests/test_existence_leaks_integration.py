"""Black-box: a Document the reader may not see looks exactly like no Document.

Each test observes what a reader, a log, or the model can see twice: once
where another User's Document does not exist, and once where it exists and
is the best possible match, or its real identifiers are used. The two
observations must be equal, once the values that differ on every request
anyway, UUIDs and opaque Evidence IDs, are masked.
"""

import dataclasses
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.current_citations import (
    CurrentCitations,
    confirm_citations,
    evidence_still_visible,
)
from knowledge_service.answering.evidence import Evidence, EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelReply,
)
from knowledge_service.answering.history import load_history
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.app import create_app
from knowledge_service.conversations import ConversationLimits
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId, UserId
from knowledge_service.persistence.conversations import (
    delete_conversation,
    start_conversation,
)
from knowledge_service.retrieval.vector_search import (
    search_similar_chunks,
)
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel, vector_source
from test_streaming import QUESTION, REQUEST_ID, USAGE, streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
)

BOB = AuthorizationContext(user_id=UserId(UUID(int=0xB0B)))
READERS_GROUP = next(iter(READER.group_ids))
LIMITS = ConversationLimits()
# Values that differ on every request whatever the data holds.
VOLATILE = [
    (
        re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
        "<id>",
    ),
    (re.compile(r"E[0-9a-f]{8}"), "<evidence>"),
]
# The attributes every log record has; anything else was passed as `extra`.
STANDARD = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def masked(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


class Watching(CitingModel):
    """The citing model, keeping what each prompt showed it."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.prompts: list[str] = []

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.prompts.append(masked(prompt.input))
        async for part in super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        ):
            yield part


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, str, str]]:
    """Every log record, from any logger, with every extra field.

    Only timing and identifiers are masked, so a record that differs in
    anything else, or appears only once, makes two observations differ.
    """
    records: list[tuple[str, str, str, str]] = []
    for record in caplog.records:
        fields = {
            key: value for key, value in record.__dict__.items() if key not in STANDARD
        }
        for value in fields.values():
            if isinstance(value, dict):
                cast(dict[str, Any], value).pop("duration_ms", None)
        records.append(
            (record.name, record.levelname, record.getMessage(), masked(repr(fields)))
        )
    return records


async def observe(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> tuple[Any, ...]:
    """Everything outside the server sees of one question about the handbook."""
    caplog.clear()
    model = Watching("handbook.txt")
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, vector_source(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/questions", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )
    return (
        response.status_code,
        masked(repr(sorted(response.headers.items()))),
        masked(response.text),
        tuple(model.prompts),
        tuple(logged(caplog)),
    )


async def bobs_secret(runtime: DatabaseRuntime) -> DocumentId:
    """Another User's Document, an exact match for the question."""
    return await a_document(
        runtime,
        "SECRET-bob.txt",
        readers=[Subject.user(BOB.user_id)],
        vector=[1.0, 0.0, 0.0],
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "own", [True, False], ids=["reader-has-one", "reader-has-none"]
)
async def test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, own: bool
) -> None:
    caplog.set_level(logging.DEBUG)
    if own:
        await a_document(
            runtime,
            "handbook.txt",
            readers=[Subject.group(READERS_GROUP)],
            vector=[0.6, 0.8, 0.0],
        )

    absent = await observe(runtime, caplog)
    await bobs_secret(runtime)
    present = await observe(runtime, caplog)

    assert present == absent
    # The reader with a Document of her own is answered; without, abstains.
    assert ('"decision":"answer"' if own else '"no_evidence"') in absent[2]
    assert "SECRET" not in repr(present)


async def bobs_evidence(runtime: DatabaseRuntime) -> Evidence:
    """Bob's real Chunk as Evidence, with the identifiers retrieval gave it."""
    await bobs_secret(runtime)
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection, identity=IDENTITY, query=QUERY, context=BOB, limit=1
        )
    (item,) = build_evidence(found.candidates).items
    return item


def invented(item: Evidence) -> Evidence:
    """The same Evidence pointing at a Chunk and Document that never existed."""
    return dataclasses.replace(
        item, chunk_id=ChunkId(uuid4()), document_id=DocumentId(uuid4())
    )


def answer_citing(item: Evidence) -> AnswerResult:
    citation = Citation(
        evidence_id=item.evidence_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        source_id=item.source_id,
        content_version=item.content_version,
        title=item.title,
        heading_path=item.heading_path,
        source_link=item.source_link,
        excerpt=item.excerpt,
    )
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text="The SECRET-bob.txt policy.",
        reason=None,
        citations=(citation,),
        defect=None,
        usage=USAGE,
        context=cast(Any, None),
    )


@pytest.mark.integration
async def test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            result = await confirm_citations(
                connection, READER, answer_citing(item), request_id=REQUEST_ID
            )
        outcomes.append((result, logged(caplog)))

    # The whole result, reason, defect, and usage included, and every record.
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].outcome is AnswerOutcome.WITHDRAWN


@pytest.mark.integration
async def test_forbidden_evidence_reads_like_missing_evidence(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            visible = await evidence_still_visible(
                connection, READER, EvidenceSet((item,)), request_id=REQUEST_ID
            )
        outcomes.append((visible, logged(caplog)))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] is False


@pytest.mark.integration
async def test_another_users_deleted_and_missing_conversations_read_the_same(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with runtime.transaction() as connection:
        bobs = await start_conversation(
            connection, owner_id=BOB.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        own = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await delete_conversation(
            connection,
            owner_id=READER.user_id,
            conversation_id=own.conversation_id,
            now=OBSERVED_AT,
        )
    targets = {
        "bobs": bobs.conversation_id,
        "deleted": own.conversation_id,
        "missing": ConversationId(uuid4()),
    }
    seen: dict[str, tuple[Any, ...]] = {}

    for name, conversation_id in targets.items():
        caplog.clear()
        async with runtime.transaction() as connection:
            history = await load_history(
                connection,
                READER,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
                budget_tokens=4_000,
            )
            deleted = await delete_conversation(
                connection,
                owner_id=READER.user_id,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
            )
        seen[name] = (history, deleted, logged(caplog))

    assert seen["bobs"] == seen["deleted"] == seen["missing"] == (None, False, [])
