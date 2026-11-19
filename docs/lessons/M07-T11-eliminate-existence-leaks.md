# M07-T11 — Eliminate existence leaks

Task: [M07-T11 — Eliminate existence leaks](../curriculum/milestones/M07-identity-and-authorization/M07-T11-eliminate-existence-leaks.md)

## What you will do

This lesson checks that a Document a reader may not see is, to that reader, exactly like a Document that does not exist: not only its text, but every trace it could leave. A system can hide a Document's content and still give it away, through an error that differs, a count that is one higher, a response that takes longer, or a log line that appears only when it is there. Each of those is an existence leak.

- **Black-box comparisons:** tests observe everything outside the server sees, twice, once without another User's Document and once with it as the best possible match, and require the two to be equal: response bytes, headers, every log record from any logger, and the model's prompt.
- **Forbidden IDs like missing IDs:** a real Chunk, Evidence, or Conversation ID that belongs to someone else gives exactly the same result and log as one that never existed.
- **Timing:** vector search took about 10 ms longer when 5,000 hidden Chunks sat near the question. It now settles the reader's Chunks before ranking any, so the hidden ones cost nothing; a structural test pins that.
- **What remains, written down:** lexical search has the same timing gap but reaches no reader yet; the threat model records it, with numbers, for M09.

| The task asks for | Where it happens |
|---|---|
| Unauthorized and absent Documents indistinguishable across errors and counts | Step 4: `tests/test_existence_leaks_integration.py` |
| Across timing-sensitive paths | Steps 2 and 3: vector search ranks only the reader's Chunks; lexical recorded as a limit in Step 6 |
| Across model context | Step 4: the prompt is the same with or without the hidden Document |
| Black-box tests compare responses for missing and forbidden identifiers | Step 4 |
| No seeded sensitive text in telemetry | Step 4: every log record, from any logger, is compared |
| Update only what the change made inaccurate | Step 6: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 4 write code and tests. Steps 1, 5, and 6 do not, though Step 6 edits two documents.

Terms used in this lesson:

- **Existence leak:** any difference a reader can observe between "a Document I may not see exists" and "no such Document exists".
- **Side channel:** information that leaks through how a system behaves, such as time or size, rather than through what it returns.
- **Black-box test:** a test that looks only at what is visible from outside, here the response, the logs, and the prompt, never at internal state.
- **Masking:** replacing values that change on every request anyway, such as a request ID or an opaque Evidence ID, so two observations can be compared exactly.
- **Materialized step:** a part of a query PostgreSQL computes once, on its own, before the rest uses it. Like sorting a guest list before seating anyone, rather than checking each guest only as they reach a seat.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Most channels were already closed.** Earlier lessons put the permission check inside every query (M07-T07, M07-T08), made withdrawn and empty answers end alike (M07-T09), and kept logs to counts. This lesson's tests confirm it from outside: with another User's exact-match Document present, the stream, the logs, and the prompt do not change by a single byte.
- **Compare everything, not a chosen few.** The first version compared four log fields from one logger; the review added leaks elsewhere and they passed. Now every log record from every logger is compared with all its extra fields, and results are compared whole, reason and usage included.
- **The timing leak.** The vector query had no index, so PostgreSQL sorted every stored vector by distance and then checked permissions row by row, nearest first, until it had enough rows. Every hidden Chunk nearer than the reader's results cost one more check: with 5,000 of them the search took about 10 ms longer, steadily. That tells a reader that something hidden sits close to their question.
- **The fix: decide who may read first.** The query now computes the reader's Chunks in a `MATERIALIZED` step, which PostgreSQL computes on its own and never merges into the ranking, and then ranks only those. Measured with 5,000 hidden exact matches, the gap fell to within noise. The cost: a reader who can see 20,000 Documents now waits about 50 ms instead of 24 ms, because every visible Chunk is checked once; a reader who sees few Documents waits about 3 ms, as before. That trade was chosen on purpose.
- **Why a structural test, not a timing test.** Timing in tests is noisy, and with a small corpus PostgreSQL already starts from the grants, so a timing test passed on the old code too. The structural test reads the compiled SQL and requires the grants inside the materialized step and the distance outside it, which is what makes the work independent of hidden Chunks.
- **Why lexical search is only recorded.** The same fix would stop lexical search from using its fast word index, slowing every search for readers who see many Documents. Lexical search reaches no reader yet, so the threat model records the gap, about 5 to 28 ms per 5,000 hidden matches, to be fixed or bounded before M09 serves it.
- **What the review found.** An independent adversarial review compared stream bytes, prompts, logs, `truncated`, eligible counts, hybrid diagnostics, error paths, and forbidden, deleted, and missing IDs, and measured timing with up to 20,000 visible and 5,000 hidden Documents, with query plans. It found:
  - the vector timing leak described above, now fixed and pinned by the structural test;
  - the lexical timing leak, recorded with its numbers;
  - that a hybrid branch deadline would turn the lexical gap into a definite timed-out branch, recorded for M09;
  - four of six planted leaks survived the first tests: a count in another log field, a sleep, a different `reason`, and a title in another logger; the log and result comparisons now catch the ones that are not about timing;
  - `rate_message` and `load_message_feedback` read by Message ID with no owner check, not reachable today, recorded as a gap.
- **Known limits.** Lexical and sparse search time still depends on hidden matches. Vector search is slower for readers who see very many Documents. Rating Messages needs an owner check before any route exposes it.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 5 checks the last prediction.

Create `docs/evidence/M07-T11-eliminate-existence-leaks.md`:

```markdown
# M07-T11 — Eliminate existence leaks evidence

## Invariant

To a reader, a Document they may not see is indistinguishable from one that
does not exist: the answer stream's bytes and headers, every log record, and
the model's prompt are the same with or without another User's best-matching
Document, and a forbidden Chunk, Evidence, or Conversation ID gives the same
result and log as a missing one. Vector search settles the reader's Chunks
before it ranks any, so its work does not grow with hidden Chunks near the
question. Lexical search's timing gap is recorded, with numbers, as a known
limit until it serves a reader.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_existence_leaks_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_existence_leaks_integration.py`.

## Predictions

- Whether alice's answer stream, the logs, or the prompt change when bob's exact-match Document is added:
- What `confirm_citations` returns for a Citation of bob's real Chunk and for one of an invented Chunk:
- How long a vector search takes for a reader who sees 20,000 Documents, with and without 5,000 hidden exact matches, before and after the change:
- Which tests fail if `MATERIALIZED` is removed from the vector query, and which keep passing:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: rank only the reader's Chunks

**In plain words:** this step changes the order in which the vector search works. Before, the database could line up every stored vector by distance and then check, one by one, whether the reader may see it, so hidden Chunks close to the question cost extra work. Now it first makes the full list of Chunks the reader may see, and only then sorts those by distance. The results are the same; only the work, and so the time, no longer depends on hidden Chunks.

| Change | What it does |
|---|---|
| Module docstring | Says the work does not depend on hidden Chunks, and why |
| `visible` | A `MATERIALIZED` common table expression: the Chunks that meet `eligibility` and `restriction` |
| The ranking | Joins `visible` and ranks only those; the permission check no longer sits beside `ORDER BY` |

In `src/knowledge_service/retrieval/vector_search.py`:

**1.** Find:

```python
- **Filters.** Optional `filters` (see `retrieval.filters`) narrow the
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
```

Replace with:

```python
- **Filters.** Optional `filters` (see `retrieval.filters`) narrow the
  eligible Chunks further, in the same WHERE, so every guarantee above,
  `truncated` included, counts only Chunks that are eligible and match.
- **Empty.** No eligible Chunk is a normal, empty result, not an error.
  A Chunk whose stored vector has no direction (all zeros) has no distance,
  so it is never a Candidate.

The work does not depend on Chunks the reader may not see. The statement
first settles every Chunk the reader may see, in a materialized step the
database cannot reorder, and only then ranks those by distance. If it
ranked first and checked permissions row by row, as a planner may choose,
every hidden Chunk nearer than the reader's results would cost one more
check, and the time of a search would tell a reader how many hidden
Chunks are close to their question.

Every stored vector and every query has length 1, so distances are right
in pgvector's 32-bit floats. Ties are exact: two vectors in the same
direction can still differ in the last digits of their distances, and are
then ordered by those digits, not by Chunk ID.
"""
```

**2.** Find:

```python
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet `eligibility`.

    The predicate sits in the same WHERE as the space, so PostgreSQL applies
    it before ORDER BY and LIMIT. The space must still be current when the
    statement runs, so it never reads a space a migration has just switched
    away from. Ties in distance are broken by Chunk ID so
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
```

Replace with:

```python
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet `eligibility`.

    The Chunks that meet `eligibility` and `restriction` are chosen first,
    in a `MATERIALIZED` common table expression, which PostgreSQL computes
    once and never merges into the ranking; only those are then ordered by
    distance and cut by the limit. So no Chunk the reader may not see is
    ranked, and the work does not grow with them. The space must still be
    current when the statement runs, so it never reads a space a migration
    has just switched away from. Ties in distance are broken by Chunk ID so
    that equal inputs always return the same order. A stored vector with no
    direction gives a NaN distance, which is not a distance, so its row is
    left out; PostgreSQL treats NaN as equal to NaN.
    """
    visible = (
        sa.select(chunks.c.chunk_id)
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(eligibility, restriction)
        .cte("visible")
        .prefix_with("MATERIALIZED")
    )
    distance = chunk_embeddings.c.embedding.cosine_distance(list(query))
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
```

**3.** Find:

```python
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
            sa.exists().where(
                embedding_spaces.c.space_id == space_id.value,
                embedding_spaces.c.is_current,
            ),
            eligibility,
            restriction,
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )

```

Replace with:

```python
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(chunk_embeddings)
        .join(visible, visible.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id == space_id.value,
            sa.exists().where(
                embedding_spaces.c.space_id == space_id.value,
                embedding_spaces.c.is_current,
            ),
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )

```

`MATERIALIZED` matters: without it, PostgreSQL may fold a common table expression back into the main query and choose the old order again.

**Check:** the vector search tests still pass against PostgreSQL:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_search_integration.py -q
```

Expected: `43 passed`, unchanged.

### Step 3 — Coding step: pin the order in the SQL

**In plain words:** this test builds the vector query without running it and reads its text. It checks that the access grants are read inside the materialized step, that the distance is computed only after it, and that the ranking joins the result of that step. Timing tests are noisy; this one is exact.

In `tests/test_vector_search.py`:

Find:

```python
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values
    assert [DOCUMENT.value] in values


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
```

Replace with:

```python
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values
    assert [DOCUMENT.value] in values


def test_the_reader_s_chunks_are_settled_before_any_ranking() -> None:
    # A MATERIALIZED step is computed on its own and never merged into the
    # ranking, so the grants are read for every Chunk once, whatever its
    # distance, and the time of a search cannot count hidden near Chunks.
    statement = vector_search_statement(
        space_id=EmbeddingSpaceId.new(),
        query=(1.0, 0.0, 0.0),
        eligibility=eligible_chunks_predicate(AuthorizationContext(user_id=USER)),
        restriction=metadata_predicate(()),
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    visible, ranked = sql.split(")\n SELECT", 1)

    assert visible.startswith("WITH visible AS MATERIALIZED")
    assert "access_grants" in visible
    assert "access_grants" not in ranked
    assert "<=>" not in visible
    assert "JOIN visible" in ranked
    assert "ORDER BY" in ranked


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
```

**Check:** `uv run pytest tests/test_vector_search.py -q` gives `35 passed`.

### Step 4 — Coding step: black-box comparisons

**In plain words:** these tests look at the system from outside, twice. Once there is no Document of bob's; once bob's Document is the perfect match for alice's question, or alice's request uses bob's real IDs. Everything alice or a log could see is recorded both times, with only request IDs and Evidence IDs masked, and the two records must be identical.

| Test | Compares |
|---|---|
| `test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see` | Status, headers, body, prompt, and every log record, for a reader with one Document and with none |
| `test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one` | The whole `confirm_citations` result and its log |
| `test_forbidden_evidence_reads_like_missing_evidence` | `evidence_still_visible` and its log |
| `test_another_users_deleted_and_missing_conversations_read_the_same` | `load_history` and `delete_conversation` for bob's, a deleted, and a missing Conversation |

Create `tests/test_existence_leaks_integration.py`:

```python
"""Black-box: a Document the reader may not see looks exactly like no Document.

Each test observes what a reader, a log, or the model can see twice: once
where another User's Document does not exist, and once where it exists and
is the best possible match, or its real identifiers are used. The two
observations must be equal, once the values that differ on every request
anyway, UUIDs and opaque Evidence IDs, are masked.
"""

import dataclasses
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
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
from knowledge_service.app import create_app
from knowledge_service.conversations import ConversationLimits
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId, UserId
from knowledge_service.persistence.conversations import (
    delete_conversation,
    start_conversation,
)
from knowledge_service.retrieval.vector_search import (
    search_similar_chunks,
)
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel, vector_source
from test_streaming import QUESTION, REQUEST_ID, USAGE, streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
)

BOB = AuthorizationContext(user_id=UserId(UUID(int=0xB0B)))
READERS_GROUP = next(iter(READER.group_ids))
LIMITS = ConversationLimits()
# Values that differ on every request whatever the data holds.
VOLATILE = [
    (
        re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
        "<id>",
    ),
    (re.compile(r"E[0-9a-f]{8}"), "<evidence>"),
]
# The attributes every log record has; anything else was passed as `extra`.
STANDARD = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def masked(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


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


class Watching(CitingModel):
    """The citing model, keeping what each prompt showed it."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.prompts: list[str] = []

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.prompts.append(masked(prompt.input))
        async for part in super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        ):
            yield part


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, str, str]]:
    """Every log record, from any logger, with every extra field.

    Only timing and identifiers are masked, so a record that differs in
    anything else, or appears only once, makes two observations differ.
    """
    records: list[tuple[str, str, str, str]] = []
    for record in caplog.records:
        fields = {
            key: value for key, value in record.__dict__.items() if key not in STANDARD
        }
        for value in fields.values():
            if isinstance(value, dict):
                cast(dict[str, Any], value).pop("duration_ms", None)
        records.append(
            (record.name, record.levelname, record.getMessage(), masked(repr(fields)))
        )
    return records


async def observe(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> tuple[Any, ...]:
    """Everything outside the server sees of one question about the handbook."""
    caplog.clear()
    model = Watching("handbook.txt")
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, vector_source(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )
    return (
        response.status_code,
        masked(repr(sorted(response.headers.items()))),
        masked(response.text),
        tuple(model.prompts),
        tuple(logged(caplog)),
    )


async def bobs_secret(runtime: DatabaseRuntime) -> DocumentId:
    """Another User's Document, an exact match for the question."""
    return await a_document(
        runtime,
        "SECRET-bob.txt",
        readers=[Subject.user(BOB.user_id)],
        vector=[1.0, 0.0, 0.0],
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "own", [True, False], ids=["reader-has-one", "reader-has-none"]
)
async def test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, own: bool
) -> None:
    caplog.set_level(logging.DEBUG)
    if own:
        await a_document(
            runtime,
            "handbook.txt",
            readers=[Subject.group(READERS_GROUP)],
            vector=[0.6, 0.8, 0.0],
        )

    absent = await observe(runtime, caplog)
    await bobs_secret(runtime)
    present = await observe(runtime, caplog)

    assert present == absent
    # The reader with a Document of her own is answered; without, abstains.
    assert ('"decision":"answer"' if own else '"no_evidence"') in absent[2]
    assert "SECRET" not in repr(present)


async def bobs_evidence(runtime: DatabaseRuntime) -> Evidence:
    """Bob's real Chunk as Evidence, with the identifiers retrieval gave it."""
    await bobs_secret(runtime)
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection, identity=IDENTITY, query=QUERY, context=BOB, limit=1
        )
    (item,) = build_evidence(found.candidates).items
    return item


def invented(item: Evidence) -> Evidence:
    """The same Evidence pointing at a Chunk and Document that never existed."""
    return dataclasses.replace(
        item, chunk_id=ChunkId(uuid4()), document_id=DocumentId(uuid4())
    )


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
        text="The SECRET-bob.txt policy.",
        reason=None,
        citations=(citation,),
        defect=None,
        usage=USAGE,
        context=cast(Any, None),
    )


@pytest.mark.integration
async def test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            result = await confirm_citations(
                connection, READER, answer_citing(item), request_id=REQUEST_ID
            )
        outcomes.append((result, logged(caplog)))

    # The whole result, reason, defect, and usage included, and every record.
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].outcome is AnswerOutcome.WITHDRAWN


@pytest.mark.integration
async def test_forbidden_evidence_reads_like_missing_evidence(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            visible = await evidence_still_visible(
                connection, READER, EvidenceSet((item,)), request_id=REQUEST_ID
            )
        outcomes.append((visible, logged(caplog)))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] is False


@pytest.mark.integration
async def test_another_users_deleted_and_missing_conversations_read_the_same(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with runtime.transaction() as connection:
        bobs = await start_conversation(
            connection, owner_id=BOB.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        own = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await delete_conversation(
            connection,
            owner_id=READER.user_id,
            conversation_id=own.conversation_id,
            now=OBSERVED_AT,
        )
    targets = {
        "bobs": bobs.conversation_id,
        "deleted": own.conversation_id,
        "missing": ConversationId(uuid4()),
    }
    seen: dict[str, tuple[Any, ...]] = {}

    for name, conversation_id in targets.items():
        caplog.clear()
        async with runtime.transaction() as connection:
            history = await load_history(
                connection,
                READER,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
                budget_tokens=4_000,
            )
            deleted = await delete_conversation(
                connection,
                owner_id=READER.user_id,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
            )
        seen[name] = (history, deleted, logged(caplog))

    assert seen["bobs"] == seen["deleted"] == seen["missing"] == (None, False, [])
```

`logged` keeps every attribute a record carries beyond the standard ones, so a field added by any code, through any logger, shows up in the comparison. The review planted a title in another logger and a count in another field; the first version, which read four named fields, missed both.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_existence_leaks_integration.py -q
```

Expected: `5 passed`.

### Step 5 — No coding in this step: break it on purpose

This step removes the one word that keeps the order, and shows which tests notice.

**Action 1:** in `vector_search_statement` in `src/knowledge_service/retrieval/vector_search.py`, delete this line:

```python
        .prefix_with("MATERIALIZED")
```

**Action 2:** run `uv run pytest tests/test_vector_search.py -q`. Expected: `1 failed, 34 passed`: `test_the_reader_s_chunks_are_settled_before_any_ranking`.

**Action 3:** with the line still deleted, run the security contract from M07-T08:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_search_security_contract_integration.py tests/test_existence_leaks_integration.py -q
```

Expected: `186 passed`. Every result is still right; only the work, and so the time, may depend on hidden Chunks again. A timing leak never shows in a test of results, which is why the structural test exists.

**Action 4:** put the line back and run `uv run pytest tests/test_vector_search.py -q` again: `35 passed`. Compare with your last prediction in the evidence file.

### Step 6 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model named `M07-T11` as an owner of invariant 7; the trace now names this lesson's tests, and a new row records the timing channel.

**Action 1:** update two documents.

In `docs/architecture.md`:

**1.** Find:

````markdown
filters (Source, Document, media type, language, modified since) are typed
conditions from a fixed list; they are added to the same WHERE and only
narrow it.

```mermaid
````

Replace with:

````markdown
filters (Source, Document, media type, language, modified since) are typed
conditions from a fixed list; they are added to the same WHERE and only
narrow it. Since M07-T11 the statement settles the reader's Chunks in a
`MATERIALIZED` step before it ranks any, so its work, and its time, does not
grow with hidden Chunks near the question.

```mermaid
````

**2.** Find:

```markdown
        Search-->>Caller: NoCurrentEmbeddingSpace
    else current
        Search->>DB: one SELECT: space still current AND available AND a matching Access Grant AND every filter AND a real distance, ORDER BY distance, Chunk ID, LIMIT k + 1
        DB-->>Search: eligible Chunks with provenance and distance
        alt no rows and the space is no longer current
```

Replace with:

```markdown
        Search-->>Caller: NoCurrentEmbeddingSpace
    else current
        Search->>DB: one SELECT: first the Chunks that are available, granted, and match every filter (MATERIALIZED), then of those, in the current space with a real distance, ORDER BY distance, Chunk ID, LIMIT k + 1
        DB-->>Search: eligible Chunks with provenance and distance
        alt no rows and the space is no longer current
```

In `docs/threat-model.md`:

**1.** Find:

```markdown
| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
```

Replace with:

```markdown
| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same; black-box tests show that another User's best-matching Document changes nothing in the response bytes, headers, log records, or the model's prompt, and that a forbidden Chunk, Evidence, or Conversation ID gives the same result and log as a missing one | Authorization adversarial suite (M07-T15) |
| The time a search takes tells a reader how many hidden Chunks match their question | Vector search settles the reader's Chunks in a `MATERIALIZED` step before ranking any, so its work does not grow with hidden near Chunks: with 5,000 hidden exact matches the gap fell from about 10 ms to within noise, at the cost of about twice the time for a reader who can see 20,000 Documents | Lexical search still filters after its index, about 5 to 28 ms slower per 5,000 hidden matches; it reaches no reader yet, and must be fixed or bounded before lexical or hybrid retrieval joins a served path (M09); a hybrid branch deadline would turn that gap into a timed-out branch |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
```

**2.** Find:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

Replace with:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- `rate_message` and `load_message_feedback` take a Message ID with no owner check, and rating a missing Message fails differently from rating another User's; no route exposes them yet, and an owner-scoped wrapper must come first. Sparse search (the prototype) scores hidden Chunks in Python, so its time depends on them.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
uv run pytest tests/test_vector_search.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2785 passed for `make test-unit`, 1 more than M07-T10, and 641 passed for `make test-integration`, 5 more.

**Action 3:** complete the evidence file:

- **Design:** the black-box comparisons, what is masked and why, the materialized step, and what lexical search still leaks.
- **Verification:** the commands and counts, the timing numbers before and after, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why compare everything, why decide who may read before ranking, why accept the cost for large readers, and why lexical search waits; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T11 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T12, and commit with `m07-t11: eliminate existence leaks`.

## Reflection questions

1. The answer stream returns the same bytes whether bob's Document exists or not. Name two other things, besides the bytes, that a curious reader could watch, and say which this lesson checks.
2. Removing `MATERIALIZED` broke no test of results. Why is a timing leak invisible to such tests, and what kind of test catches it?
3. The fix doubled the time for a reader who sees 20,000 Documents. Who pays that cost, who benefits, and what would you change once the corpus grows to millions of Chunks?
4. A future endpoint answers "403 Forbidden" for another User's Conversation and "404 Not Found" for a missing one. What does a reader learn, and what should it answer instead?
5. bob has 5,000 Documents about a planned reorganisation that alice may not see. Describe how alice could try to learn that they exist through lexical search once it serves readers, and what the fix in this lesson would need to become there.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] Vector search settles the reader's Chunks in a `MATERIALIZED` step before ranking, and its tests still pass (43).
- [ ] The structural test pins that order (35 in the file).
- [ ] The black-box tests pass against PostgreSQL (5): stream, logs, prompt, and forbidden versus missing IDs.
- [ ] You ran the deliberate break, saw the structural test fail while the contract passed, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe the fix, its cost, and the lexical limit, and the trace names the new tests.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
