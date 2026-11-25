"""The answer stream's Evidence from real PostgreSQL, searched as the reader.

`VectorEvidenceSource` embeds the Question and runs the vector search with
the request's Authorization Context. These tests plant a hidden Chunk that is
an exact match for the Question and check it never becomes Evidence, never
reaches the model's prompt, and never reaches the client.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingUsage,
    InvalidEmbedding,
)
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.settings import Settings
from test_eligibility_predicate import changed_after_checking, wider_than_checked
from test_streaming import QUESTION, REQUEST_ID, Model, streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
)

MARKER = "SEEDED-SECRET-77c0"
OTHER_USER = UserId(UUID(int=0xB0B))
READERS_GROUP = next(iter(READER.group_ids))
OTHER_GROUP = GroupId(UUID(int=0xF))


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    """A migrated database with one Source and a current Embedding Space."""
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


class FixedModel:
    """An EmbeddingModel that embeds every Question as `QUERY`.

    `replies_as` and `vectors` make it answer like a broken provider.
    """

    identity: EmbeddingModelIdentity = IDENTITY
    limits = EmbeddingLimits(max_inputs=4, max_input_tokens=64, max_batch_tokens=256)

    def __init__(
        self,
        *,
        replies_as: EmbeddingModelIdentity = IDENTITY,
        vectors: int | None = None,
    ) -> None:
        self.calls = 0
        self.timeouts: list[float] = []
        self.replies_as = replies_as
        self.vectors = vectors

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def embed(
        self, texts: Sequence[str], *, request_id: UUID, timeout_seconds: float
    ) -> EmbeddingBatch:
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        count = len(texts) if self.vectors is None else self.vectors
        return EmbeddingBatch(
            identity=self.replies_as,
            vectors=tuple(QUERY for _ in range(count)),
            usage=EmbeddingUsage(input_tokens=len(texts), latency_ms=0),
        )


class RecordingModel(Model):
    """The streaming test model, keeping every prompt it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[EvidencePrompt] = []

    def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> Any:
        self.prompts.append(prompt)
        return super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        )


def source(runtime: DatabaseRuntime, model: Any = None, **settings: Any) -> Any:
    return VectorEvidenceSource(
        runtime, model or FixedModel(), embed_timeout_seconds=5, **settings
    )


async def plant(runtime: DatabaseRuntime) -> tuple[Any, Any]:
    """A hidden exact match with the marker, and a farther granted Chunk."""
    hidden = await a_document(
        runtime,
        f"{MARKER}.txt",
        readers=[Subject.user(OTHER_USER), Subject.group(OTHER_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )
    granted = await a_document(
        runtime,
        "granted.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[0.6, 0.8, 0.0],
    )
    return hidden, granted


@pytest.mark.integration
async def test_the_reader_gets_only_evidence_it_may_see(
    runtime: DatabaseRuntime,
) -> None:
    _, granted = await plant(runtime)

    found = await source(runtime, limit=1).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert [item.document_id for item in found.evidence.items] == [granted]
    assert found.signals.eligible == 1
    assert MARKER not in " ".join(item.text for item in found.evidence.items)


@pytest.mark.integration
async def test_a_reader_with_no_grant_finds_nothing(runtime: DatabaseRuntime) -> None:
    await plant(runtime)
    stranger = AuthorizationContext(user_id=UserId(UUID(int=0x5)))

    found = await source(runtime).find(
        QUESTION.text, reader=stranger, request_id=REQUEST_ID
    )

    assert (found.evidence.items, found.signals.eligible) == ((), 0)


@pytest.mark.integration
async def test_the_limit_bounds_the_evidence(runtime: DatabaseRuntime) -> None:
    for index in range(5):
        await a_document(
            runtime,
            f"public-{index}.txt",
            readers=[Subject.public()],
            vector=[1.0, float(index), 0.0],
        )

    found = await source(runtime, limit=3).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert len(found.evidence.items) == found.signals.eligible == 3


@pytest.mark.integration
@pytest.mark.parametrize(
    "reader",
    [
        None,
        {"user_id": READER.user_id},
        READER.user_id,
        wider_than_checked(),
        changed_after_checking("group_ids", set[GroupId]()),
    ],
    ids=["none", "mapping", "user-id", "subclass", "changed"],
)
async def test_a_reader_that_is_not_a_context_is_refused_before_embedding(
    runtime: DatabaseRuntime, reader: object
) -> None:
    model = FixedModel()

    with pytest.raises(InvalidAnswerRequest):
        await source(runtime, model).find(
            QUESTION.text,
            reader=cast(AuthorizationContext, reader),
            request_id=REQUEST_ID,
        )

    assert model.calls == 0


@pytest.mark.integration
async def test_the_question_is_embedded_with_the_configured_timeout(
    runtime: DatabaseRuntime,
) -> None:
    model = FixedModel()

    await VectorEvidenceSource(runtime, model, embed_timeout_seconds=2.5).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert model.timeouts == [2.5]


OTHER_MODEL = EmbeddingModelIdentity(provider="fake", model="older", dimensions=3)


@pytest.mark.integration
@pytest.mark.parametrize(
    "model",
    [FixedModel(replies_as=OTHER_MODEL), FixedModel(vectors=0), FixedModel(vectors=2)],
    ids=["another-model", "no-vector", "two-vectors"],
)
async def test_a_model_reply_that_is_not_one_vector_of_its_own_is_refused(
    runtime: DatabaseRuntime, model: FixedModel
) -> None:
    await plant(runtime)

    with pytest.raises(InvalidEmbedding):
        await source(runtime, model).find(
            QUESTION.text, reader=READER, request_id=REQUEST_ID
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"limit": 2.0},
        {"embed_timeout_seconds": 0},
        {"embed_timeout_seconds": float("nan")},
        {"embed_timeout_seconds": float("inf")},
        {"embed_timeout_seconds": True},
    ],
    ids=[
        "zero",
        "over-max",
        "bool-limit",
        "float-limit",
        "zero-timeout",
        "nan",
        "inf",
        "bool-timeout",
    ],
)
def test_a_source_with_a_bad_limit_or_timeout_is_refused(
    settings: dict[str, Any],
) -> None:
    arguments: dict[str, Any] = {"embed_timeout_seconds": 5, **settings}
    with pytest.raises(ValueError, match="must be"):
        VectorEvidenceSource(cast(Any, None), FixedModel(), **arguments)


@pytest.mark.integration
async def test_a_hidden_exact_match_never_reaches_the_model_or_the_client(
    runtime: DatabaseRuntime,
) -> None:
    await plant(runtime)
    model = RecordingModel()
    app = create_app(
        streamer=streamer(model, source=source(runtime)), authenticator=TRUSTED
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/questions", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )

    assert response.status_code == 200
    assert len(model.prompts) == 1
    assert "granted" in model.prompts[0].input
    assert MARKER not in model.prompts[0].input
    assert MARKER not in response.text
