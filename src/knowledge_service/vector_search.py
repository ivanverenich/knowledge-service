"""Find the Chunks nearest to a query vector that one reader may see.

Eligibility is part of the same SQL statement as the ranking: the database
drops every Chunk whose Document is unavailable or not granted to the reader
before it orders the rest by distance. An ineligible Chunk is therefore never
ranked, never counted toward the limit, and never leaves the database.

What a caller can rely on in a `VectorSearchResult`:

- **Limit.** `limit` is top-k: an int from 1 to `MAX_CANDIDATES`. Anything
  else raises `InvalidCandidateLimit` before the database is used.
- **Distance.** Cosine distance, between 0 and 2; lower is nearer. 0 is the
  same direction as the query, 1 is unrelated, 2 is the opposite one.
- **Order.** Nearest first, then by Chunk ID when distances are equal, so
  the same data and query always give the same order. `rank` counts from 1.
- **Fewer than k.** When fewer eligible Chunks exist, all of them are
  returned and `truncated` is False. `truncated` is True only when more
  eligible Chunks exist beyond the limit.
- **Filters.** Optional `filters` (see `retrieval_filters`) narrow the
  eligible Chunks further, in the same WHERE, so every guarantee above,
  `truncated` included, counts only Chunks that are eligible and match.
- **Empty.** No eligible Chunk is a normal, empty result, not an error.
  A Chunk whose stored vector has no direction (all zeros) has no distance,
  so it is never a Candidate.

Every stored vector and every query has length 1, so distances are right
in pgvector's 32-bit floats. Ties are exact: two vectors in the same
direction can still differ in the last digits of their distances, and are
then ordered by those digits, not by Chunk ID.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings import (
    DistanceMetric,
    EmbeddingModelIdentity,
    Vector,
    unit_length,
)
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    SourceId,
)
from knowledge_service.persistence import (
    chunk_embeddings,
    chunks,
    documents,
    eligible_chunks_predicate,
    load_current_embedding_space_id,
    metadata_predicate,
)
from knowledge_service.retrieval_filters import MetadataCondition, checked_conditions

# The largest top-k one search may ask for, so its rows stay bounded.
MAX_CANDIDATES = 100


class VectorSearchError(Exception):
    """Base error for a vector search."""


class InvalidVectorQuery(VectorSearchError):
    """The query vector or the limit cannot be searched; nothing was read."""


class InvalidCandidateLimit(InvalidVectorQuery):
    """The limit is not an int from 1 to `MAX_CANDIDATES`; nothing was read."""


class NoCurrentEmbeddingSpace(VectorSearchError):
    """The current Embedding Space does not hold the query's model identity."""


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """One eligible Chunk, where it came from, and its distance to the query.

    `rank` is its 1-based position in the result. `distance` is the cosine
    distance, from 0 for the same direction to 2 for the opposite one. The
    fields holding Document text are kept out of repr, so logging a
    Candidate never shows them.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    distance: float
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """The Candidates of one search and how to read them.

    See the module docstring for the guarantees on order, distance, and
    fewer-than-k results.
    """

    metric: DistanceMetric
    limit: int
    candidates: tuple[VectorCandidate, ...]
    truncated: bool


async def search_similar_chunks(
    connection: AsyncConnection,
    *,
    identity: EmbeddingModelIdentity,
    query: Vector,
    context: AuthorizationContext,
    limit: int,
    filters: Sequence[MetadataCondition] = (),
) -> VectorSearchResult:
    """Return up to `limit` eligible Chunks, nearest first.

    `identity` names the model that embedded the query; only vectors of the
    current Embedding Space are compared, and it must hold that identity.
    Raises `InvalidCandidateLimit` for a bad limit, `InvalidVectorQuery` for
    a bad vector, and `NoCurrentEmbeddingSpace` when the spaces differ.
    """
    _check_limit(limit)
    unit_query = _unit_query(query, dimensions=identity.dimensions)
    conditions = checked_conditions(filters)
    space_id = await load_current_embedding_space_id(connection, identity)
    if space_id is None:
        raise NoCurrentEmbeddingSpace("the current embedding space is another model")
    statement = vector_search_statement(
        space_id=space_id,
        query=unit_query,
        eligibility=eligible_chunks_predicate(context),
        restriction=metadata_predicate(conditions),
        # One row more than asked shows whether more eligible Chunks exist.
        limit=limit + 1,
    )
    records = (await connection.execute(statement)).mappings().all()
    return VectorSearchResult(
        metric=DistanceMetric.COSINE,
        limit=limit,
        candidates=tuple(
            _candidate_from(dict(record), rank=index)
            for index, record in enumerate(records[:limit], start=1)
        ),
        truncated=len(records) > limit,
    )


def vector_search_statement(
    *,
    space_id: EmbeddingSpaceId,
    query: Vector,
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet `eligibility`.

    The predicate sits in the same WHERE as the space, so PostgreSQL applies
    it before ORDER BY and LIMIT. Ties in distance are broken by Chunk ID so
    that equal inputs always return the same order. A stored vector with no
    direction gives a NaN distance, which is not a distance, so its row is
    left out; PostgreSQL treats NaN as equal to NaN.
    """
    distance = chunk_embeddings.c.embedding.cosine_distance(list(query))
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            distance.label("distance"),
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(chunk_embeddings)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id == space_id.value,
            eligibility,
            restriction,
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )


def _check_limit(limit: int) -> None:
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidCandidateLimit(
            f"limit must be an int between 1 and {MAX_CANDIDATES}"
        )


def _unit_query(
    query: Vector,
    *,
    dimensions: int,
) -> Vector:
    """Check the query and return it scaled to length 1, like stored vectors."""
    if type(query) is not tuple:
        raise InvalidVectorQuery("query must be a tuple of numbers")
    if len(query) != dimensions:
        raise InvalidVectorQuery(
            f"query has {len(query)} dimensions, expected {dimensions}"
        )
    values = [_finite(value) for value in query]
    # Cosine distance to a zero vector is undefined, so every row would tie.
    if not any(values):
        raise InvalidVectorQuery("query must not be all zeros")
    return unit_length(values)


def _finite(value: object) -> float:
    # bool is an int, and a string may look numeric; neither is a number here.
    if type(value) is not float and type(value) is not int:
        raise InvalidVectorQuery("query must hold numbers")
    try:
        number = float(value)
    except OverflowError:
        raise InvalidVectorQuery("query must hold finite numbers") from None
    if not math.isfinite(number):
        raise InvalidVectorQuery("query must hold finite numbers")
    return number


def _candidate_from(record: Mapping[str, object], *, rank: int) -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        distance=cast(float, record["distance"]),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=cast(str, record["text"]),
    )
