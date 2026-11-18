"""One security contract, run against every search: by meaning and by words.

Each seed builds a small corpus where Chunks the reader may not see are
usually the best matches: exact vector matches, and the query's words in
their titles and repeated in their text. A plain Python statement of the
eligibility rule, `may_read`, says which Documents each reader may see. Every
search under contract must then return exactly those, rank only those, and
never show text, a title, or a highlight from any other:

- `dense`: `search_similar_chunks`, by vector distance;
- `english`: `search_lexical_chunks` routed to the `english` configuration;
- `ukrainian`: routed to the `simple` configuration, read by topic words;
- `ukrainian_typed`: the same with web-search syntax, read as typed;
- `both`: a mixed-language question, routed to both at once;
- `both_typed`: the same with a quoted phrase and an excluded word.

Each search's best result must also be the reader's best match by the
reference model: the nearest vector, or a title match when there is one.
"""

import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, Subject, SubjectKind
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, GroupId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_space,
)

SEEDS = range(30)
WORDS = "annual leave щорічна відпустка"
ALICE = UserId(UUID(int=0xA1))
BOB = UserId(UUID(int=0xB0))
ENGINEERING = GroupId(UUID(int=0xE1))
FINANCE = GroupId(UUID(int=0xF1))
SUBJECTS = [
    Subject.public(),
    Subject.user(ALICE),
    Subject.user(BOB),
    Subject.group(ENGINEERING),
    Subject.group(FINANCE),
    # The same UUIDs under the other kind are other subjects.
    Subject.group(GroupId(ALICE.value)),
    Subject.user(UserId(ENGINEERING.value)),
]
READERS = [
    AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING})),
    AuthorizationContext(user_id=ALICE),
    AuthorizationContext(user_id=BOB, group_ids=frozenset({FINANCE})),
    AuthorizationContext(
        user_id=UserId(UUID(int=0xC0)), group_ids=frozenset({ENGINEERING, FINANCE})
    ),
]


@dataclass(frozen=True, slots=True)
class Planted:
    """One Document: who may read it, how well it matches, and its fate."""

    name: str
    readers: tuple[Subject, ...]
    vector: tuple[float, float, float]
    in_title: bool
    repeats: int
    revoked: bool
    tombstoned: bool

    @property
    def title(self) -> str:
        return f"{self.name} {WORDS}" if self.in_title else self.name

    @property
    def text(self) -> str:
        return f"{self.name}. " + " ".join([WORDS] * self.repeats)


type Search = Callable[
    [AsyncConnection, AuthorizationContext, int],
    Awaitable[tuple[Sequence[Candidate], bool]],
]


async def dense(
    connection: AsyncConnection, reader: AuthorizationContext, limit: int
) -> tuple[Sequence[Candidate], bool]:
    found = await search_similar_chunks(
        connection, identity=IDENTITY, query=QUERY, context=reader, limit=limit
    )
    return found.candidates, found.truncated


def lexical(text: str, configurations: tuple[LexicalConfiguration, ...]) -> Search:
    async def search(
        connection: AsyncConnection, reader: AuthorizationContext, limit: int
    ) -> tuple[Sequence[Candidate], bool]:
        found = await search_lexical_chunks(
            connection, text=text, context=reader, limit=limit
        )
        # The contract is only worth its name if each route really runs.
        assert found.route.configurations == configurations
        return found.candidates, found.truncated

    return search


ENGLISH = LexicalConfiguration.ENGLISH
SIMPLE = LexicalConfiguration.SIMPLE
SEARCHES: dict[str, Search] = {
    "dense": dense,
    "english": lexical("annual leave", (ENGLISH,)),
    "ukrainian": lexical("щорічна відпустка", (SIMPLE,)),
    "ukrainian_typed": lexical('"щорічна відпустка"', (SIMPLE,)),
    "both": lexical(WORDS, (ENGLISH, SIMPLE)),
    "both_typed": lexical('"annual leave" щорічна відпустка -ніщо', (ENGLISH, SIMPLE)),
}


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


def corpus(seed: int) -> list[Planted]:
    rng = random.Random(seed)  # noqa: S311 - a seeded, reproducible corpus
    planted: list[Planted] = []
    for index in range(rng.randint(6, 12)):
        # Half the corpus sits exactly on the query vector.
        if rng.random() < 0.5:
            vector = (1.0, 0.0, 0.0)
        else:
            vector = (rng.uniform(-1, 1), rng.uniform(0.1, 1), rng.uniform(-1, 1))
        planted.append(
            Planted(
                name=f"doc{index}x{seed}",
                readers=tuple(rng.sample(SUBJECTS, rng.randint(0, 3))),
                vector=vector,
                in_title=rng.random() < 0.5,
                repeats=rng.randint(1, 4),
                revoked=rng.random() < 0.15,
                tombstoned=rng.random() < 0.15,
            )
        )
    return planted


def may_read(reader: AuthorizationContext, item: Planted) -> bool:
    """The eligibility rule, written out in Python."""
    if item.revoked or item.tombstoned:
        return False
    for subject in item.readers:
        if subject.kind is SubjectKind.PUBLIC:
            return True
        if subject.kind is SubjectKind.USER and subject.identifier == reader.user_id:
            return True
        if subject.kind is SubjectKind.GROUP and subject.identifier in reader.group_ids:
            return True
    return False


def distance(item: Planted) -> float:
    norm = math.sqrt(sum(value * value for value in item.vector))
    return 1 - item.vector[0] / norm


def strength(item: Planted) -> tuple[bool, int]:
    """How strongly an item's words match: a title match, then repeats."""
    return item.in_title, item.repeats


def normalized(item: Planted) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=item.name,
            version="v1",
            media_type="text/plain",
            content=item.text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title=item.title, modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def store(
    runtime: DatabaseRuntime, planted: list[Planted]
) -> dict[str, DocumentId]:
    """Publish the corpus, embed each Chunk, then revoke and tombstone."""
    ids: dict[str, DocumentId] = {}
    for item in planted:
        reconciled = await reconcile_document(
            runtime, normalized(item), readers=item.readers, observed_at=OBSERVED_AT
        )
        document_id = reconciled.document.document_id
        async with runtime.transaction() as connection:
            space_id = await load_embedding_space_id(connection, IDENTITY)
            assert space_id is not None
            (chunk,) = await load_chunks(connection, document_id=document_id)
            await insert_chunk_embeddings(
                connection,
                space_id=space_id,
                dimensions=IDENTITY.dimensions,
                vectors={chunk.chunk_id: item.vector},
                created_at=OBSERVED_AT,
            )
        ids[item.name] = document_id
    later = OBSERVED_AT + timedelta(hours=1)
    for item in planted:
        if item.revoked:
            await reconcile_document(
                runtime, normalized(item), readers=[], observed_at=later
            )
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={item.name for item in planted if not item.tombstoned},
        observed_at=later,
    )
    return ids


def shown(candidate: Candidate) -> str:
    """Everything a Candidate carries that a person could read."""
    parts = [candidate.text, candidate.title, candidate.external_id]
    parts.append(candidate.heading_path or "")
    if isinstance(candidate, LexicalCandidate):
        parts += [candidate.text[h.start : h.end] for h in candidate.highlights]
    return " ".join(parts)


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", list(SEARCHES))
async def test_every_search_returns_exactly_what_the_reader_may_see(
    runtime: DatabaseRuntime, name: str, seed: int
) -> None:
    search = SEARCHES[name]
    planted = corpus(seed)
    ids = await store(runtime, planted)

    by_id = {ids[item.name]: item for item in planted}
    for reader in READERS:
        allowed = [item for item in planted if may_read(reader, item)]
        eligible = {item.name for item in allowed}
        hidden = {item.name for item in planted} - eligible
        async with runtime.transaction() as connection:
            everything, more = await search(connection, reader, 100)
            first, truncated = await search(connection, reader, 1)

        assert {c.document_id for c in everything} == {ids[n] for n in eligible}, seed
        assert not more, seed
        text = " ".join(shown(candidate) for candidate in everything)
        assert not any(hidden_name in text for hidden_name in hidden), seed
        assert truncated is (len(eligible) > 1), seed
        if not allowed:
            assert first == (), seed
            continue
        # The best match among what the reader may see, by the reference model.
        (best,) = first
        top = by_id[best.document_id]
        if name == "dense":
            nearest = min(map(distance, allowed))
            assert distance(top) == pytest.approx(nearest, abs=1e-6), seed
        else:
            # A title match outranks any number of matches in the text; past
            # that, `ts_rank` saturates, so repeats may tie.
            assert top.in_title or not any(item.in_title for item in allowed), seed


def test_the_corpora_are_adversarial() -> None:
    # In most seed and reader pairs, a Document the reader may not see must
    # match at least as well as every one it may see, by vector and by
    # words, or the contract above would prove little.
    by_vector = by_words = 0
    for seed in SEEDS:
        planted = corpus(seed)
        for reader in READERS:
            eligible = [item for item in planted if may_read(reader, item)]
            hidden = [item for item in planted if not may_read(reader, item)]
            if not eligible or not hidden:
                continue
            if min(map(distance, hidden)) <= min(map(distance, eligible)):
                by_vector += 1
            if max(map(strength, hidden)) >= max(map(strength, eligible)):
                by_words += 1
    assert by_vector >= len(SEEDS)
    assert by_words >= len(SEEDS)


@pytest.mark.integration
async def test_a_query_that_forgets_to_join_documents_still_sees_only_eligible_chunks(
    runtime: DatabaseRuntime,
) -> None:
    planted = corpus(0)
    ids = await store(runtime, planted)
    reader = READERS[0]
    statement = sa.select(chunks.c.document_id).where(eligible_chunks_predicate(reader))

    async with runtime.transaction() as connection:
        rows = (await connection.execute(statement)).scalars().all()

    eligible = {ids[item.name].value for item in planted if may_read(reader, item)}
    assert sorted(rows) == sorted(eligible)
