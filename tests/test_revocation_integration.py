"""A grant revoked in the middle of a Conversation: every later read fails closed.

alice asked about the roadmap once and got an Answer citing it. She asks
again, and while the model is writing, a Synchronization Run takes the
roadmap away from engineering. From then on, at every consistency point,
she must get nothing from it: the current Answer is withdrawn, her History
withholds the earlier Answer, search finds nothing, and a new Question
abstains without the model.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import AbstentionReason, EvidenceSignals
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.history import load_history
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId
from knowledge_service.identity.context import Caller
from knowledge_service.ingestion import synchronization
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import SourceAccess
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel
from test_streaming import QUESTION, Model, streamer
from test_synchronization_integration import (
    ALICE,
    DIRECTORY,
    INSERT_SOURCE,
    LATER,
    OBSERVED_AT,
    SOURCE_ID,
    OnePage,
    a_record,
)

ENGINEERING = SourceAccess(public=False, principals=("group:engineering",))
CAROL_ONLY = SourceAccess(public=False, principals=("user:carol@example.test",))
LIMITS = ConversationLimits()
SEARCH_WORDS = "roadmap policy"


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


class AsAlice:
    """An authenticator that makes every request alice's, a `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=ALICE, roles=frozenset({ApplicationRole.USER}))


class WordSearch:
    """An Evidence source that searches the stored words as the reader."""

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self.runtime = runtime

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            found = await search_lexical_chunks(
                connection, text=SEARCH_WORDS, context=reader, limit=5
            )
        evidence = build_evidence(found.candidates)
        return FoundEvidence(evidence, EvidenceSignals(len(evidence.items), None, None))


async def sync(
    runtime: DatabaseRuntime, folder: Path, access: SourceAccess, at: datetime
) -> None:
    await synchronize_source(
        runtime,
        DirectorySource(folder, access=access),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


async def ask(runtime: DatabaseRuntime, model: Model) -> StreamedAnswer:
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, WordSearch(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=AsAlice(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer any"},
        )
    reader = AnswerEventReader()
    reader.feed(response.content)
    return reader.close()


async def roadmap_reference(runtime: DatabaseRuntime) -> MessageReference:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    chunks.c.chunk_id,
                    documents.c.document_id,
                    documents.c.content_version,
                )
                .join(documents, documents.c.document_id == chunks.c.document_id)
                .where(documents.c.external_id == "roadmap.md")
            )
        ).one()
    return MessageReference(
        chunk_id=ChunkId(row.chunk_id),
        document_id=DocumentId(row.document_id),
        content_version=ContentVersion(row.content_version),
    )


async def earlier_turn(runtime: DatabaseRuntime) -> ConversationId:
    """alice's Conversation, with one earlier Answer citing the roadmap."""
    reference = await roadmap_reference(runtime)
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=ALICE.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await add_turn(
            connection,
            owner_id=ALICE.user_id,
            conversation_id=started.conversation_id,
            question="What is on the roadmap?",
            reply="The roadmap.md policy.",
            outcome=MessageOutcome.ANSWERED,
            references=(reference,),
            now=OBSERVED_AT,
            limits=LIMITS,
        )
    return started.conversation_id


async def earlier_answers(
    runtime: DatabaseRuntime, conversation_id: ConversationId
) -> list[str | None]:
    async with runtime.transaction() as connection:
        history = await load_history(
            connection,
            ALICE,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )
    assert history is not None
    return [turn.answer for turn in history.turns]


@pytest.mark.integration
async def test_a_grant_revoked_mid_conversation_fails_every_later_read_closed(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]

    async def revoke() -> None:
        # A Synchronization Run commits while the model is still writing.
        await sync(runtime, tmp_path, CAROL_ONLY, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=revoke))
    after = Model()
    later = await ask(runtime, after)
    async with runtime.transaction() as connection:
        searched = await search_lexical_chunks(
            connection, text=SEARCH_WORDS, context=ALICE, limit=5
        )

    # The Answer being written when the grant went is withdrawn.
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert during.citations == ()
    # Every later read fails closed.
    assert await earlier_answers(runtime, conversation_id) == [None]
    assert searched.candidates == ()
    assert (later.outcome, later.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert after.calls == 0


@pytest.mark.integration
async def test_a_grant_kept_by_the_same_run_still_answers(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    # The control case: the same run, but engineering keeps the roadmap.
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)

    async def resync() -> None:
        await sync(runtime, tmp_path, ENGINEERING, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=resync))

    assert during.outcome is StreamOutcome.ANSWERED
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]


@pytest.mark.integration
async def test_a_question_asked_while_the_run_still_parses_already_fails_closed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    # notes.md comes first on the page and changes its text; roadmap.md
    # comes second and drops engineering. alice asks about the roadmap
    # while the run is still working on notes.md.
    # Plain words too: the search reads "roadmap.md" as one file name.
    roadmap = "The roadmap.md policy. Roadmap policy.\n"
    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nOld.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "group:engineering"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    # Without a revocation, alice's question is answered.
    before = await ask(runtime, CitingModel("roadmap.md"))
    assert before.outcome is StreamOutcome.ANSWERED
    answers: list[StreamedAnswer] = []
    reconcile = synchronization.reconcile_document

    async def reconciling(runtime_: Any, normalized: Any, **kwargs: Any) -> Any:
        if normalized.provenance.external_id == "notes.md":
            answers.append(await ask(runtime, CitingModel("roadmap.md")))
        return await reconcile(runtime_, normalized, **kwargs)

    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nNew.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "user:carol@example.test"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    (during,) = answers
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
