"""Sparse search without a database: the index, and refusals before any read."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from fakes import FAKE_VOCABULARY
from knowledge_service.access import AuthorizationContext
from knowledge_service.embeddings.port import EmbeddingMalformedResponse
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)
from knowledge_service.identifiers import ChunkId, UserId
from knowledge_service.retrieval.sparse_search import (
    InvalidSparseQuery,
    InvalidSparseWeights,
    SparseIndexMismatch,
    search_sparse_chunks,
    sparse_index,
)

IDENTITY = SparseModelIdentity(
    provider="fake", model="fake-sparse", vocabulary_size=FAKE_VOCABULARY
)
READER = AuthorizationContext(user_id=UserId(UUID(int=1)))
FIRST = ChunkId(UUID(int=1))
SECOND = ChunkId(UUID(int=2))


class NoDatabase:
    """Fails the test if a search touches the database."""

    async def execute(self, *_: object) -> None:
        raise AssertionError("the database was used")


NO_DATABASE = cast(AsyncConnection, NoDatabase())


def test_the_index_keeps_postings_by_token_in_chunk_order() -> None:
    index = sparse_index(
        IDENTITY,
        {SECOND: ((5, 0.5), (9, 0.25)), FIRST: ((5, 1.0),)},
    )

    assert index.postings == {5: ((FIRST, 1.0), (SECOND, 0.5)), 9: ((SECOND, 0.25),)}
    assert (index.chunks, index.weights) == (2, 3)


def test_valid_sparse_weights_become_pairs_and_may_be_empty() -> None:
    vectors = checked_sparse_vectors(
        [[[3, 0.5], [7, 1.5]], []], expected=2, vocabulary_size=10
    )

    assert vectors == (((3, 0.5), (7, 1.5)), ())


def test_a_wrong_number_of_sparse_vectors_is_refused() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 0.5]]], expected=2, vocabulary_size=10)


@pytest.mark.parametrize(
    "limit", [0, 101, True, 1.0], ids=["zero", "too many", "bool", "float"]
)
async def test_a_bad_limit_is_refused_before_the_database(limit: object) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=IDENTITY,
            context=READER,
            limit=cast(int, limit),
        )


async def test_weights_from_another_model_are_refused_before_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})
    other = SparseModelIdentity(
        provider="local", model="BAAI/bge-m3@other", vocabulary_size=FAKE_VOCABULARY
    )

    with pytest.raises(SparseIndexMismatch):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=other,
            context=READER,
            limit=10,
        )


@pytest.mark.parametrize(
    "query",
    [
        [(5, 1.0)],
        ((5, -1.0),),
        ((5, float("nan")),),
        ((FAKE_VOCABULARY, 1.0),),
        ((5, 1.0), (5, 2.0)),
        ((5, 1),),
        ((5,),),
    ],
    ids=[
        "a list",
        "negative weight",
        "not a number",
        "outside the vocabulary",
        "repeated token",
        "int weight",
        "no weight",
    ],
)
async def test_a_malformed_query_is_refused_before_the_database(
    query: object,
) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=cast(SparseVector, query),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


async def test_a_query_sharing_no_token_finds_nothing_without_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    result = await search_sparse_chunks(
        NO_DATABASE,
        index=index,
        query=((6, 1.0),),
        identity=IDENTITY,
        context=READER,
        limit=10,
    )

    assert (result.candidates, result.truncated) == ((), False)


@pytest.mark.parametrize(
    "vector",
    [
        ((5, float("nan")),),
        ((5, -3.0),),
        ((5, 1e300),),
        ((FAKE_VOCABULARY, 1.0),),
        ((9, 1.0), (5, 1.0)),
    ],
    ids=["not a number", "negative", "huge", "outside the vocabulary", "unordered"],
)
def test_the_index_refuses_weights_the_port_does_not_allow(vector: object) -> None:
    with pytest.raises(InvalidSparseWeights):
        sparse_index(IDENTITY, {FIRST: cast(SparseVector, vector)})


async def test_a_weight_too_large_to_multiply_safely_is_refused() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.7e308),),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


def test_a_weight_over_the_bound_is_malformed() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 1e6]]], expected=1, vocabulary_size=10)
