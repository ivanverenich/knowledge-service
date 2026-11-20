"""The authorization adversarial suite: one set of scenarios, every interface.

Each seed of `authorization_scenarios.scenario` is published into real
PostgreSQL, then every person probes every interface a reader can reach,
in English and in Ukrainian:

- **retrieval:** vector search, lexical search, and hybrid retrieval, also
  cut to one result, so `truncated` must count only eligible Chunks;
- **answer:** the Evidence source and the answer stream over HTTP, with a
  model that cites everything it is shown and repeats every marker in its
  prompt, so anything it was shown would reach the client;
- **citation:** the Citation and Evidence checks, given the real
  identifiers of every Document, captured while it could still be read:
  Documents of other people, revoked ones, deleted ones (direct IDs);
- **Conversation:** another person's Conversation and History by ID, and
  the owner's own History once a Document it cited is revoked;
- **revocation race:** a grant taken away, or a Document deleted, while the
  model writes.

Each probe must return exactly the Documents `may_read` allows, where it
returns Documents at all. No marker of a Document the reader may not see,
and none of its identifiers, may appear in what a probe returns, in the
model's prompt, or in the bytes sent; no marker may appear in any field of
any log record or in any audit row. Agent tools do not exist yet; when they
do (M11), they join this suite as another probe.
"""

import dataclasses
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import (
    ENGINEERING,
    LATER,
    PROBES,
    Fate,
    Person,
    Scenario,
    ScenarioAuthenticator,
    Secret,
    leaked,
    scenario,
)
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.audit_trail import DatabaseAuditTrail
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.current_citations import (
    CurrentCitations,
    confirm_citations,
    evidence_still_visible,
)
from knowledge_service.answering.evidence import Evidence, EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelReply,
)
from knowledge_service.answering.history import load_history
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction, AuditDecision
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ConversationId, DocumentId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.audit import load_request_audit_events
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.conversations import (
    add_turn,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.retrieval.hybrid import (
    RetrievalBranch,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_streaming import USAGE, Model, streamer
from test_vector_evidence_integration import FixedModel
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_space,
)

SEEDS = range(6)
LIMIT = 20
LIMITS = ConversationLimits()
REQUEST_ID = UUID("00000000-0000-0000-0000-0000000000f1")
EVIDENCE_ID = re.compile(r'"id": "(E[0-9a-f]{8})"')
MARKER = re.compile(r"(?:mark|twin)\d+s\d+")
# A reader no person is: it sees every Document once, to capture its IDs.
SNAPSHOT = UserId(UUID(int=0x5AA9))
GRANTED_AT = OBSERVED_AT + timedelta(minutes=1)


@dataclasses.dataclass(frozen=True, slots=True)
class Published:
    """A scenario as stored: each marker's Document and captured Evidence."""

    scenario: Scenario
    documents: dict[str, DocumentId]
    evidence: dict[str, Evidence]

    def markers(self, documents: set[DocumentId]) -> set[str]:
        by_id = {document: marker for marker, document in self.documents.items()}
        return {by_id[document] for document in documents}

    def secrets_of(self, markers: set[str]) -> set[str]:
        """Markers, and the Document and Chunk IDs behind them, as text."""
        found = set(markers)
        for marker in markers:
            found.add(str(self.documents[marker].value))
            found.add(str(self.evidence[marker].chunk_id.value))
        return found


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


def normalized(secret: Secret) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=secret.external_id,
            version="v1",
            media_type="text/plain",
            content=secret.text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title=secret.title, modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(runtime: DatabaseRuntime, case: Scenario) -> Published:
    """Store every Document and capture its Evidence while it can be read.

    Each Document is first readable by `SNAPSHOT` too, so its real
    identifiers can be captured, as a client that once read it holds them.
    Then it gets only its real readers, and is revoked or deleted as the
    scenario says.
    """
    documents: dict[str, DocumentId] = {}
    for secret in case.secrets:
        reconciled = await reconcile_document(
            runtime,
            normalized(secret),
            readers=[*secret.readers(), Subject.user(SNAPSHOT)],
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
                vectors={chunk.chunk_id: QUERY for chunk in stored},
                created_at=OBSERVED_AT,
            )
        documents[secret.marker] = document_id
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=AuthorizationContext(user_id=SNAPSHOT),
            limit=LIMIT,
        )
    captured = {
        item.document_id: item for item in build_evidence(found.candidates).items
    }
    evidence = {marker: captured[document] for marker, document in documents.items()}
    for secret in case.secrets:
        revoked = secret.fate is Fate.REVOKED
        await reconcile_document(
            runtime,
            normalized(secret),
            readers=[] if revoked else secret.readers(),
            observed_at=OBSERVED_AT + LATER if revoked else GRANTED_AT,
        )
    # A pass that no longer sees the deleted ones tombstones them; their
    # Chunks stay stored until a purge Job runs, which never runs here.
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={s.external_id for s in case.secrets if s.fate is not Fate.DELETED},
        observed_at=OBSERVED_AT + LATER,
    )
    return Published(case, documents, evidence)


def exposed(*items: Any) -> list[object]:
    """Every field of every returned item, so nothing can hide a marker."""
    seen: list[object] = []
    for item in items:
        if dataclasses.is_dataclass(item):
            for field in dataclasses.fields(item):
                seen.extend(exposed(getattr(item, field.name)))
        elif isinstance(item, (tuple, list)):
            seen.extend(exposed(*cast(tuple[Any, ...], item)))
        else:
            seen.append(item)
    return seen


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every field of every log record, the `extra` ones included."""
    return json.dumps([record.__dict__ for record in caplog.records], default=str)


async def audit_rows(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(sa.text("SELECT to_jsonb(a) FROM audit_events a"))
        ).scalars()
        return json.dumps(list(rows))


async def nothing_recorded(
    runtime: DatabaseRuntime, published: Published, caplog: pytest.LogCaptureFixture
) -> None:
    """No marker in any log record or audit row, whoever may read it."""
    markers = {secret.marker for secret in published.scenario.secrets}
    assert leaked(markers, logged(caplog)) == set()
    assert leaked(markers, await audit_rows(runtime)) == set()


# --- Retrieval -------------------------------------------------------------------

Found = tuple[set[DocumentId], bool | None, list[object]]
Probe = Callable[[DatabaseRuntime, AuthorizationContext, str, int], Awaitable[Found]]


async def vector(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection, identity=IDENTITY, query=QUERY, context=reader, limit=limit
        )
    return {c.document_id for c in found.candidates}, found.truncated, exposed(found)


async def lexical(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    async with runtime.transaction() as connection:
        found = await search_lexical_chunks(
            connection, text=question, context=reader, limit=limit
        )
    return {c.document_id for c in found.candidates}, found.truncated, exposed(found)


async def hybrid(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    found = await retrieve_hybrid(
        question,
        context=reader,
        branches={
            RetrievalBranch.DENSE: dense_branch(runtime, FixedModel()),
            RetrievalBranch.LEXICAL: lexical_branch(runtime),
        },
        limit=limit,
        timeout_seconds=10,
        request_id=REQUEST_ID,
    )
    return {c.document_id for c in found.candidates}, None, exposed(found)


async def evidence(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    source = VectorEvidenceSource(
        runtime, FixedModel(), limit=limit, embed_timeout_seconds=5
    )
    found = await source.find(question, reader=reader, request_id=REQUEST_ID)
    items = found.evidence.items
    return {item.document_id for item in items}, None, exposed(found)


PROBES_BY_NAME: dict[str, Probe] = {
    "vector": vector,
    "lexical": lexical,
    "hybrid": hybrid,
    "evidence": evidence,
}
# Lexical search reads the words of the question's language only; the fake
# embedding model gives every question the same vector.
SAME_LANGUAGE_ONLY = {"lexical"}


def wanted(published: Published, person: Person, name: str, language: str) -> set[str]:
    readable = published.scenario.readable(person)
    if name not in SAME_LANGUAGE_ONLY:
        return readable
    return {
        s.marker
        for s in published.scenario.secrets
        if s.marker in readable and s.language == language
    }


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_every_retrieval_returns_exactly_what_each_person_may_read(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))

    for person in published.scenario.people:
        reader = person.context()
        hidden = published.secrets_of(published.scenario.forbidden(person))
        for name, probe in PROBES_BY_NAME.items():
            for language, question in PROBES.items():
                where = f"{name}/{language}/{person.name}/seed {seed}"
                expected = wanted(published, person, name, language)
                documents, _, seen = await probe(runtime, reader, question, LIMIT)
                assert published.markers(documents) == expected, where
                assert leaked(hidden, *seen) == set(), where
                # Cut to one: the one is readable, and `truncated` counts
                # only eligible Chunks, so it says nothing about the rest.
                top, truncated, seen = await probe(runtime, reader, question, 1)
                assert published.markers(top) <= expected, where
                assert len(top) == min(1, len(expected)), where
                if truncated is not None:
                    assert truncated is (len(expected) > 1), where
                assert leaked(hidden, *seen) == set(), where
    await nothing_recorded(runtime, published, caplog)


# --- The answer stream over HTTP -----------------------------------------------


class CitingEverything(Model):
    """Cites every Evidence it is shown; keeps each prompt it was sent.

    With `echo`, its answer repeats every marker in its prompt, so the text
    it streams would show anything it was shown.
    """

    def __init__(self, *, echo: bool = True) -> None:
        super().__init__()
        self.prompts: list[str] = []
        self.echo = echo

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.calls += 1
        self.prompts.append(prompt.input)
        markers = sorted(set(MARKER.findall(prompt.input)))
        answer = "The annual leave policy"
        if self.echo and markers:
            answer += " from " + " ".join(markers)
        final = json.dumps(
            {
                "answer": answer + ".",
                "citations": EVIDENCE_ID.findall(prompt.input),
                "abstained": False,
                "reason": None,
            }
        )
        yield final
        yield ModelReply(text=final, refused=False, usage=USAGE)


async def ask(
    runtime: DatabaseRuntime,
    people: list[Person],
    person: Person,
    question: str,
    model: CitingEverything,
) -> bytes:
    """Ask over HTTP as `person`; the stream must end with a completion."""
    source: Any = VectorEvidenceSource(
        runtime, FixedModel(), limit=LIMIT, embed_timeout_seconds=5
    )
    app = create_app(
        streamer=streamer(
            model,
            source=source,
            citations=CurrentCitations(runtime),
            audit=DatabaseAuditTrail(runtime),
        ),
        authenticator=ScenarioAuthenticator(people),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": question},
            headers={**person.authorization, "X-Request-ID": str(REQUEST_ID)},
        )
    assert response.status_code == 200
    assert b"event: completion" in response.content
    return response.content


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_the_answer_stream_shows_each_person_only_what_they_may_read(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    people = list(published.scenario.people)

    for person in people:
        expected = published.scenario.readable(person)
        forbidden = published.scenario.forbidden(person)
        hidden = published.secrets_of(forbidden)
        markers = expected | forbidden
        for language, question in PROBES.items():
            where = f"answer/{language}/{person.name}/seed {seed}"
            caplog.clear()
            model = CitingEverything()
            sent = await ask(runtime, people, person, question, model)
            # What reached the model is all the person may read, and only it;
            # the model repeated it, so the stream carried it too.
            assert leaked(markers, *model.prompts) == expected, where
            assert leaked(expected, sent) == expected, where
            assert leaked(hidden, sent, *model.prompts) == set(), where
            # caplog is cleared each pass, so check every marker each pass.
            assert leaked(markers, logged(caplog)) == set(), where
            assert leaked(hidden, logged(caplog)) == set(), where
    await nothing_recorded(runtime, published, caplog)


# --- Direct IDs: the real identifiers of Documents the reader may not see ----


def answer_citing(item: Evidence) -> AnswerResult:
    citation = Citation(
        evidence_id=item.evidence_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        source_id=item.source_id,
        content_version=item.content_version,
        title=item.title,
        heading_path=item.heading_path,
        source_link=item.source_link,
        excerpt=item.excerpt,
    )
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text="The annual leave policy.",
        reason=None,
        citations=(citation,),
        defect=None,
        usage=USAGE,
        context=cast(Any, None),
    )


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_a_documents_real_ids_never_let_another_person_read_it(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    denied: set[Fate] = set()

    for secret in published.scenario.secrets:
        item = published.evidence[secret.marker]
        for person in published.scenario.people:
            reader = person.context()
            allowed = secret.marker in published.scenario.readable(person)
            async with runtime.transaction() as connection:
                visible = await evidence_still_visible(
                    connection, reader, EvidenceSet((item,)), request_id=REQUEST_ID
                )
                confirmed = await confirm_citations(
                    connection, reader, answer_citing(item), request_id=REQUEST_ID
                )
            where = (secret.marker, secret.fate, person.name)
            assert visible is allowed, where
            if allowed:
                assert confirmed.outcome is AnswerOutcome.ANSWERED, where
            else:
                assert confirmed.outcome is AnswerOutcome.WITHDRAWN, where
                assert confirmed.citations == (), where
                denied.add(secret.fate)
    # Other people's, revoked, and deleted Documents were all refused.
    assert denied == set(Fate)
    await nothing_recorded(runtime, published, caplog)


# --- Conversations ---------------------------------------------------------------


async def cited(
    runtime: DatabaseRuntime, published: Published, person: Person
) -> tuple[tuple[MessageReference, ...], str]:
    """References to every readable Chunk, and a reply naming their markers."""
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=person.context(),
            limit=LIMIT,
        )
    references = tuple(
        MessageReference(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            content_version=c.content_version,
        )
        for c in found.candidates
    )
    markers = published.markers({c.document_id for c in found.candidates})
    return references, "The annual leave policy from " + " ".join(sorted(markers))


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_a_conversation_is_only_its_owners_and_forgets_what_they_lose(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    case = published.scenario
    owned: dict[str, ConversationId] = {}
    for person in case.people:
        reader = person.context()
        references, reply = await cited(runtime, published, person)
        async with runtime.transaction() as connection:
            conversation = await start_conversation(
                connection, owner_id=reader.user_id, now=OBSERVED_AT, limits=LIMITS
            )
            await add_turn(
                connection,
                owner_id=reader.user_id,
                conversation_id=conversation.conversation_id,
                question=PROBES["en"],
                reply=reply,
                outcome=MessageOutcome.ANSWERED,
                references=references,
                now=OBSERVED_AT,
                limits=LIMITS,
            )
        owned[person.name] = conversation.conversation_id

    now = OBSERVED_AT + 2 * LATER
    for person in case.people:
        reader = person.context()
        for owner, conversation_id in owned.items():
            async with runtime.transaction() as connection:
                read = await read_conversation(
                    connection,
                    owner_id=reader.user_id,
                    conversation_id=conversation_id,
                    now=now,
                )
                history = await load_history(
                    connection,
                    reader,
                    conversation_id=conversation_id,
                    now=now,
                    budget_tokens=4_000,
                )
            mine = owner == person.name
            assert (read is not None) is mine, (person.name, owner)
            assert (history is not None) is mine, (person.name, owner)

    # alice loses her group's Document; her own History withholds the
    # Answer that cited it, and shows none of its markers.
    alice = case.person("alice")
    target = next(
        s for s in case.secrets if s.grantee == ENGINEERING and s.fate is Fate.KEPT
    )
    await reconcile_document(runtime, normalized(target), readers=[], observed_at=now)
    async with runtime.transaction() as connection:
        history = await load_history(
            connection,
            alice.context(),
            conversation_id=owned["alice"],
            now=now,
            budget_tokens=4_000,
        )
    assert history is not None
    (turn,) = history.turns
    assert (turn.answer, history.withheld) == (None, 1)
    shown = [turn.question, turn.answer, *history.earlier_questions]
    assert leaked({target.marker}, *shown) == set()
    await nothing_recorded(runtime, published, caplog)


# --- A revocation race -----------------------------------------------------------


class ChangingWhileWriting(CitingEverything):
    """Runs `change` after its prompt is built, before it replies."""

    def __init__(self, change: Callable[[], Awaitable[None]]) -> None:
        # No echo: text streamed before a withdrawal cannot be unsent.
        super().__init__(echo=False)
        self.change = change

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        await self.change()
        async for part in super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        ):
            yield part


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fate", [Fate.REVOKED, Fate.DELETED])
async def test_a_document_taken_away_while_the_model_writes_is_withdrawn(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int, fate: Fate
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    case = published.scenario
    alice = case.person("alice")
    # alice's group Document: readable when she asks, gone as it is written.
    target = next(
        s for s in case.secrets if s.grantee == ENGINEERING and s.fate is Fate.KEPT
    )
    later = OBSERVED_AT + 2 * LATER

    async def take_away() -> None:
        if fate is Fate.REVOKED:
            await reconcile_document(
                runtime, normalized(target), readers=[], observed_at=later
            )
        else:
            await tombstone_missing(
                runtime,
                source_id=SOURCE_ID,
                seen={s.external_id for s in case.secrets if s is not target},
                observed_at=later,
            )

    model = ChangingWhileWriting(take_away)
    sent = await ask(runtime, list(case.people), alice, PROBES["en"], model)

    assert target.marker in model.prompts[0]
    # Withdrawn: no Citation, so not even the title or IDs leave.
    assert b"event: citation" not in sent
    assert leaked(published.secrets_of({target.marker}), sent) == set()
    async with runtime.transaction() as connection:
        trail = await load_request_audit_events(
            connection, request_id=REQUEST_ID, limit=50
        )
    reads = {
        event.target_id: event.decision
        for event in trail
        if event.action is AuditAction.DOCUMENT_READ
    }
    assert reads[published.documents[target.marker].value] is AuditDecision.DENIED
    await nothing_recorded(runtime, published, caplog)
