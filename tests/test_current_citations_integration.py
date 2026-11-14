"""Confirming Citations against the Documents as they are now, in PostgreSQL.

Each test answers from real retrieval, changes the stored Documents the
way a Synchronization Run would, and checks what `confirm_citations`
lets through to the client.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.context_budget import ContextBudget
from knowledge_service.answering.current_citations import confirm_citations
from knowledge_service.answering.evidence import EvidenceId, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelIdentity,
    ModelReply,
    generate_answer,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.contracts import Usage
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, DocumentId, GroupId, SourceId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000056")
OBSERVED_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e2")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e3")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f1")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000045")
BUDGET = ContextBudget(context_tokens=32_000, answer_tokens=1_000, history_tokens=0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=900,
    output_tokens=40,
    total_tokens=940,
)
LEAVE = "Every employee receives 24 days of annual leave."
CARRY = "Unused annual leave carries over for 5 days."

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


def a_record(name: str, *, title: str, text: str, version: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    *,
    title: str = "Leave policy",
    text: str = LEAVE,
    readers: Sequence[Subject] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text, version=version),
        readers=readers,
        observed_at=observed_at,
    )
    return reconciled.document.document_id


class CitingModel:
    """Answers by citing every Evidence it was given, in prompt order."""

    def __init__(self, answer: str = "You get 24 days of annual leave.") -> None:
        self._answer = answer

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(provider="fake", model="fake-1")

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        schema = prompt.response_schema()
        ids = schema["properties"]["citations"]["items"]["enum"]
        text = json.dumps(
            {
                "answer": self._answer,
                "citations": ids,
                "abstained": False,
                "reason": None,
            }
        )
        return ModelReply(text=text, refused=False, usage=USAGE)


async def answer(
    runtime: DatabaseRuntime, question: str = "annual leave"
) -> AnswerResult:
    """Retrieve, then answer citing everything retrieved."""
    async with runtime.transaction() as connection:
        found = await search_lexical_chunks(
            connection, text=question, context=CONTEXT, limit=10
        )
    result = await generate_answer(
        question,
        build_evidence(found.candidates),
        BUDGET,
        CitingModel(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )
    assert result.outcome is AnswerOutcome.ANSWERED
    return result


async def confirm(
    runtime: DatabaseRuntime,
    result: AnswerResult,
    context: AuthorizationContext = CONTEXT,
) -> AnswerResult:
    async with runtime.transaction() as connection:
        return await confirm_citations(
            connection, context, result, request_id=REQUEST_ID
        )


def assert_withdrawn(confirmed: AnswerResult, original: AnswerResult) -> None:
    assert confirmed.outcome is AnswerOutcome.WITHDRAWN
    assert (confirmed.text, confirmed.reason, confirmed.citations) == ("", None, ())
    assert confirmed.defect is None
    # The call was paid for, and the context report still explains it.
    assert confirmed.usage == original.usage
    assert confirmed.context == original.context


@pytest.mark.integration
async def test_a_current_answer_reaches_the_client_unchanged(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)

    confirmed = await confirm(runtime, result)

    assert len(result.citations) == 2
    assert confirmed == result


@pytest.mark.integration
async def test_a_renamed_document_is_cited_under_its_current_title(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt", title="Leave policy")
    result = await answer(runtime)
    await publish(
        runtime, "leave.txt", title="Annual leave", version="v2", observed_at=LATER
    )

    confirmed = await confirm(runtime, result)

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    (before,) = result.citations
    (after,) = confirmed.citations
    assert (after.chunk_id, after.excerpt) == (before.chunk_id, before.excerpt)
    assert (before.title, after.title) == ("Leave policy", "Annual leave")
    assert confirmed.text == result.text


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime, "leave.txt", readers=(Subject.user(OTHER),), observed_at=LATER
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        text="Every employee receives 26 days of annual leave.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


async def purge(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.text.contains("24 days"))
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone, purge],
    ids=["access-revoked", "new-version", "tombstoned", "chunks-purged"],
)
async def test_an_answer_citing_a_document_that_changed_is_withdrawn(
    runtime: DatabaseRuntime, change: object
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)
    assert len(result.citations) == 2

    await change(runtime)  # type: ignore[operator]
    confirmed = await confirm(runtime, result)

    # One Citation failed, so the whole Answer is withheld, not just it.
    assert_withdrawn(confirmed, result)


@pytest.mark.integration
async def test_another_readers_context_cannot_confirm_the_answer(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)

    confirmed = await confirm(runtime, result, AuthorizationContext(user_id=OTHER))

    assert_withdrawn(confirmed, result)


async def forged(
    runtime: DatabaseRuntime, result: AnswerResult, *, private: DocumentId
) -> tuple[Citation, Citation]:
    """Two hand-made Citations of the private Document's Chunk."""
    async with runtime.transaction() as connection:
        record = (
            await connection.execute(
                sa.select(chunks.c.chunk_id).where(
                    chunks.c.document_id == private.value
                )
            )
        ).one()
    (real,) = result.citations
    secret = ChunkId(record.chunk_id)
    # A fresh Evidence ID each: otherwise the check refuses the repeat first.
    whole = replace(
        real, evidence_id=EvidenceId.new(), chunk_id=secret, document_id=private
    )
    relabelled = replace(real, evidence_id=EvidenceId.new(), chunk_id=secret)
    return whole, relabelled


@pytest.mark.integration
async def test_a_citation_of_a_chunk_the_reader_may_not_see_never_reaches_the_client(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt")
    private = await publish(
        runtime,
        "secret.txt",
        title=MARKER,
        text=f"{MARKER} salary bands.",
        readers=(Subject.user(OTHER),),
    )
    result = await answer(runtime)

    for citation in await forged(runtime, result, private=private):
        sneaked = replace(result, citations=(*result.citations, citation))
        confirmed = await confirm(runtime, sneaked)
        assert_withdrawn(confirmed, sneaked)
        assert MARKER not in repr(confirmed)
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in caplog.records)


@pytest.mark.integration
async def test_each_check_logs_counts_only(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt", title=MARKER)
    result = await answer(runtime)
    await revoke(runtime)

    await confirm(runtime, result)

    (record,) = [r for r in caplog.records if r.message == "citations confirmed"]
    assert record.__dict__["citation_check"] == {
        "request_id": str(REQUEST_ID),
        "cited": 1,
        "withdrawn": 1,
        "outcome": "withdrawn",
    }
    assert MARKER not in caplog.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["document_id", "source_id", "content_version"], ids=str
)
async def test_a_citation_whose_identity_differs_from_storage_is_withdrawn(
    runtime: DatabaseRuntime, field: str
) -> None:
    await publish(runtime, "leave.txt")
    other = await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime, "24 days")
    (real,) = result.citations
    wrong = {
        "document_id": other,
        "source_id": SourceId(UUID(int=99)),
        "content_version": real.content_version.next(),
    }[field]
    relabelled = replace(result, citations=(replace(real, **{field: wrong}),))

    confirmed = await confirm(runtime, relabelled)

    assert_withdrawn(confirmed, relabelled)


@pytest.mark.integration
async def test_a_confirmed_citation_is_rebuilt_from_storage(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)
    (real,) = result.citations
    forged = replace(
        real, title=MARKER, heading_path=MARKER, source_link=MARKER, excerpt=MARKER
    )

    confirmed = await confirm(runtime, replace(result, citations=(forged,)))

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    assert confirmed.citations == (real,)
