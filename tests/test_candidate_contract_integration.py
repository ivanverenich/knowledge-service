"""The Candidate contract, checked the same way for every retrieval branch.

Each test runs once for vector search and once for lexical search, through
the function a caller uses. Every Document's text holds the word "policy",
and every Chunk gets a vector, so both branches can find the same Chunks.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingSpaceId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.documents import load_document_by_id
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import chunks, embedding_spaces
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    InvalidCandidateLimit,
    VectorCandidate,
    search_similar_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000044")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
READER = UserId.parse("00000000-0000-0000-0000-0000000000d1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000d2")
CONTEXT = AuthorizationContext(user_id=READER)
QUERY = (1.0, 0.0, 0.0)
WORD = "policy"
MARKER = "zebramarker"
# Long enough that each heading starts its own Chunk.
SECTION = " ".join(["Staff read this section of the handbook carefully."] * 12)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@dataclass(frozen=True, slots=True)
class Found:
    """What one branch returned, in the terms the contract talks about."""

    result: object
    candidates: Sequence[Candidate]
    truncated: bool
    # Each Candidate's fit, lower is better, so order can be checked alike.
    fits: list[float]


@dataclass(frozen=True, slots=True)
class Branch:
    name: str
    search: Callable[[AsyncConnection, AuthorizationContext, int], Awaitable[Found]]
    invalid_limit: type[Exception]


async def by_vector(
    connection: AsyncConnection, context: AuthorizationContext, limit: int
) -> Found:
    result = await search_similar_chunks(
        connection, identity=IDENTITY, query=QUERY, context=context, limit=limit
    )
    return Found(
        result=result,
        candidates=result.candidates,
        truncated=result.truncated,
        fits=[candidate.distance for candidate in result.candidates],
    )


async def by_words(
    connection: AsyncConnection, context: AuthorizationContext, limit: int
) -> Found:
    result = await search_lexical_chunks(
        connection, text=WORD, context=context, limit=limit
    )
    return Found(
        result=result,
        candidates=result.candidates,
        truncated=result.truncated,
        fits=[-candidate.score for candidate in result.candidates],
    )


BRANCHES = [
    pytest.param(Branch("vector", by_vector, InvalidCandidateLimit), id="vector"),
    pytest.param(Branch("lexical", by_words, InvalidLexicalQuery), id="lexical"),
]


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=IDENTITY.provider,
                model=IDENTITY.model,
                dimensions=IDENTITY.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(
    name: str, *, title: str, text: str, version: str = "v1"
) -> NormalizedDocument:
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
    text: str = "The policy.",
    title: str = "Handbook",
    vector: Sequence[float] = (1.0, 1.0, 0.0),
    readers: Sequence[Subject] = (Subject.public(),),
    version: str = "v1",
) -> DocumentId:
    """Store a Document and give each of its Chunks the same vector."""
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text, version=version),
        readers=readers,
        observed_at=OBSERVED_AT,
    )
    document_id = reconciled.document.document_id
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, IDENTITY)
        assert space_id is not None
        stored = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=IDENTITY.dimensions,
            vectors={chunk.chunk_id: vector for chunk in stored},
            created_at=OBSERVED_AT,
        )
    return document_id


async def found(
    runtime: DatabaseRuntime,
    branch: Branch,
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> Found:
    async with runtime.transaction() as connection:
        return await branch.search(connection, context, limit)


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_each_candidate_carries_the_provenance_of_its_chunk(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(runtime, "guide.md", title="Staff guide", text="Draft.")
    # A second version, so the content version is not the first one.
    document_id = await a_document(
        runtime,
        "guide.md",
        title="Staff guide",
        text=(
            f"# Leave\n\nThe leave policy. {SECTION}\n\n"
            f"# Travel\n\nThe travel policy. {SECTION}"
        ),
        version="v2",
    )

    result = await found(runtime, branch)

    async with runtime.transaction() as connection:
        document = await load_document_by_id(connection, document_id=document_id)
        stored = await load_chunks(connection, document_id=document_id)
    assert document is not None
    # Two Chunks, each with its own heading and anchor, so nothing matches
    # by chance.
    assert len({(chunk.heading_path, chunk.source_anchor) for chunk in stored}) == 2
    assert all(chunk.heading_path and chunk.source_anchor for chunk in stored)
    assert {chunk.content_version for chunk in stored} == {ContentVersion(2)}
    by_id = {chunk.chunk_id: chunk for chunk in stored}
    assert {candidate.chunk_id for candidate in result.candidates} == set(by_id)
    for candidate in result.candidates:
        chunk = by_id[candidate.chunk_id]
        assert candidate.document_id == document_id
        assert candidate.source_id == SOURCE
        assert candidate.content_version == chunk.content_version
        assert candidate.ordinal == chunk.ordinal
        assert candidate.external_id == "guide.md"
        assert candidate.title == document.title == "Staff guide"
        assert candidate.heading_path == chunk.heading_path
        assert candidate.source_anchor == chunk.source_anchor
        assert candidate.text == chunk.text


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_ranks_count_from_one_best_first_and_ties_go_by_chunk_id(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    # Three Documents alike in words and vector tie; one fits better in both.
    for name in ["a.txt", "b.txt", "c.txt"]:
        await a_document(runtime, name)
    await a_document(
        runtime, "best.txt", title="Policy", text="Policy policy.", vector=QUERY
    )

    result = await found(runtime, branch)

    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3, 4]
    assert result.fits == sorted(result.fits)
    assert result.candidates[0].external_id == "best.txt"
    tied = [candidate.chunk_id for candidate in result.candidates[1:]]
    assert len(set(result.fits[1:])) == 1
    assert tied == sorted(tied, key=lambda chunk_id: chunk_id.value)


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize(
    ("limit", "returned", "truncated"),
    [(1, 1, True), (2, 2, True), (3, 3, False), (MAX_CANDIDATES, 3, False)],
)
async def test_the_limit_bounds_the_candidates_and_truncated_says_more_exist(
    runtime: DatabaseRuntime,
    branch: Branch,
    limit: int,
    returned: int,
    truncated: bool,
) -> None:
    # Three fits, best first in both branches, so the limit must keep the
    # best ones, not just any.
    await a_document(
        runtime, "a.txt", title="Policy", text="Policy policy policy.", vector=QUERY
    )
    await a_document(runtime, "b.txt", text="Policy policy.", vector=(1.0, 0.5, 0.0))
    await a_document(runtime, "c.txt", text="The policy.", vector=(1.0, 1.0, 0.0))

    result = await found(runtime, branch, limit=limit)

    assert [c.external_id for c in result.candidates] == [
        "a.txt",
        "b.txt",
        "c.txt",
    ][:returned]
    assert result.truncated is truncated


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_the_limit_cuts_ties_by_chunk_id_whatever_order_rows_are_stored_in(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    tied = [await a_document(runtime, f"{index}.txt") for index in range(6)]
    # Rewriting a row moves it to free space, often at the end of the table.
    # Rewriting the last Document first tends to store the rows against
    # Chunk ID order, so the order cannot come right by accident.
    async with runtime.transaction() as connection:
        for document_id in reversed(tied):
            await connection.execute(
                sa.update(chunks)
                .where(chunks.c.document_id == document_id.value)
                .values(ordinal=chunks.c.ordinal)
            )
    everything = await found(runtime, branch)
    assert len(set(everything.fits)) == 1
    by_chunk_id = sorted(
        (candidate.chunk_id for candidate in everything.candidates),
        key=lambda chunk_id: chunk_id.value,
    )

    for limit in range(1, len(tied) + 1):
        result = await found(runtime, branch, limit=limit)

        assert [c.chunk_id for c in result.candidates] == by_chunk_id[:limit]


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_a_better_chunk_the_reader_may_not_see_is_never_a_candidate(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(
        runtime,
        "hidden.txt",
        title="Policy",
        text="Policy policy policy.",
        vector=QUERY,
        readers=(Subject.user(OTHER_USER),),
    )
    await a_document(runtime, "visible.txt")

    for limit in [1, 10]:
        result = await found(runtime, branch, limit=limit)

        assert [c.external_id for c in result.candidates] == ["visible.txt"]
        # Not counted either: nothing more exists for this reader.
        assert result.truncated is False
    other = await found(
        runtime, branch, context=AuthorizationContext(user_id=OTHER_USER)
    )
    assert other.candidates[0].external_id == "hidden.txt"


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_no_eligible_chunk_is_an_empty_result(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(runtime, "hidden.txt", readers=(Subject.user(OTHER_USER),))

    result = await found(runtime, branch)

    assert list(result.candidates) == []
    assert result.truncated is False


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_a_result_repr_shows_no_document_text(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(
        runtime,
        f"{MARKER}.md",
        title=MARKER,
        text=f"# {MARKER}\n\nThe {MARKER} policy.",
    )

    result = await found(runtime, branch)

    assert len(result.candidates) == 1
    assert MARKER in result.candidates[0].text
    assert MARKER not in repr(result.result)
    assert MARKER not in repr(result.candidates[0])


@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize("limit", [0, -1, MAX_CANDIDATES + 1, True, 2.0, "5", None])
async def test_a_bad_limit_is_refused_before_the_database(
    branch: Branch, limit: object
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(branch.invalid_limit):
        await branch.search(unused, CONTEXT, cast(int, limit))


def test_both_candidate_types_meet_the_contract() -> None:
    # Pyright checks this assignment against the Protocol's fields.
    kinds: list[type[Candidate]] = [VectorCandidate, LexicalCandidate]

    assert len(kinds) == 2
