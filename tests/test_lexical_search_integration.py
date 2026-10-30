"""Real PostgreSQL tests for English full-text search over Chunks."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from database_fixtures import migration_config, migration_environment
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
    english_search_statement,
    search_english_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000031")
OBSERVED_AT = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000a2")
CONTEXT = AuthorizationContext(user_id=READER)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

# A second Chunk for a Document that already has one at ordinal 0.
INSERT_CHUNK = sa.text(
    "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal, text,"
    " heading_path, token_count, content_fingerprint) VALUES (gen_random_uuid(),"
    " :document_id, 1, 1, 'More notes.', NULL, 2, repeat('c', 64))"
)
# Chunks whose stored terms differ from what their current title, headings,
# and text give.
STALE_CHUNKS = sa.text(
    "SELECT count(*) FROM chunks JOIN documents USING (document_id)"
    " WHERE search_en IS DISTINCT FROM"
    " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
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


def a_record(
    name: str, *, title: str, text: str, version: str = "v1"
) -> NormalizedDocument:
    """A one-Chunk Document; Markdown when the text starts with a heading."""
    media_type = "text/markdown" if text.startswith("#") else "text/plain"
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type=media_type,
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


async def search(
    runtime: DatabaseRuntime,
    text: str,
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> tuple[LexicalCandidate, ...]:
    async with runtime.transaction() as connection:
        return await search_english_chunks(
            connection, text=text, context=context, limit=limit
        )


async def names(runtime: DatabaseRuntime, text: str, **kwargs: Any) -> list[str]:
    matches = await search(runtime, text, **kwargs)
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.text("SELECT document_id, external_id FROM documents")
        )
        by_id = {row.document_id: row.external_id for row in rows}
    return [by_id[match.document_id.value] for match in matches]


@pytest.mark.integration
async def test_english_stemming_matches_other_forms_of_a_word(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "leave.txt",
        title="Time off",
        text="Employees request paid leave every year.",
    )

    assert await names(runtime, "requesting leaves") == ["leave.txt"]
    assert await names(runtime, "employee") == ["leave.txt"]
    # Only stop words: no search terms, so nothing matches.
    assert await names(runtime, "the of and") == []


@pytest.mark.integration
async def test_a_title_match_outranks_a_heading_match_and_a_text_match(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "text.txt", title="Notes", text="General notes about onboarding."
    )
    await a_document(
        runtime, "heading.md", title="Guide", text="# Onboarding\n\nGeneral notes."
    )
    await a_document(
        runtime, "title.txt", title="Onboarding", text="General notes for staff."
    )

    matches = await search(runtime, "onboarding")

    assert await names(runtime, "onboarding") == [
        "title.txt",
        "heading.md",
        "text.txt",
    ]
    scores = [match.score for match in matches]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == 3
    assert [match.rank for match in matches] == [1, 2, 3]


@pytest.mark.integration
async def test_quoted_words_must_appear_together_and_in_order(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "phrase.txt", title="Policy", text="Read the paid leave policy."
    )
    await a_document(
        runtime, "apart.txt", title="Policy", text="Leave is always paid here."
    )

    assert sorted(await names(runtime, "paid leave")) == ["apart.txt", "phrase.txt"]
    assert await names(runtime, '"paid leave"') == ["phrase.txt"]
    assert await names(runtime, "leave -always") == ["phrase.txt"]
    assert sorted(await names(runtime, "always or read")) == [
        "apart.txt",
        "phrase.txt",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        "paid & | ! ( ) :* <-> leave",
        '"unclosed quote leave',
        "'; DROP TABLE chunks; --",
        "leave:*",
        "-",
        "or",
        "\\",
        "((((leave",
        "!!!",
    ],
)
async def test_any_typed_text_is_read_as_words_never_as_syntax(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "leave.txt", title="Time off", text="Employees request paid leave."
    )

    await search(runtime, text)

    # The table is still there and still searchable.
    assert await names(runtime, "leave") == ["leave.txt"]


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    # The hidden Document matches in its title, its heading, and its text.
    await a_document(
        runtime,
        "hidden.md",
        title="Salary bands",
        text="# Salary bands\n\nSalary bands for every salary grade.",
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime, "visible.txt", title="Payroll", text="Salary is paid monthly."
    )

    assert await names(runtime, "salary") == ["visible.txt"]
    assert await names(runtime, "salary", limit=1) == ["visible.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert await names(runtime, "salary", context=other) == [
        "hidden.md",
        "visible.txt",
    ]


@pytest.mark.integration
async def test_a_title_only_change_updates_what_the_chunks_match(
    runtime: DatabaseRuntime,
) -> None:
    text = "General notes for staff."
    document_id = await a_document(runtime, "notes.txt", title="Onboarding", text=text)
    async with runtime.transaction() as connection:
        (before,) = await load_chunks(connection, document_id=document_id)

    renamed = await reconcile_document(
        runtime,
        a_record("notes.txt", title="Relocation", text=text),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        (after,) = await load_chunks(connection, document_id=document_id)

    assert renamed.change is Change.TITLE
    assert after.chunk_id == before.chunk_id
    assert await names(runtime, "onboarding") == []
    assert await names(runtime, "relocation") == ["notes.txt"]


@pytest.mark.integration
async def test_equal_scores_are_ordered_by_chunk_id(
    runtime: DatabaseRuntime,
) -> None:
    for name in ("twin-a.txt", "twin-b.txt", "twin-c.txt"):
        await a_document(runtime, name, title="Twin", text="Exactly the same words.")

    matches = await search(runtime, "same words")

    assert len({match.score for match in matches}) == 1
    chunk_ids = [match.chunk_id for match in matches]
    assert chunk_ids == sorted(chunk_ids, key=lambda chunk_id: chunk_id.value)


@pytest.mark.integration
async def test_the_search_reads_the_gin_index(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Doc ' || i, 1,"
                " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now()"
                " FROM generate_series(1, 2000) AS i"
            ),
            {"source": SOURCE.value},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal,"
                " text, heading_path, token_count, content_fingerprint)"
                " SELECT gen_random_uuid(), document_id, 1, 0,"
                " 'filler words number ' || external_id, NULL, 4, repeat('c', 64)"
                " FROM documents"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO access_grants (grant_id, document_id, subject_kind,"
                " subject_id, granted_at) SELECT gen_random_uuid(), document_id,"
                " 'public', NULL, now() FROM documents"
            )
        )
        await connection.execute(sa.text("ANALYZE chunks, documents, access_grants"))
        statement = english_search_statement(
            text="relocation",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = explained.scalars().all()

    assert any("ix_chunks_search_en" in line for line in plan), "\n".join(plan)


@pytest.mark.integration
def test_the_migration_fills_the_vectors_of_existing_chunks(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "b6c7d8e9f0a1")
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
            connection.execute(
                sa.text(
                    "INSERT INTO documents (document_id, source_id, external_id,"
                    " title, content_version, content_fingerprint,"
                    " authorization_version, authorization_fingerprint,"
                    " availability, created_at, updated_at) VALUES"
                    " (gen_random_uuid(), :source, 'old.txt', 'Relocation', 1,"
                    " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now())"
                ),
                {"source": SOURCE.value},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, document_id, content_version,"
                    " ordinal, text, heading_path, token_count, content_fingerprint)"
                    " SELECT gen_random_uuid(), document_id, 1, 0,"
                    " 'Moving to a new office.', 'Offices', 4, repeat('c', 64)"
                    " FROM documents"
                )
            )
        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            stored = connection.scalar(sa.text("SELECT search_en::text FROM chunks"))
    finally:
        engine.dispose()

    assert stored == "'move':3C 'new':6C 'offic':2B,7C 'reloc':1A"


async def stale_chunks(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(await connection.scalar(STALE_CHUNKS) or 0)


@pytest.mark.integration
@pytest.mark.parametrize("text", ["-" * 33, "- " * 40, "leave " + "-" * 40])
async def test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "leave.txt", title="Time off", text="Employees request paid leave."
    )

    async with runtime.transaction() as connection:
        # Once a raw InternalError whose message repeated the query text.
        with pytest.raises(InvalidLexicalQuery):
            await search_english_chunks(
                connection, text=text, context=CONTEXT, limit=10
            )
        after = await search_english_chunks(
            connection, text="leave", context=CONTEXT, limit=10
        )

    assert len(after) == 1


@pytest.mark.integration
async def test_a_chunk_written_during_a_title_change_gets_the_new_title(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime, "notes.txt", title="Onboarding", text="General notes for staff."
    )
    async with (
        runtime.engine.connect() as renaming,
        runtime.engine.connect() as writing,
    ):
        await renaming.begin()
        await renaming.execute(
            sa.text(
                "UPDATE documents SET title = 'Relocation' WHERE document_id = :id"
            ),
            {"id": document_id.value},
        )

        async def add_chunk() -> None:
            async with writing.begin():
                await writing.execute(INSERT_CHUNK, {"document_id": document_id.value})

        adding = asyncio.create_task(add_chunk())
        finished_early, _ = await asyncio.wait({adding}, timeout=0.5)
        await renaming.commit()
        await adding

    # The insert waited for the rename instead of keeping the old title.
    assert not finished_early
    assert await stale_chunks(runtime) == 0
    assert await names(runtime, "relocation") == ["notes.txt", "notes.txt"]


@pytest.mark.integration
async def test_a_direct_write_to_the_search_terms_is_replaced(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "notes.txt", title="Onboarding", text="General notes for staff."
    )

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("UPDATE chunks SET search_en = to_tsvector('english', 'bogus')")
        )

    assert await names(runtime, "bogus") == []
    assert await stale_chunks(runtime) == 0
