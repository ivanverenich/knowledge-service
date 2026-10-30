# Ukrainian test data is written in Cyrillic.
# ruff: noqa: RUF001
"""Highlights on lexical Candidates, against a real database.

A highlight is a pair of offsets into the Candidate's `text`. Each test
checks the words they point at, through `search_lexical_chunks`.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.language import Language
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.lexical_search import (
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000055")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e1")
CONTEXT = AuthorizationContext(user_id=READER)

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


def a_record(name: str, *, title: str, text: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    title: str,
    text: str,
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text),
        readers=readers,
        observed_at=OBSERVED_AT,
    )
    return reconciled.document.document_id


async def only_candidate(
    runtime: DatabaseRuntime, text: str, *, language: Language | None = None
) -> LexicalCandidate:
    async with runtime.transaction() as connection:
        result = await search_lexical_chunks(
            connection, text=text, context=CONTEXT, limit=10, language=language
        )
    (candidate,) = result.candidates
    return candidate


def highlighted(candidate: LexicalCandidate) -> list[str]:
    return [candidate.text[h.start : h.end] for h in candidate.highlights]


@pytest.mark.integration
async def test_highlights_point_at_the_words_english_stemming_matched(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "leave.txt",
        title="Handbook",
        text="Employees request parental leave in the portal.",
    )

    candidate = await only_candidate(runtime, "requesting leaves")

    assert highlighted(candidate) == ["request", "leave"]


@pytest.mark.integration
async def test_an_identifier_is_one_highlight(runtime: DatabaseRuntime) -> None:
    await a_document(
        runtime,
        "error.txt",
        title="Handbook",
        text="Error E-1042 means the request was rejected.",
    )

    candidate = await only_candidate(runtime, "E-1042")

    # PostgreSQL marks "E" and "-1042" apart; touching marks join.
    assert highlighted(candidate) == ["E-1042"]


@pytest.mark.integration
async def test_highlights_join_the_words_of_every_configuration_that_matched(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "policy.txt", title="Handbook", text="The policy.")

    # English drops "the"; `simple` keeps and marks it.
    candidate = await only_candidate(runtime, "the policy", language=Language.MIXED)

    assert highlighted(candidate) == ["The", "policy"]


@pytest.mark.integration
async def test_a_mixed_question_highlights_both_alphabets(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "deploy.txt",
        title="Розгортання",
        text="Команда розгортає сервіси через deployments у Kubernetes.",
    )

    candidate = await only_candidate(runtime, "розгортає deploying")

    assert highlighted(candidate) == ["розгортає", "deployments"]


@pytest.mark.integration
async def test_a_match_only_in_the_title_has_no_highlight(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "notes.txt", title="Leave", text="General notes.")

    candidate = await only_candidate(runtime, "leave")

    assert candidate.highlights == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    "stored",
    [
        "Leave \x02policy\x03 here, policy.",
        "Leave policy\x03 here.",
        "Leave policy here.\x02",
    ],
)
async def test_text_holding_a_mark_character_gets_no_highlight(
    runtime: DatabaseRuntime, stored: str
) -> None:
    document_id = await a_document(
        runtime, "odd.txt", title="Handbook", text="Leave policy here."
    )
    # A row can hold the characters `ts_headline` marks words with.
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(chunks)
            .where(chunks.c.document_id == document_id.value)
            .values(text=stored)
        )

    candidate = await only_candidate(runtime, "policy")

    # Offsets that might point at the wrong words are worse than none.
    assert candidate.text == stored
    assert candidate.highlights == ()
