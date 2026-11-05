"""Prototype: find Chunks by BGE-M3 sparse term weights, for one reader.

This is the M05-T10 experiment, not a production branch: no default uses
it, and its index lives in memory for one evaluation run. It answers one
question: does BGE-M3's sparse output find Ukrainian (and English) Chunks
better than PostgreSQL full-text search, and at what cost?

How it works:

- `build_sparse_index` reads every available Chunk, as dense embedding
  does, asks the sparse model for its weights in batches within the
  model's limits, and keeps them as postings: for each token ID, the
  Chunks that hold it and their weights.
- `search_sparse_chunks` scores a query against the postings, a sum of
  weight products over shared tokens, then lets PostgreSQL drop every
  Chunk the reader may not see and order the rest, in one statement.

What a caller can rely on in a `SparseSearchResult`:

- **Eligible.** The scores go to PostgreSQL as parameters, and the
  permission check sits in the same WHERE as the ordering, as in vector
  search: a Chunk the reader may not see is never ranked, never counted,
  and its text never leaves the database. A Chunk deleted after the index
  was built has no row, so it is never returned either.
- **Ranked.** Highest score first, then by Chunk ID; `rank` counts from 1.
  A Chunk that shares no token with the query has no score and is not a
  Candidate, so a query of only unweighted tokens returns nothing.
- **One model.** The query's weights must come from the model that built
  the index; otherwise `SparseIndexMismatch` is raised before the database
  is used.
- **Bounded.** At most `limit` Candidates, an int from 1 to
  `MAX_CANDIDATES`, and `truncated` says whether more eligible Chunks match.
- **Quiet.** Errors name no text, and the fields holding Document text are
  kept out of repr, as in every Candidate.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion, DocumentAvailability
from knowledge_service.embeddings.sparse import (
    MAX_SPARSE_WEIGHT,
    SparseModel,
    SparseModelIdentity,
    SparseVector,
)
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Scores travel as 64-bit floats, so PostgreSQL orders exactly what was summed.
_SCORE = sa.Double[float]()


class SparseSearchError(Exception):
    """Base error for a sparse search."""


class InvalidSparseQuery(SparseSearchError):
    """The query weights or the limit cannot be searched; nothing was read."""


class InvalidSparseWeights(SparseSearchError):
    """A Chunk's weights break the sparse port's rules; nothing was indexed."""


class SparseIndexMismatch(SparseSearchError):
    """The query's weights come from another model than the index's."""


@dataclass(frozen=True, slots=True)
class SparseIndex:
    """Sparse weights of every indexed Chunk, kept by token for scoring.

    `postings` maps a token ID to the Chunks that hold it, by Chunk ID, with
    their weights. `chunks` counts the indexed Chunks and `weights` the
    stored (token, weight) pairs, which is what storing them would cost.
    """

    identity: SparseModelIdentity
    postings: Mapping[int, tuple[tuple[ChunkId, float], ...]] = field(repr=False)
    chunks: int
    weights: int


@dataclass(frozen=True, slots=True)
class SparseCandidate:
    """One eligible Chunk that shares weighted tokens with the query.

    `score` is the sum of weight products over shared tokens; higher is
    better and only the order means anything. The fields holding Document
    text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SparseSearchResult:
    """The Candidates of one sparse search; see the module docstring."""

    limit: int
    candidates: tuple[SparseCandidate, ...]
    truncated: bool


def sparse_index(
    identity: SparseModelIdentity, vectors: Mapping[ChunkId, SparseVector]
) -> SparseIndex:
    """Turn each Chunk's weights into postings by token, in Chunk ID order.

    Every vector is checked like a query, because a model is outside code:
    a NaN, negative, or huge weight raises `InvalidSparseWeights`.
    """
    postings: dict[int, list[tuple[ChunkId, float]]] = defaultdict(list)
    for chunk_id in sorted(vectors, key=lambda chunk: chunk.value):
        checked = _checked_weights(
            vectors[chunk_id], identity.vocabulary_size, InvalidSparseWeights
        )
        for token, weight in checked:
            postings[token].append((chunk_id, weight))
    return SparseIndex(
        identity=identity,
        postings={token: tuple(pairs) for token, pairs in postings.items()},
        chunks=len(vectors),
        weights=sum(len(vector) for vector in vectors.values()),
    )


async def build_sparse_index(
    runtime: DatabaseRuntime,
    model: SparseModel,
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> SparseIndex:
    """Ask `model` for the weights of every available Chunk and index them.

    Restricted Chunks are indexed too, because search must be able to score
    them for the permission check to matter. Raises the model's
    `EmbeddingError` when a batch fails; nothing is kept then.
    """
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(chunks.c.chunk_id, chunks.c.text)
                .select_from(chunks.join(documents))
                .where(documents.c.availability == DocumentAvailability.AVAILABLE.value)
                .order_by(chunks.c.chunk_id)
            )
        ).all()
    ids = [ChunkId(cast(UUID, row.chunk_id)) for row in rows]
    texts = [cast(str, row.text) for row in rows]
    identity = model.sparse_identity
    vectors: dict[ChunkId, SparseVector] = {}
    for start, end in _batches(texts, model):
        weights = await model.encode_sparse(
            texts[start:end],
            request_id=uuid5(request_id, f"sparse {start}"),
            timeout_seconds=timeout_seconds,
        )
        vectors.update(zip(ids[start:end], weights, strict=True))
    return sparse_index(identity, vectors)


async def search_sparse_chunks(
    connection: AsyncConnection,
    *,
    index: SparseIndex,
    query: SparseVector,
    identity: SparseModelIdentity,
    context: AuthorizationContext,
    limit: int,
) -> SparseSearchResult:
    """Return up to `limit` eligible Chunks, highest sparse score first.

    `identity` names the model that weighted the query. Raises
    `InvalidSparseQuery` for a bad limit or query and `SparseIndexMismatch`
    when `identity` is not the index's, both before the database is used.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidSparseQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    if identity != index.identity:
        raise SparseIndexMismatch("the query was weighted by another model")
    checked = _checked_weights(query, identity.vocabulary_size, InvalidSparseQuery)
    scores = _scores(index, checked)
    if not scores:
        return SparseSearchResult(limit=limit, candidates=(), truncated=False)
    statement = sparse_search_statement(
        scores=scores,
        eligibility=eligible_chunks_predicate(context),
        # One row more than asked shows whether more eligible Chunks match.
        limit=limit + 1,
    )
    records = (await connection.execute(statement)).mappings().all()
    return SparseSearchResult(
        limit=limit,
        candidates=tuple(
            _candidate_from(dict(record), rank=position)
            for position, record in enumerate(records[:limit], start=1)
        ),
        truncated=len(records) > limit,
    )


def sparse_search_statement(
    *,
    scores: Mapping[ChunkId, float],
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the best-scored Chunks that meet `eligibility`.

    The scores arrive as two array parameters and become a table with
    `unnest`; the permission check sits in the same WHERE, so PostgreSQL
    drops ineligible Chunks before ORDER BY and LIMIT. Ties in score are
    broken by Chunk ID.
    """
    ids = sorted(scores, key=lambda chunk: chunk.value)
    scored = (
        sa.func.unnest(
            sa.bindparam("chunk_ids", [c.value for c in ids], type_=ARRAY(sa.Uuid())),
            sa.bindparam("scores", [scores[c] for c in ids], type_=ARRAY(_SCORE)),
        )
        .table_valued(sa.column("chunk_id", sa.Uuid()), sa.column("score", _SCORE))
        .render_derived(name="scored", with_types=False)
    )
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            scored.c.score,
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(scored)
        .join(chunks, chunks.c.chunk_id == scored.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(eligibility)
        .order_by(scored.c.score.desc(), chunks.c.chunk_id)
        .limit(limit)
    )


def sparse_branch(
    runtime: DatabaseRuntime, model: SparseModel, index: SparseIndex
) -> BranchSearch:
    """Weight the question with `model`, then search `index`."""

    async def search(request: BranchRequest) -> BranchResult:
        vectors = await model.encode_sparse(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_sparse_chunks(
                connection,
                index=index,
                query=vectors[0],
                identity=model.sparse_identity,
                context=request.context,
                limit=request.limit,
            )

    return search


def _batches(texts: Sequence[str], model: SparseModel) -> list[tuple[int, int]]:
    """Cut `texts` into consecutive (start, end) runs within the model's limits."""
    limits = model.limits
    runs: list[tuple[int, int]] = []
    start = 0
    while start < len(texts):
        end, total = start, 0
        while end < len(texts) and end - start < limits.max_inputs:
            tokens = model.count_tokens(texts[end])
            if end > start and total + tokens > limits.max_batch_tokens:
                break
            total += tokens
            end += 1
        runs.append((start, end))
        start = end
    return runs


def _checked_weights(
    value: object, vocabulary_size: int, error: type[SparseSearchError]
) -> SparseVector:
    """Return `value` if it keeps the sparse port's rules, else raise `error`."""
    if type(value) is not tuple:
        raise error("weights must be a tuple of (token, weight) pairs")
    previous = -1
    for pair in cast(tuple[object, ...], value):
        if type(pair) is not tuple or len(cast(tuple[object, ...], pair)) != 2:
            raise error("weights must be a tuple of (token, weight) pairs")
        token, weight = cast(tuple[object, object], pair)
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise error("every token must be an int in the vocabulary")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise error(f"every weight must be above 0 and at most {MAX_SPARSE_WEIGHT}")
        if token <= previous:
            raise error("tokens must be in ascending order, each once")
        previous = token
    return cast(SparseVector, value)


def _scores(index: SparseIndex, query: SparseVector) -> dict[ChunkId, float]:
    """Sum weight products over shared tokens, in a fixed order every time."""
    scores: dict[ChunkId, float] = defaultdict(float)
    for token, weight in query:
        for chunk_id, chunk_weight in index.postings.get(token, ()):
            scores[chunk_id] += weight * chunk_weight
    return dict(scores)


def _candidate_from(record: Mapping[str, object], *, rank: int) -> SparseCandidate:
    return SparseCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        score=cast(float, record["score"]),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=cast(str, record["text"]),
    )
