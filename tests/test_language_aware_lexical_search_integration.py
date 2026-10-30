# Ukrainian test data is written in Cyrillic.
# ruff: noqa: RUF001
"""Language-aware lexical search against a real database.

The corpus has an English, a Ukrainian, and a mixed Document, and all three
mention the identifier `E-1042`. Each test checks the route a Question gets
and the Documents it finds through `search_lexical_chunks`.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.dialects import postgresql

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
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_routing import (
    LexicalConfiguration,
    QuestionScript,
)
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalSearch,
    lexical_search_statement,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000033")
OBSERVED_AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000c1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000c2")
CONTEXT = AuthorizationContext(user_id=READER)
MARKER = "zebramarker"

ENGLISH = (LexicalConfiguration.ENGLISH,)
SIMPLE = (LexicalConfiguration.SIMPLE,)
BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

# name: (title, text)
CORPUS = {
    "leave.txt": (
        "Leave requests",
        "Employees request parental leave in the portal."
        " Error E-1042 means the request was rejected.",
    ),
    "vidpustka.txt": (
        "Відпустка",
        "Працівник подає заяву на відпустку в порталі."
        " Помилка E-1042 означає, що заяву відхилено.",
    ),
    "deploy.txt": (
        "Розгортання",
        "Команда розгортає сервіси через deployments у Kubernetes."
        " Помилка E-1042 зупиняє розгортання.",
    ),
}


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


async def the_corpus(runtime: DatabaseRuntime) -> None:
    for name, (title, text) in CORPUS.items():
        await a_document(runtime, name, title=title, text=text)


async def search(
    runtime: DatabaseRuntime,
    text: str,
    *,
    language: Language | None = None,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> LexicalSearch:
    async with runtime.transaction() as connection:
        return await search_lexical_chunks(
            connection, text=text, context=context, limit=limit, language=language
        )


async def found(runtime: DatabaseRuntime, text: str, **kwargs: Any) -> list[str]:
    """The names of the Documents found, in result order."""
    result = await search(runtime, text, **kwargs)
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.text("SELECT document_id, external_id FROM documents")
        )
        by_id = {row.document_id: row.external_id for row in rows}
    return [by_id[match.document_id.value] for match in result.matches]


@pytest.mark.integration
async def test_a_stated_language_that_agrees_uses_one_configuration(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    english = await search(runtime, "requesting leaves", language=Language.ENGLISH)
    ukrainian = await search(runtime, "відпустку", language=Language.UKRAINIAN)

    assert english.route.configurations == ENGLISH
    assert await found(runtime, "requesting leaves", language=Language.ENGLISH) == [
        "leave.txt"
    ]
    assert ukrainian.route.configurations == SIMPLE
    assert await found(runtime, "відпустку", language=Language.UKRAINIAN) == [
        "vidpustka.txt"
    ]


@pytest.mark.integration
async def test_a_wrong_stated_language_does_not_lose_english_stemming(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    # A Ukrainian reader types an English Question. With `simple` alone,
    # "requesting leaves" would miss "request" and "leave".
    result = await search(runtime, "requesting leaves", language=Language.UKRAINIAN)

    assert result.route.configurations == BOTH
    assert await found(runtime, "requesting leaves", language=Language.UKRAINIAN) == [
        "leave.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize("language", [None, Language.UNKNOWN])
async def test_without_a_stated_language_the_alphabet_decides(
    runtime: DatabaseRuntime, language: Language | None
) -> None:
    await the_corpus(runtime)

    english = await search(runtime, "requesting leaves", language=language)
    ukrainian = await search(runtime, "відпустку", language=language)

    assert (english.route.script, english.route.configurations) == (
        QuestionScript.LATIN,
        ENGLISH,
    )
    assert await found(runtime, "requesting leaves", language=language) == ["leave.txt"]
    assert (ukrainian.route.script, ukrainian.route.configurations) == (
        QuestionScript.CYRILLIC,
        SIMPLE,
    )
    assert await found(runtime, "відпустку", language=language) == ["vidpustka.txt"]


@pytest.mark.integration
async def test_a_mixed_question_keeps_english_stemming_for_its_english_words(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    # "deploying" matches "deployments" only through the English stem
    # `deploy`; "розгортає" is the exact Ukrainian form in the text.
    result = await search(runtime, "розгортає deploying")

    assert (result.route.script, result.route.configurations) == (
        QuestionScript.MIXED,
        BOTH,
    )
    assert await found(runtime, "розгортає deploying") == ["deploy.txt"]
    # A stated Ukrainian cannot narrow the route below what the text needs.
    assert await found(runtime, "розгортає deploying", language=Language.UKRAINIAN) == [
        "deploy.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("text", "script", "configurations", "names"),
    [
        (
            "E-1042",
            QuestionScript.NONE,
            BOTH,
            {"leave.txt", "vidpustka.txt", "deploy.txt"},
        ),
        ("error E-1042", QuestionScript.LATIN, ENGLISH, {"leave.txt"}),
        (
            "помилка E-1042",
            QuestionScript.CYRILLIC,
            SIMPLE,
            {"vidpustka.txt", "deploy.txt"},
        ),
    ],
)
async def test_identifiers_match_on_every_route_and_do_not_decide_it(
    runtime: DatabaseRuntime,
    text: str,
    script: QuestionScript,
    configurations: tuple[LexicalConfiguration, ...],
    names: set[str],
) -> None:
    await the_corpus(runtime)

    result = await search(runtime, text)

    assert (result.route.script, result.route.configurations) == (
        script,
        configurations,
    )
    assert set(await found(runtime, text)) == names


@pytest.mark.integration
@pytest.mark.parametrize("language", [None, Language.ENGLISH])
@pytest.mark.parametrize(
    ("text", "name"),
    [("IT", "it.txt"), ("it", "it.txt"), ("the who", "who.txt")],
)
async def test_a_question_of_english_stop_words_falls_back_to_simple(
    runtime: DatabaseRuntime, language: Language | None, text: str, name: str
) -> None:
    await a_document(
        runtime, "it.txt", title="IT department", text="The IT team fixes laptops."
    )
    await a_document(runtime, "who.txt", title="The Who", text="A rock band.")

    result = await search(runtime, text, language=language)

    assert result.route.configurations == BOTH
    assert result.route.english_words is False
    assert await found(runtime, text, language=language) == [name]


@pytest.mark.integration
async def test_a_word_joined_by_an_underscore_keeps_english_stemming(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "policy.txt", title="Політика", text="Файл leave_policy оновлено."
    )

    # `simple` alone would need "policies" exactly.
    assert await found(runtime, "leave_policies оновлено") == ["policy.txt"]


@pytest.mark.integration
async def test_a_chunk_found_by_both_configurations_is_returned_once(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    result = await search(runtime, "E-1042")

    chunk_ids = [match.chunk_id for match in result.matches]
    assert len(chunk_ids) == len(set(chunk_ids)) == 3
    assert [match.rank for match in result.matches] == [1, 2, 3]
    scores = [match.score for match in result.matches]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "hidden.txt",
        title="Deploying deployments",
        text="Розгортає deployments, розгортає deployments.",
        readers=(Subject.user(OTHER_USER),),
    )
    await the_corpus(runtime)

    assert await found(runtime, "розгортає deploying") == ["deploy.txt"]
    assert await found(runtime, "розгортає deploying", limit=1) == ["deploy.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert (await found(runtime, "розгортає deploying", context=other))[0] == (
        "hidden.txt"
    )


@pytest.mark.integration
@pytest.mark.parametrize("words", ["відпустку", "leave"])
async def test_a_refused_question_on_any_route_keeps_no_question_text(
    runtime: DatabaseRuntime, words: str
) -> None:
    await the_corpus(runtime)

    async with runtime.transaction() as connection:
        with pytest.raises(InvalidLexicalQuery) as refused:
            await search_lexical_chunks(
                connection,
                text=f"{MARKER} {'-' * 40} {words}",
                context=CONTEXT,
                limit=5,
            )
        still_usable = await connection.scalar(sa.text("SELECT 1"))

    assert still_usable == 1
    assert MARKER not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


@pytest.mark.integration
async def test_both_routes_read_both_gin_indexes(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Document ' || i,"
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
                " 'filler words для заповнення ' || external_id, NULL, 4,"
                " repeat('c', 64) FROM documents"
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
        statement = lexical_search_statement(
            BOTH,
            text="розгортає deploying",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = "\n".join(explained.scalars().all())

    assert "ix_chunks_search_en" in plan, plan
    assert "ix_chunks_search_simple" in plan, plan
