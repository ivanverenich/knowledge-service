"""Find a Question's Evidence in PostgreSQL by meaning, as one reader.

`VectorEvidenceSource` is the `EvidenceSource` the answer stream uses over
the real database. It embeds the Question, then runs the vector search with
the request's Authorization Context, so the permission check happens inside
the same SQL statement that ranks Chunks: a Chunk the reader may not see is
never ranked, never counted, and never leaves the database, however close
its vector is.

It does not rerank, so its signals carry no score: the abstention policy can
only tell whether anything eligible was found, and a policy with a score
threshold refuses every request it judges.
"""

import math
from uuid import UUID

from knowledge_service.access import (
    AuthorizationContext,
    InvalidAuthorizationContext,
    checked_reader,
)
from knowledge_service.answering.abstention import EvidenceSignals
from knowledge_service.answering.evidence import MAX_EVIDENCE, build_evidence
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel, InvalidEmbedding
from knowledge_service.retrieval.vector_search import search_similar_chunks

DEFAULT_LIMIT = 8


class VectorEvidenceSource:
    """Evidence for one Question: the nearest Chunks the reader may see."""

    def __init__(
        self,
        runtime: DatabaseRuntime,
        model: EmbeddingModel,
        *,
        limit: int = DEFAULT_LIMIT,
        embed_timeout_seconds: float,
    ) -> None:
        # bool is an int, so `True` would otherwise pass as a limit of 1.
        if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE:
            raise ValueError(f"limit must be an int between 1 and {MAX_EVIDENCE}")
        if (
            type(embed_timeout_seconds) not in (int, float)
            or not math.isfinite(embed_timeout_seconds)
            or embed_timeout_seconds <= 0
        ):
            raise ValueError("embed_timeout_seconds must be a positive number")
        self._runtime = runtime
        self._model = model
        self._limit = limit
        self._timeout = float(embed_timeout_seconds)

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        """The nearest eligible Chunks as Evidence, and how many there were.

        The reader is checked, fields included, before the Question is
        embedded, so a request without a sound Authorization Context costs
        no provider call.
        """
        try:
            reader = checked_reader(reader)
        except InvalidAuthorizationContext:
            raise InvalidAnswerRequest(
                "reader must be an AuthorizationContext"
            ) from None
        batch = await self._model.embed(
            [question], request_id=request_id, timeout_seconds=self._timeout
        )
        # Search only with the model's own space and exactly one vector: a
        # reply naming another model would compare against vectors this
        # Question was never embedded for.
        if batch.identity != self._model.identity or len(batch.vectors) != 1:
            raise InvalidEmbedding("the model must return one vector of its own")
        async with self._runtime.transaction() as connection:
            result = await search_similar_chunks(
                connection,
                identity=batch.identity,
                query=batch.vectors[0],
                context=reader,
                limit=self._limit,
            )
        return FoundEvidence(
            evidence=build_evidence(result.candidates),
            signals=EvidenceSignals(
                eligible=len(result.candidates), top_score=None, reranker=None
            ),
        )
