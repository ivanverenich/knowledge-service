"""Vector search checks that need no database."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, InvalidAuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingModelIdentity, Vector
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.language import Language
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.filters import metadata_predicate
from knowledge_service.retrieval.filters import (
    FilterField,
    FilterOperator,
    MetadataCondition,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    DistanceMetric,
    InvalidCandidateLimit,
    InvalidVectorQuery,
    VectorCandidate,
    VectorSearchResult,
    search_similar_chunks,
    vector_search_statement,
)

IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
USER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
GROUP = GroupId.parse("00000000-0000-0000-0000-0000000000b1")
DOCUMENT = DocumentId.parse("00000000-0000-0000-0000-0000000000c1")
MARKER = "zebramarker"
# A media type that must reach the database only as a bound parameter.
INJECTED_MEDIA_TYPE = "text/x-zebramarker"


def test_a_context_names_one_user_and_only_groups() -> None:
    context = AuthorizationContext(user_id=USER, group_ids=frozenset({GROUP}))

    assert context.group_ids == {GROUP}
    assert AuthorizationContext(user_id=USER).group_ids == frozenset()


@pytest.mark.parametrize(
    ("user_id", "group_ids"),
    [
        (USER.value, frozenset[GroupId]()),
        (GroupId(USER.value), frozenset[GroupId]()),
        (USER, [GROUP]),
        (USER, frozenset({USER})),
        (USER, frozenset({GROUP.value})),
    ],
)
def test_a_context_refuses_other_types(user_id: object, group_ids: object) -> None:
    with pytest.raises(InvalidAuthorizationContext):
        AuthorizationContext(
            user_id=cast(UserId, user_id),
            group_ids=cast(frozenset[GroupId], group_ids),
        )


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ((1.0, 0.0), 5),
        ((1.0, 0.0, 0.0, 0.0), 5),
        ((float("nan"), 0.0, 0.0), 5),
        ((float("inf"), 0.0, 0.0), 5),
        ((0.0, 0.0, 0.0), 5),
        ((1.0, 0.0, 0.0), 0),
        ((1.0, 0.0, 0.0), MAX_CANDIDATES + 1),
        ((1.0, 0.0, 0.0), True),
        # Each of these once crashed with a TypeError or an OverflowError.
        ((10**400, 0.0, 0.0), 5),
        (("1", 0.0, 0.0), 5),
        ((None, 0.0, 0.0), 5),
        ((True, 0.0, 0.0), 5),
        ([1.0, 0.0, 0.0], 5),
        (None, 5),
        (iter((1.0, 0.0, 0.0)), 5),
    ],
)
async def test_a_bad_query_is_refused_before_the_database(
    query: object, limit: int
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidVectorQuery):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=cast(Vector, query),
            context=AuthorizationContext(user_id=USER),
            limit=limit,
        )


def test_the_reader_and_the_query_are_bound_parameters() -> None:
    statement = vector_search_statement(
        space_id=EmbeddingSpaceId.new(),
        query=(0.25, 0.5, 0.75),
        eligibility=eligible_chunks_predicate(
            AuthorizationContext(user_id=USER, group_ids=frozenset({GROUP}))
        ),
        restriction=metadata_predicate(
            (
                MetadataCondition(
                    FilterField.MEDIA_TYPE,
                    FilterOperator.ANY_OF,
                    frozenset({INJECTED_MEDIA_TYPE}),
                ),
                MetadataCondition(
                    FilterField.LANGUAGE,
                    FilterOperator.ANY_OF,
                    frozenset({Language.UKRAINIAN}),
                ),
                MetadataCondition(
                    FilterField.DOCUMENT,
                    FilterOperator.ANY_OF,
                    frozenset({DOCUMENT}),
                ),
            )
        ),
        limit=5,
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    values = list(compiled.params.values())

    assert str(USER.value) not in sql
    assert str(GROUP.value) not in sql
    assert "0.25" not in sql
    assert INJECTED_MEDIA_TYPE not in sql
    assert "'uk'" not in sql
    assert str(DOCUMENT.value) not in sql
    assert USER.value in values
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values
    assert [DOCUMENT.value] in values


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=0.5,
        external_id=f"{MARKER}.txt",
        title=MARKER,
        heading_path=MARKER,
        source_anchor=MARKER,
        text=MARKER,
    )

    result = VectorSearchResult(
        metric=DistanceMetric.COSINE,
        limit=1,
        candidates=(candidate,),
        truncated=False,
    )

    assert MARKER not in repr(candidate)
    assert MARKER not in repr(result)


@pytest.mark.parametrize(
    "limit",
    [0, -1, MAX_CANDIDATES + 1, 10**30, True, False, 1.0, 5.5, None, "5"],
)
async def test_an_invalid_k_is_refused_before_the_database(limit: object) -> None:
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidCandidateLimit):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=(1.0, 0.0, 0.0),
            context=AuthorizationContext(user_id=USER),
            limit=cast(int, limit),
        )


async def test_a_bad_limit_is_reported_before_a_bad_query() -> None:
    unused = cast(AsyncConnection, object())

    # InvalidCandidateLimit is an InvalidVectorQuery, so T06 callers still
    # catch it; a caller that sets k can tell it apart.
    with pytest.raises(InvalidCandidateLimit):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=(0.0, 0.0, 0.0),
            context=AuthorizationContext(user_id=USER),
            limit=0,
        )
