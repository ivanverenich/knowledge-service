# M07-T07 — Enforce ACLs in vector search

Task: [M07-T07 — Enforce ACLs in vector search](../curriculum/milestones/M07-identity-and-authorization/M07-T07-enforce-acls-in-vector-search.md)

## What you will do

This lesson connects the reader from the token to the vector search over the real database, and proves with hostile data that nothing the reader may not see comes back. The permission check inside the vector query already exists from M04-T06: it joins each Chunk to its Document and its Access Grants in the same SQL that ranks by distance. What is missing is a real Evidence source that runs that query as the request's reader; the answer stream has only used test sources so far.

- **A real Evidence source:** `VectorEvidenceSource` embeds the Question and runs the vector search with the request's Authorization Context. The answer stream can now take its Evidence from PostgreSQL.
- **Only a sound reader:** the permission check accepts only an exact, unchanged `AuthorizationContext`. A subclass, a look-alike, or a context altered after it was made is refused before any SQL is built.
- **Self-contained check:** the permission check ties each Chunk to its own Document, so a query that forgets the join cannot pair every Chunk with every visible Document.
- **Adversarial proof:** thirty generated corpora, where Chunks the reader may not see are usually as near as or nearer than the ones it may see, are checked against a plain Python model of the rule.

| The task asks for | Where it happens |
|---|---|
| Join current Documents and Access Grants inside the vector Candidate query | Already in `vector_search_statement`; Step 3 makes the check stand on its own |
| Using the Authorization Context and availability state | Steps 2 and 3: only a sound reader becomes a check; Step 4: the stream's reader reaches the query |
| Tests: success, highest-risk edge, typed failure or denial | Steps 5 and 6 |
| Adversarial data proves unauthorized high-similarity Chunks are never returned | Step 6: `tests/test_vector_acl_adversarial_integration.py` |
| Authorization preserved before data leaves storage | Step 6: a hidden exact match never reaches the model's prompt or the client |
| Telemetry holds no seeded sensitive text | Step 6: the marker test over the prompt and the stream |
| Update only what the change made inaccurate | Step 8: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 6 write code and tests. Steps 1, 7, and 8 do not, though Step 8 edits two documents.

Terms used in this lesson:

- **Vector search:** finding the Chunks whose stored vectors point most nearly the same way as the Question's vector. Nearness is cosine distance: 0 is the same direction, 2 the opposite.
- **Eligibility:** whether a reader may see a Chunk: its Document is available, and a grant names everyone, the reader, or one of the reader's Groups.
- **Eligibility inside the query:** the database drops ineligible Chunks before it sorts and cuts to the top k. Like a librarian who only ever fetches books from shelves you may enter, rather than fetching everything and hiding some at the desk.
- **Adversarial data:** test data built to make a mistake visible, here by putting forbidden Chunks exactly on the query.
- **Reference model:** a few lines of plain Python that state the rule independently, so the database's answer can be checked against it.
- **Cross join:** a query that reads two tables without linking their rows, so every row of one pairs with every row of the other.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **The query already filters; the gap is the caller.** `search_similar_chunks` has passed `eligible_chunks_predicate(context)` into the same WHERE as the distance ordering since M04, and `EXPLAIN` shows the grant check below the sort and the limit. The threat model's open item was "No Evidence source over the database takes the reader yet". This lesson closes it for vector search; M07-T08 does lexical search.
- **Check the reader where it becomes SQL.** Every search builds its permission check through `eligible_chunks_predicate`. So that one function now calls `checked_reader`, which accepts only an exact `AuthorizationContext` and checks its fields again. A frozen dataclass can still be changed with `object.__setattr__`, and a subclass can answer with other Groups than the ones it was checked with.
- **The check ties Chunk to Document itself.** Every current caller joins `chunks` to `documents`. If a future one forgets, the check would still name `documents` and the database would pair every Chunk with every Document the reader may see. With `documents.document_id = chunks.document_id` inside the check, forgetting the join narrows nothing.
- **The Evidence source checks before it spends.** `VectorEvidenceSource` checks the reader before embedding, so a bad request costs no provider call. It then requires exactly one vector from its own model, because a reply naming another model would be compared against vectors this Question was never embedded for.
- **No reranking yet.** The source reports how many Chunks it found but no score. An abstention policy without a score threshold works; one with a threshold refuses every request, because there is no score to compare.
- **Seeded, not random.** The adversarial corpora come from `random.Random(seed)` for seeds 0 to 29, so a failure names a seed and reproduces exactly. A separate check proves the corpora are hard: in most seed and reader pairs, a forbidden Chunk is at least as near as every allowed one.
- **What the review found.** An independent adversarial review tried kind confusion between user and group IDs, duplicate grants, metadata filters naming hidden Documents, tombstoned Documents, non-current spaces, 20,000 groups, ties at the limit, 60 concurrent readers on one connection pool, and full SQL logging at DEBUG. Nothing hidden came back, showed in `truncated` or a count, or reached a log. It made these remarks, all fixed with tests that failed before:
  - A context changed after it was made, such as `group_ids` replaced with a plain set of other Groups, was not checked again and widened the search. Now `checked_reader` checks the fields every time.
  - The check named `documents` without linking it to `chunks`, so a query that forgot the join would return every Chunk once any Document was visible. Now the link is inside the check.
  - The Evidence source trusted the model's reply: another model's identity, no vector, or two vectors. Now it requires one vector from its own model.
  - Two mutations survived: a subclass reader passing the source's own check and costing an embedding call, and the configured timeout never reaching the model. Each now has a test.
- **Known limits.** The hybrid branch `dense_branch` still trusts the model's reply the same way. Vector and lexical queries do not require a Chunk's content version to match its Document's; old Chunks are deleted in the same transaction that writes new ones, so this is about freshness, not permissions. No test covers the race where the current Embedding Space switches during a query. No production entry point assembles the app with this source yet.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 7 checks the last prediction.

Create `docs/evidence/M07-T07-enforce-acls-in-vector-search.md`:

```markdown
# M07-T07 — Enforce ACLs in vector search evidence

## Invariant

A vector search returns only Chunks whose Document is available and granted
to everyone, to the reader's User, or to one of the reader's Groups, decided
in the same SQL statement that orders by distance and applies the limit; a
Chunk the reader may not see is never ranked, counted, or returned, however
near it is. The check accepts only an exact, unchanged `AuthorizationContext`
and ties each Chunk to its own Document. The answer stream's Evidence comes
from that search as the request's reader, and no hidden text reaches the
model's prompt, the client, or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_vector_evidence_integration.py -q`
fails with `ModuleNotFoundError: No module named 'knowledge_service.answering.vector_evidence'`.

## Predictions

- What `VectorEvidenceSource` with limit 1 returns to a reader in `/engineering` when a Chunk granted only to another user is an exact match and the engineering Chunk is farther away:
- What `eligible_chunks_predicate` does with a real context whose `group_ids` was replaced afterwards with a plain `set`:
- What `find` does when the embedding model replies with another model's identity:
- Which tests fail if `eligibility` is removed from the WHERE of `vector_search_statement`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: check a reader where it is used

**In plain words:** a reader is checked once, when it is made. This step adds a function that checks it again, every time it is about to become a permission check: is it really the Authorization Context type, and are its user and groups still the right kinds of ID? A reader that was faked or changed afterwards is refused.

| Change | What it does |
|---|---|
| `__post_init__` | Calls `_check_reader_fields`, the same checks as before |
| `checked_reader` | Returns the context if it is exactly an `AuthorizationContext` with sound fields, else `InvalidAuthorizationContext` |
| `_check_reader_fields` | The user is a `UserId`, the groups a `frozenset` of `GroupId`s |

In `src/knowledge_service/access.py`:

Find:

```python
    """

    user_id: UserId
    group_ids: frozenset[GroupId] = frozenset()

    def __post_init__(self) -> None:
        # A plain UUID, or a UserId passed as a group, would match a grant
        # meant for another subject kind, so the exact types are required.
        if type(self.user_id) is not UserId:
            raise InvalidAuthorizationContext("the user must be a UserId")
        if type(self.group_ids) is not frozenset:
            raise InvalidAuthorizationContext("the groups must be a frozenset")
        if any(type(group_id) is not GroupId for group_id in self.group_ids):
            raise InvalidAuthorizationContext("every group must be a GroupId")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")

```

Replace with:

```python
    """

    user_id: UserId
    group_ids: frozenset[GroupId] = frozenset()

    def __post_init__(self) -> None:
        _check_reader_fields(self)


def checked_reader(context: object) -> AuthorizationContext:
    """`context` itself, if it is exactly an `AuthorizationContext` with sound fields.

    A frozen dataclass can still be changed with `object.__setattr__`, and a
    subclass or look-alike can answer with other Users or Groups, so code
    that turns a reader into a permission check calls this first.
    """
    if type(context) is not AuthorizationContext:
        raise InvalidAuthorizationContext("the reader must be an AuthorizationContext")
    _check_reader_fields(context)
    return context


def _check_reader_fields(context: AuthorizationContext) -> None:
    # A plain UUID, or a UserId passed as a group, would match a grant
    # meant for another subject kind, so the exact types are required.
    if type(context.user_id) is not UserId:
        raise InvalidAuthorizationContext("the user must be a UserId")
    if type(context.group_ids) is not frozenset:
        raise InvalidAuthorizationContext("the groups must be a frozenset")
    if any(type(group_id) is not GroupId for group_id in context.group_ids):
        raise InvalidAuthorizationContext("every group must be a GroupId")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")

```

**Check:** `uv run pytest tests/test_authorization_context.py -q` gives `89 passed`, unchanged.

### Step 3 — Coding step: a permission check that stands on its own

**In plain words:** every search, by meaning or by words, gets its permission check from one function. This step makes that function check the reader with Step 2's function first, and makes it link each Chunk to its own Document itself, so the check stays correct even inside a query that forgot to do the linking.

| Change | What it does |
|---|---|
| Imports | `checked_reader`; `chunks` from the schema |
| `eligible_chunks_predicate` | Checks the reader, then adds `documents.document_id = chunks.document_id` to the availability and grant conditions |

In `src/knowledge_service/persistence/access.py`:

**1.** Find:

```python
from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
```

Replace with:

```python
from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
    InvalidGrant,
    Subject,
    SubjectKind,
    checked_reader,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, chunks, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
```

**2.** Find:

```python

def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must join `chunks` to `documents`. Every value is a
    bound parameter, never text spliced into the SQL.
    """
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
```

Replace with:

```python

def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must read `chunks` and `documents`; the predicate
    ties each Chunk to its own Document itself, so a statement that forgets
    the join narrows nothing instead of pairing every Chunk with every
    visible Document. Every value is a bound parameter, never text spliced
    into the SQL.

    Only an exact `AuthorizationContext` with sound fields is accepted (see
    `checked_reader`): a subclass, a look-alike, or a context changed after
    it was made could answer with other Users or Groups.
    """
    context = checked_reader(context)
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
```

**3.** Find:

```python
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


async def replace_access_grants(
```

Replace with:

```python
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.document_id == chunks.c.document_id,
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


async def replace_access_grants(
```

The added equality changes nothing for the searches that already join, since they join on the same columns; it only protects a future query that does not.

**Check:** every search that uses the check still passes against PostgreSQL:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_search_integration.py tests/test_lexical_search_integration.py tests/test_current_citations_integration.py -q
```

Expected: `78 passed`.

### Step 4 — Coding step: the vector Evidence source

**In plain words:** this file is what the answer stream will call to find Evidence in the real database. It checks the reader, turns the Question into a vector, makes sure the model gave back exactly one vector of its own, and runs the vector search as that reader. The Chunks it gets back become Evidence; nothing else can.

| Part | What it does |
|---|---|
| `VectorEvidenceSource(runtime, model, *, limit, embed_timeout_seconds)` | A limit from 1 to 20 and a positive, finite timeout, or `ValueError` |
| `find` | `checked_reader` (a failure becomes `InvalidAnswerRequest`); embed; one vector of the model's own identity, or `InvalidEmbedding`; `search_similar_chunks` with the reader; `build_evidence` |
| Signals | `eligible` is the number of Chunks found; no score and no reranker |

Create `src/knowledge_service/answering/vector_evidence.py`:

```python
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
```

**Check:** `uv run pyright src` reports `0 errors`.

### Step 5 — Coding step: unit tests for the reader check

**In plain words:** these tests, which need no database, try to slip a wider reader past the permission check: a subclass that answers with more Groups, an object that only looks like a reader, and real readers changed after they were made. All must be refused. Two more tests check that the reader reaches the SQL only as bound values, and that the check links Chunk to Document by itself.

Create `tests/test_eligibility_predicate.py`:

```python
"""The eligibility predicate takes only a sound Authorization Context.

Every search builds its permission check from the reader. A subclass could
answer with other Groups than the ones it was checked with, a look-alike was
never checked at all, and a context changed after it was made skipped its
checks, so all three are refused before any SQL is built.
"""

from typing import Any, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from knowledge_service.access import AuthorizationContext, InvalidAuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks

ALICE = UserId(UUID(int=0xA1))
ENGINEERING = GroupId(UUID(int=0xE1))
EVERY_GROUP = frozenset(GroupId(UUID(int=n)) for n in range(1, 4))


def wider_than_checked() -> Any:
    """A subclass that stores the Group it is checked with, then reads others."""
    stored: Any = AuthorizationContext.__dict__["group_ids"]

    def read(self: object) -> frozenset[GroupId]:
        return EVERY_GROUP

    def write(self: object, value: frozenset[GroupId]) -> None:
        stored.__set__(self, value)

    wider = type(
        "Wider",
        (AuthorizationContext,),
        {"__slots__": (), "group_ids": property(read, write)},
    )
    return wider(user_id=ALICE, group_ids=frozenset({ENGINEERING}))


def changed_after_checking(field: str, value: object) -> AuthorizationContext:
    """A real context whose field was replaced, skipping its own checks."""
    context = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))
    object.__setattr__(context, field, value)
    return context


class LookAlike:
    user_id = ALICE
    group_ids = EVERY_GROUP


@pytest.mark.parametrize(
    "reader",
    [
        wider_than_checked(),
        LookAlike(),
        None,
        changed_after_checking("group_ids", set(EVERY_GROUP)),
        changed_after_checking("group_ids", frozenset({ALICE})),
        changed_after_checking("user_id", ENGINEERING),
    ],
    ids=[
        "subclass",
        "look-alike",
        "none",
        "groups-set",
        "user-as-group",
        "group-as-user",
    ],
)
def test_anything_but_an_authorization_context_is_refused(reader: Any) -> None:
    with pytest.raises(InvalidAuthorizationContext):
        eligible_chunks_predicate(cast(AuthorizationContext, reader))


def test_the_reader_reaches_the_sql_only_as_bound_parameters() -> None:
    reader = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))

    compiled = eligible_chunks_predicate(reader).compile(dialect=postgresql.dialect())

    assert str(ALICE.value) not in str(compiled)
    assert ALICE.value in compiled.params.values()
    assert [ENGINEERING.value] in compiled.params.values()


def test_the_predicate_ties_each_chunk_to_its_own_document() -> None:
    # A statement that reads chunks but forgets to join documents must not
    # pair every Chunk with every Document the reader may see.
    reader = AuthorizationContext(user_id=ALICE)
    statement = sa.select(chunks.c.chunk_id).where(eligible_chunks_predicate(reader))

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "documents.document_id = chunks.document_id" in sql
```

`wider_than_checked` builds a subclass that stores the Group it was checked with but answers with three others. It shows why the check compares the exact type, not `isinstance`.

**Check:** `uv run pytest tests/test_eligibility_predicate.py -q` gives `8 passed`.

### Step 6 — Coding step: the Evidence source and adversarial corpora against PostgreSQL

**In plain words:** these tests use the real database. The first file plants a hidden Chunk that is an exact match for the Question and a visible one farther away, and checks the reader gets only the visible one, that a bad reader or a broken model reply is refused, and that over HTTP the hidden text never reaches the model's prompt or the response. The second file builds thirty small hostile corpora and checks the database returns exactly what a plain Python version of the rule says, and its nearest Chunk.

| File | Tests |
|---|---|
| `tests/test_vector_evidence_integration.py` | Only visible Evidence; no grant finds nothing; the limit bounds it; bad readers refused before embedding; the timeout reaches the model; a reply that is not one vector of its own is refused; bad settings refused; a hidden exact match never reaches the model or the client |
| `tests/test_vector_acl_adversarial_integration.py` | Thirty seeded corpora checked against `may_read` for four readers; a check that the corpora are hard; a query without the join still sees only eligible Chunks |

Create `tests/test_vector_evidence_integration.py`:

```python
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
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )

    assert response.status_code == 200
    assert len(model.prompts) == 1
    assert "granted" in model.prompts[0].input
    assert MARKER not in model.prompts[0].input
    assert MARKER not in response.text
```

Create `tests/test_vector_acl_adversarial_integration.py`:

```python
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
```

`may_read` is the reference model: the rule in a few lines of Python, written without looking at the SQL. If the database and this function ever disagree for any seed and reader, one of them is wrong, and the seed in the failure message reproduces it.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_vector_evidence_integration.py tests/test_vector_acl_adversarial_integration.py -q
```

Expected: `53 passed`. The marker `-m "integration or not integration"` runs both the database tests and the few in these files that need no database.

### Step 7 — No coding in this step: break it on purpose

This step moves the permission check out of the vector query, watches the right tests fail, and puts it back.

**Action 1:** in `vector_search_statement` in `src/knowledge_service/retrieval/vector_search.py`, delete this line from the `.where(...)`:

```python
            eligibility,
```

**Action 2:** run

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_acl_adversarial_integration.py tests/test_vector_evidence_integration.py -q
```

Expected: `33 failed, 11 passed`:

- `test_only_eligible_chunks_come_back_however_near_the_others_are`, all 30 seeds
- `test_the_reader_gets_only_evidence_it_may_see`
- `test_a_reader_with_no_grant_finds_nothing`
- `test_a_hidden_exact_match_never_reaches_the_model_or_the_client`

The last one shows the damage: the hidden exact match becomes Evidence, enters the model's prompt, and its text can reach the client.

**Action 3:** put the line back and run the command again: `44 passed`.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 8 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model's invariant trace named `M07-T07` as a future owner of invariant 2, and `make docs-check` refuses a completed task as an owner, so the trace now names this lesson's tests.

**Action 1:** update two documents.

In `docs/architecture.md`:

Find:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

Replace with:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

In `docs/threat-model.md`:

**1.** Find:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query | ACLs in vector and lexical search (M07-T07, M07-T08) |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

```

Replace with:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and seeded adversarial corpora check that the vector query returns exactly the eligible Chunks however near the others are | ACLs in lexical search (M07-T08) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

```

**2.** Find:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
```

Replace with:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_vector_acl_adversarial_integration.py::test_only_eligible_chunks_come_back_however_near_the_others_are`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical search joins the answer stream in M07-T08, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
uv run pytest tests/test_eligibility_predicate.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2750 passed for `make test-unit`, 17 more than M07-T06, and 471 passed for `make test-integration`, 44 more.

**Action 3:** complete the evidence file:

- **Design:** where the check sits in the query, `checked_reader`, the self-contained join, and what `VectorEvidenceSource` checks and returns.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why the reader is checked in the predicate, why the predicate joins by itself, and why seeded corpora with a reference model; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T07 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T08, and commit with `m07-t07: enforce acls in vector search`.

## Reflection questions

1. The answer stream calls `VectorEvidenceSource.find(question, reader=...)`. What does the stream no longer need to know about embeddings, Embedding Spaces, or how permissions are checked?
2. Someone adds a vector index to speed up search, and the database starts reading the 40 nearest vectors from the index before checking permissions. What would a reader with few allowed Documents see, and which of this lesson's tests would notice?
3. A colleague says: "the reader is checked when the token is verified; checking it again in every query is wasted work." What does `checked_reader` catch that the first check cannot?
4. Thirty seeds passed. What kind of mistake could still hide from these corpora, and how would you change the generator to look for it?
5. Alice is in `/engineering`. A Document granted to `/engineering` is far from her Question, and one granted only to bob is an exact match. Write what `find` with limit 1 returns to her, what it returns to bob, and what it returns to carol, who is in no group.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `checked_reader` refuses anything but an exact `AuthorizationContext` with sound fields, and the context tests still pass (89).
- [ ] `eligible_chunks_predicate` checks the reader and ties each Chunk to its own Document, and the existing search tests still pass (78).
- [ ] `VectorEvidenceSource` checks the reader before embedding, requires one vector from its own model, and searches as the reader.
- [ ] The reader-check unit tests pass (8).
- [ ] The Evidence source and adversarial tests pass against PostgreSQL (53).
- [ ] You ran the deliberate break, saw the 33 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe the source and its limits, and the trace names the new tests.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
