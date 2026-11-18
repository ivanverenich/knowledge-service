"""Adversarial corpora: unauthorized near-matches never leave the vector query.

Each seed builds a small corpus where Chunks the reader may not see are
usually nearer to the query than the ones it may see: exact matches, grants
to other users and groups, a group granted under the reader's own user ID,
revoked and tombstoned Documents. A plain Python model of the eligibility
rule says which Chunks the reader may see; the database must return exactly
those, and its nearest one, and never text from any other.
"""

import math
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject, SubjectKind
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, GroupId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
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

SEEDS = range(30)
MARKER = "HIDDEN"
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
    external_id: str
    readers: tuple[Subject, ...]
    vector: tuple[float, float, float]
    revoked: bool
    tombstoned: bool


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
        readers = tuple(rng.sample(SUBJECTS, rng.randint(0, 3)))
        # Half the corpus sits exactly on the query; the rest is farther out.
        if rng.random() < 0.5:
            vector = (1.0, 0.0, 0.0)
        else:
            vector = (rng.uniform(-1, 1), rng.uniform(0.1, 1), rng.uniform(-1, 1))
        planted.append(
            Planted(
                external_id=f"doc-{index}-{MARKER if index % 2 else 'open'}",
                readers=readers,
                vector=vector,
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


def distance(vector: tuple[float, float, float]) -> float:
    norm = math.sqrt(sum(value * value for value in vector))
    return 1 - vector[0] / norm


async def store(
    runtime: DatabaseRuntime, planted: list[Planted]
) -> dict[str, DocumentId]:
    ids: dict[str, DocumentId] = {}
    for item in planted:
        ids[item.external_id] = await a_document(
            runtime, item.external_id, readers=item.readers, vector=item.vector
        )
    later = OBSERVED_AT + timedelta(hours=1)
    for item in planted:
        if item.revoked:
            await reconcile_document(
                runtime, plain_text(item.external_id), readers=[], observed_at=later
            )
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={item.external_id for item in planted if not item.tombstoned},
        observed_at=later,
    )
    return ids


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_only_eligible_chunks_come_back_however_near_the_others_are(
    runtime: DatabaseRuntime, seed: int
) -> None:
    planted = corpus(seed)
    ids = await store(runtime, planted)

    for reader in READERS:
        eligible = [item for item in planted if may_read(reader, item)]
        async with runtime.transaction() as connection:
            everything = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=reader, limit=100
            )
            nearest = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=reader, limit=1
            )

        returned = {candidate.document_id for candidate in everything.candidates}
        assert returned == {ids[item.external_id] for item in eligible}, seed
        assert not everything.truncated
        hidden = {item.external_id for item in planted} - {
            item.external_id for item in eligible
        }
        texts = " ".join(candidate.text for candidate in everything.candidates)
        assert not any(external_id in texts for external_id in hidden), seed
        if not eligible:
            assert nearest.candidates == ()
            continue
        (first,) = nearest.candidates
        best = min(distance(item.vector) for item in eligible)
        assert first.distance == pytest.approx(best, abs=1e-6), seed
        assert nearest.truncated is (len(eligible) > 1)


def test_the_corpora_are_adversarial() -> None:
    # Most seeds must put a Chunk the reader may not see nearest of all,
    # or the test above would prove little.
    hard = 0
    for seed in SEEDS:
        planted = corpus(seed)
        for reader in READERS:
            eligible = [item for item in planted if may_read(reader, item)]
            hidden = [item for item in planted if not may_read(reader, item)]
            if (
                eligible
                and hidden
                and min(map(distance, (i.vector for i in hidden)))
                <= min(map(distance, (i.vector for i in eligible)))
            ):
                hard += 1
    assert hard >= len(SEEDS)


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

    eligible = {
        ids[item.external_id].value for item in planted if may_read(reader, item)
    }
    assert sorted(rows) == sorted(eligible)
