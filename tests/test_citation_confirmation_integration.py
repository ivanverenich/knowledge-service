"""The answer stream confirms its Citations against PostgreSQL, as the reader.

Each test runs the whole stream over HTTP with the real `CurrentCitations`.
A model cites the first Evidence it is shown; before it replies, a test can
change the database, as a Synchronization Run might while the model writes.
Evidence the reader may not see must never reach the model, so none of its
text is streamed; a Citation the reader may no longer see withdraws the
Answer, which ends with the same decision and message as an empty search.
"""

import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.abstention import AbstentionReason, EvidenceSignals
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import UserId
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_streaming import QUESTION, USAGE, Model, streamer
from test_vector_evidence_integration import FixedModel
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
    plain_text,
)

BOB = AuthorizationContext(user_id=UserId(UUID(int=0xB0B)))
READERS_GROUP = next(iter(READER.group_ids))
LATER = OBSERVED_AT.replace(hour=OBSERVED_AT.hour + 1)
EVIDENCE_ID = re.compile(r'"id": "(E[0-9a-f]{8})"')


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
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


class CitingModel(Model):
    """Answers with the text of a file and cites the first Evidence shown.

    `before_reply` runs after the text has streamed and before the reply
    completes, as a change committed while the model is still writing.
    """

    def __init__(
        self,
        name: str,
        before_reply: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self.answer = f"The {name} policy."
        self.before_reply = before_reply

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        found = EVIDENCE_ID.search(prompt.input)
        assert found is not None
        final = (
            '{"answer": "' + self.answer + '", "citations": ["' + found[1] + '"],'
            ' "abstained": false, "reason": null}'
        )
        yield final[:-10]
        if self.before_reply is not None:
            await self.before_reply()
        yield final[-10:]
        yield ModelReply(text=final, refused=False, usage=USAGE)


class FoundFor:
    """An Evidence source that returns what another reader's search found.

    It stands for a retrieval bug, or IDs taken from someone else's answer:
    the Evidence is real, but not the requesting reader's to see.
    """

    def __init__(self, runtime: DatabaseRuntime, owner: AuthorizationContext) -> None:
        self.runtime = runtime
        self.owner = owner

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            found = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=self.owner, limit=1
            )
        return FoundEvidence(
            build_evidence(found.candidates), EvidenceSignals(1, None, None)
        )


async def ask(runtime: DatabaseRuntime, model: Model, source: Any) -> StreamedAnswer:
    answer, _ = await ask_raw(runtime, model, source)
    return answer


async def ask_raw(
    runtime: DatabaseRuntime, model: Model, source: Any
) -> tuple[StreamedAnswer, bytes]:
    """The answer as a client rebuilds it, and every byte that was sent."""
    app = create_app(
        streamer=streamer(model, source=source, citations=CurrentCitations(runtime)),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )
    assert response.status_code == 200
    reader = AnswerEventReader()
    reader.feed(response.content)
    return reader.close(), response.content


def vector_source(runtime: DatabaseRuntime) -> VectorEvidenceSource:
    return VectorEvidenceSource(runtime, FixedModel(), embed_timeout_seconds=5)


@pytest.mark.integration
async def test_a_citation_the_reader_may_see_is_sent_with_its_provenance(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    answer = await ask(runtime, CitingModel("roadmap.txt"), vector_source(runtime))

    assert answer.outcome is StreamOutcome.ANSWERED
    (citation,) = answer.citations
    assert citation.title == "roadmap.txt"


@pytest.mark.integration
async def test_another_users_chunk_is_never_cited_to_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    # Bob's Document, found as bob, handed to alice's stream: a valid ID
    # of another User.
    await a_document(
        runtime,
        "SECRET-bob.txt",
        readers=[Subject.user(BOB.user_id)],
        vector=[1.0, 0.0, 0.0],
    )

    model = CitingModel("SECRET-bob.txt")

    answer, sent = await ask_raw(runtime, model, FoundFor(runtime, BOB))

    assert answer.outcome is StreamOutcome.ABSTAINED
    assert answer.reason is AbstentionReason.NO_EVIDENCE
    assert model.calls == 0
    # Not even streamed text: the model was never shown bob's Chunk.
    assert b"SECRET" not in sent


@pytest.mark.integration
async def test_a_grant_revoked_while_the_model_writes_withdraws_the_answer(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    answer = await ask(
        runtime, CitingModel("roadmap.txt", before_reply=revoke), vector_source(runtime)
    )

    assert answer.outcome is StreamOutcome.ABSTAINED
    assert answer.reason is AbstentionReason.NO_EVIDENCE
    assert answer.citations == ()
    assert answer.text == ""


@pytest.mark.integration
async def test_withdrawn_and_empty_end_with_the_same_decision_and_message(
    runtime: DatabaseRuntime,
) -> None:
    # The text streamed before the withdrawal stays sent; the end is the same.
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    withdrawn = await ask(
        runtime, CitingModel("roadmap.txt", before_reply=revoke), vector_source(runtime)
    )
    empty = await ask(runtime, CitingModel("roadmap.txt"), vector_source(runtime))

    assert (withdrawn.outcome, withdrawn.reason, withdrawn.message) == (
        empty.outcome,
        empty.reason,
        empty.message,
    )
