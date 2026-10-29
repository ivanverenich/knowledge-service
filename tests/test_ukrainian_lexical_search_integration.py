# Cyrillic letters and Ukrainian apostrophes are what these tests are about.
# ruff: noqa: RUF001, RUF003
"""Real PostgreSQL tests for the Ukrainian lexical baseline over Chunks.

`FIXTURE_REPORT` is the report the evidence copies: each query, the
Documents it finds, and why. The misses are expected; they are what the
`simple` configuration cannot do, and a later task that adds Ukrainian
morphology should turn them into matches on purpose.
"""

import asyncio
import unicodedata
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
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalMatch,
    search_ukrainian_chunks,
    ukrainian_search_statement,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000032")
OBSERVED_AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000b1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000b2")
CONTEXT = AuthorizationContext(user_id=READER)
MARKER = "маркерзапиту"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/dovidnyk', 'Dovidnyk', true, now(), now())"
)
INSERT_CHUNK = sa.text(
    "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal, text,"
    " heading_path, token_count, content_fingerprint) VALUES (gen_random_uuid(),"
    " :document_id, 1, 1, 'Додаткові нотатки.', NULL, 2, repeat('c', 64))"
)
# Chunks whose stored terms differ from what their current title, headings,
# and text give, in either configuration.
STALE_CHUNKS = sa.text(
    "SELECT count(*) FROM chunks JOIN documents USING (document_id)"
    " WHERE search_simple IS DISTINCT FROM"
    " simple_search_vector(documents.title, chunks.heading_path, chunks.text)"
    " OR search_en IS DISTINCT FROM"
    " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
)

# The fixture corpus. "’" is U+2019 and "ʼ" is U+02BC, the apostrophe the
# Ukrainian standard prefers; the default parser keeps only the second inside
# a word.
CORPUS = {
    "vidpustka.txt": (
        "Відпустка",
        "Працівник має право на щорічну оплачувану відпустку."
        " Працівники узгоджують їхню відпустку з керівником.",
    ),
    "likarnyanyi.md": (
        "Лікарняний",
        "# Тимчасова непрацездатність на роботі\n\n"
        "Лікарняний оформлює лікар в електронній системі.",
    ),
    "simya.txt": (
        "Сімейні обставини",
        "Додаткову відпустку надають у зв’язку з народженням дитини в сімʼї.",
    ),
    "leave.txt": ("Time off", "Employees request paid leave every year."),
}

# (query, Documents found best first, why)
FIXTURE_REPORT: list[tuple[str, list[str], str]] = [
    ("відпустку", ["vidpustka.txt", "simya.txt"], "exact form in the text"),
    ("ВІДПУСТКА", ["vidpustka.txt"], "exact form in a title, any letter case"),
    ("ЇХНЮ", ["vidpustka.txt"], "Ukrainian capitals ї, і lowercase"),
    ('"оплачувану відпустку"', ["vidpustka.txt"], "exact phrase"),
    ("непрацездатність", ["likarnyanyi.md"], "exact form in a heading"),
    ("зв'язку", ["simya.txt"], "both apostrophes split the word the same way"),
    ("сімʼї", ["simya.txt"], "the U+02BC apostrophe keeps the word whole"),
    ("відпустки", [], "miss: another case of відпустка, no stemming"),
    ("лікарняного", [], "miss: another case of лікарняний, no stemming"),
    ("оплачувана відпустка", [], "miss: the text has оплачувану"),
    ("сім'ї", [], "miss: an ASCII apostrophe splits the word, the text has ʼ"),
    ("leave", ["leave.txt"], "any language matches in its exact form"),
    ("leaves", [], "miss: no English stemming either"),
    ("every", ["leave.txt"], "no stop words: English every is kept"),
    (
        "на",
        ["likarnyanyi.md", "vidpustka.txt"],
        "noise: no stop words, so на matches every Chunk with it",
    ),
]


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
    """A one-Chunk Document; Markdown when the text starts with a heading."""
    media_type = "text/markdown" if text.startswith("#") else "text/plain"
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
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
) -> tuple[LexicalMatch, ...]:
    async with runtime.transaction() as connection:
        return await search_ukrainian_chunks(
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


async def stale_chunks(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(await connection.scalar(STALE_CHUNKS) or 0)


@pytest.mark.integration
async def test_the_fixture_report_shows_exact_matches_and_known_misses(
    runtime: DatabaseRuntime,
) -> None:
    for name, (title, text) in CORPUS.items():
        await a_document(runtime, name, title=title, text=text)

    report = [(query, await names(runtime, query)) for query, _, _ in FIXTURE_REPORT]

    assert report == [(query, found) for query, found, _ in FIXTURE_REPORT]


@pytest.mark.integration
async def test_a_title_match_outranks_a_heading_match_and_a_text_match(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "text.txt", title="Нотатки", text="Загальні нотатки про адаптацію."
    )
    await a_document(
        runtime, "heading.md", title="Посібник", text="# Адаптацію\n\nЗагальні нотатки."
    )
    await a_document(
        runtime, "title.txt", title="Адаптацію", text="Загальні нотатки для команди."
    )

    matches = await search(runtime, "адаптацію")

    assert await names(runtime, "адаптацію") == ["title.txt", "heading.md", "text.txt"]
    assert len({match.score for match in matches}) == 3
    assert [match.rank for match in matches] == [1, 2, 3]


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "hidden.md",
        title="Зарплата",
        text="# Зарплата\n\nЗарплата для кожного рівня, зарплата щомісяця.",
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime, "visible.txt", title="Виплати", text="Зарплата виплачується щомісяця."
    )

    assert await names(runtime, "зарплата") == ["visible.txt"]
    assert await names(runtime, "зарплата", limit=1) == ["visible.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert await names(runtime, "зарплата", context=other) == [
        "hidden.md",
        "visible.txt",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        "відпустка & | ! ( ) :* <-> право",
        "'; DROP TABLE chunks; --",
        "відпустку:*",
        '"незакрита лапка',
    ],
)
async def test_any_typed_text_is_read_as_words_never_as_syntax(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "vidpustka.txt", title="Відпустка", text="Право на відпустку."
    )

    await search(runtime, text)

    assert await names(runtime, "відпустку") == ["vidpustka.txt"]


@pytest.mark.integration
async def test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "vidpustka.txt", title="Відпустка", text="Право на відпустку."
    )

    async with runtime.transaction() as connection:
        with pytest.raises(InvalidLexicalQuery) as refused:
            await search_ukrainian_chunks(
                connection, text=MARKER + " " + "-" * 40, context=CONTEXT, limit=10
            )
        after = await search_ukrainian_chunks(
            connection, text="відпустку", context=CONTEXT, limit=10
        )

    assert len(after) == 1
    # Error reporters walk the whole chain; the database error repeats the text.
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert MARKER not in str(refused.value)


@pytest.mark.integration
async def test_decomposed_letters_match_their_composed_form(
    runtime: DatabaseRuntime,
) -> None:
    # й and ї can be stored as one character or as a letter plus a mark.
    composed = "Йод і їхня відпустка."
    decomposed = unicodedata.normalize("NFD", "Йодний розчин і їхній склад.")
    await a_document(runtime, "composed.txt", title="Склад", text=composed)
    await a_document(runtime, "decomposed.txt", title="Розчин", text=decomposed)

    assert await names(runtime, unicodedata.normalize("NFD", "йод")) == ["composed.txt"]
    assert await names(runtime, "їхній") == ["decomposed.txt"]
    assert await names(runtime, "йодний") == ["decomposed.txt"]


@pytest.mark.integration
async def test_a_title_only_change_updates_what_the_chunks_match(
    runtime: DatabaseRuntime,
) -> None:
    text = "Загальні нотатки для команди."
    await a_document(runtime, "notes.txt", title="Адаптація", text=text)

    renamed = await reconcile_document(
        runtime,
        a_record("notes.txt", title="Переїзд", text=text),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert renamed.change is Change.TITLE
    assert await names(runtime, "адаптація") == []
    assert await names(runtime, "переїзд") == ["notes.txt"]
    assert await stale_chunks(runtime) == 0


@pytest.mark.integration
async def test_a_chunk_written_during_a_title_change_gets_the_new_title(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime, "notes.txt", title="Адаптація", text="Загальні нотатки."
    )
    async with (
        runtime.engine.connect() as renaming,
        runtime.engine.connect() as writing,
    ):
        await renaming.begin()
        await renaming.execute(
            sa.text("UPDATE documents SET title = 'Переїзд' WHERE document_id = :id"),
            {"id": document_id.value},
        )

        async def add_chunk() -> None:
            async with writing.begin():
                await writing.execute(INSERT_CHUNK, {"document_id": document_id.value})

        adding = asyncio.create_task(add_chunk())
        finished_early, _ = await asyncio.wait({adding}, timeout=0.5)
        await renaming.commit()
        await adding

    assert not finished_early
    assert await stale_chunks(runtime) == 0
    assert await names(runtime, "переїзд") == ["notes.txt", "notes.txt"]


@pytest.mark.integration
async def test_a_direct_write_to_the_search_terms_is_replaced(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "notes.txt", title="Адаптація", text="Загальні нотатки.")

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("UPDATE chunks SET search_simple = to_tsvector('simple', 'фальш')")
        )

    assert await names(runtime, "фальш") == []
    assert await stale_chunks(runtime) == 0


@pytest.mark.integration
async def test_the_search_reads_the_gin_index(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Документ ' || i,"
                " 1, repeat('a', 64), 1, repeat('b', 64), 'available', now(), now()"
                " FROM generate_series(1, 2000) AS i"
            ),
            {"source": SOURCE.value},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal,"
                " text, heading_path, token_count, content_fingerprint)"
                " SELECT gen_random_uuid(), document_id, 1, 0,"
                " 'слова для заповнення ' || external_id, NULL, 4, repeat('c', 64)"
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
        statement = ukrainian_search_statement(
            text="переїзд",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = explained.scalars().all()

    assert any("ix_chunks_search_simple" in line for line in plan), "\n".join(plan)


@pytest.mark.integration
def test_the_migration_fills_the_terms_of_existing_chunks(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "c7d8e9f0a1b2")
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
                    " (gen_random_uuid(), :source, 'old.txt', 'Переїзд', 1,"
                    " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now())"
                ),
                {"source": SOURCE.value},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, document_id, content_version,"
                    " ordinal, text, heading_path, token_count, content_fingerprint)"
                    " SELECT gen_random_uuid(), document_id, 1, 0,"
                    " 'Переїзд до нового офісу.', 'Офіси', 4, repeat('c', 64)"
                    " FROM documents"
                )
            )
        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            stored = connection.scalar(
                sa.text("SELECT search_simple::text FROM chunks")
            )
    finally:
        engine.dispose()

    assert stored == "'до':4C 'нового':5C 'офіси':2B 'офісу':6C 'переїзд':1A,3C"
