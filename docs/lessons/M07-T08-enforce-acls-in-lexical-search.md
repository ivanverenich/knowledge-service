# M07-T08 — Enforce ACLs in lexical search

Task: [M07-T08 — Enforce ACLs in lexical search](../curriculum/milestones/M07-identity-and-authorization/M07-T08-enforce-acls-in-lexical-search.md)

## What you will do

This lesson proves that search by words obeys exactly the same permission rule as search by meaning, for English, Ukrainian, and mixed questions, and before any ranking or highlighting. The lexical query has used the same permission check as the vector query since M05; what was missing is one shared test contract that both kinds of search must pass. At the end, the same thirty hostile corpora run through six searches, and each must return exactly what the reader may see.

- **One contract, six searches:** vector search; English; Ukrainian by topic words; Ukrainian with web-search syntax; mixed; and mixed with syntax. Each route is asserted, so the contract cannot silently skip one.
- **The same rule, written once:** `may_read`, the plain Python statement of who may see what, judges every search.
- **Before ranking and highlighting:** a SQL test shows the permission check sits inside the ranked query, before its sort and limit, and that highlighting runs only on the rows that survived, for every configuration and both ways of reading Ukrainian.
- **No production code changes.** The search code already does this; the lesson's work is the proof, and the deliberate break shows the proof bites.

| The task asks for | Where it happens |
|---|---|
| Identical permission semantics for English and Ukrainian lexical branches | Step 3: the shared contract runs every branch against `may_read` |
| Before ranking or highlighting | Step 2: the compiled-SQL test |
| Tests: success, highest-risk edge, typed failure or denial | Steps 2 and 3 |
| Shared security contract tests run against dense and lexical search | Step 3: `tests/test_search_security_contract_integration.py` |
| Authorization preserved before data leaves storage; no seeded text | Step 3: no hidden name in text, title, external ID, heading, or highlight |
| Update only what the change made inaccurate | Step 5: `docs/threat-model.md` |

This lesson includes coding, but only tests: Steps 2 and 3 write them. Steps 1, 4, and 5 do not, though Step 5 edits one document.

Terms used in this lesson:

- **Lexical search:** search by words, using PostgreSQL full-text search. English uses word stems ("leaves" matches "leave"); Ukrainian uses words as typed, lowercased.
- **Topic words:** for a Ukrainian question without search syntax, the question minus its function words; a Chunk must contain at least two of them.
- **As typed:** a question with web-search syntax (`"quoted phrase"`, `or`, `-word`) is read literally instead. It is a different branch of the SQL.
- **Highlighting:** `ts_headline` marks where the question's words occur in a Chunk's text, so the interface can show them. It reads the whole text, so it must only ever see allowed rows.
- **Security contract:** one set of rules every search must pass, written once and run against each search, like one safety inspection applied to every door of a building.
- **Route:** which configurations a question is matched with: English, simple (Ukrainian), or both.

## Before you begin

This section explains the choices behind the tests, so each step makes sense when you reach it.

- **Why no code change.** `lexical_search_statement` puts `eligible_chunks_predicate` in the WHERE of its ranked subquery, next to the match condition, before `ORDER BY` and `LIMIT`; the outer query only calls `ts_headline` on the rows that subquery kept. M07-T07 made that predicate check the reader and join by itself. The review below tried hard to break lexical search and could not.
- **Why a contract instead of more examples.** The vector and lexical tests were written separately, each with its own cases. A contract states the rules once and runs them against every search, so a new search, or a new branch of an old one, is held to exactly the same standard.
- **Every branch, not just every function.** The Ukrainian search builds a different SQL condition for topic words than for text with search syntax. A check dropped from only one branch would pass any test that never takes that branch, so the contract has a typed Ukrainian search and a typed mixed search, and the SQL test compiles every combination.
- **The corpora are hard for words too.** Hidden Documents often hold the question's words in their title and repeated in their text, so they would win the ranking if the check were missing. A separate test proves that, by vector and by words, in most seed and reader pairs.
- **The best result is checked against the rule, not against itself.** For vector search, the first result must be as near as the nearest Document the reader may see. For lexical search, it must have the question's words in its title whenever any allowed Document does, since a title match always outranks text matches.
- **It replaces last lesson's corpus test.** `tests/test_vector_acl_adversarial_integration.py` from M07-T07 covered only vector search. The contract covers it and more, so this lesson deletes that file and moves its two extra tests into the contract.
- **What the review found.** An independent adversarial review ran 72 hostile questions in five language settings and two limits: quotes, `or`, exclusions, stop words only, 33 dashes, decomposed letters, apostrophe variants, tsquery syntax, invisible and right-to-left characters, 1,000-character questions. It checked results, scores, highlights, and `truncated` before and after inserting hidden Documents, and 6.7 MB of DEBUG logs. Nothing hidden leaked, and nothing depended on hidden rows. It found these gaps in the tests, all fixed:
  - The contract never took the Ukrainian as-typed branch. A mutation that dropped the check only there survived every test. Now the contract has `ukrainian_typed` and `both_typed`, and the SQL test compiles quoted text.
  - The contract checked the best result only against its own longer list, which a wrong sort order also passes. Now it checks against the reference model.
- **Known limits.** Sparse search, the BGE-M3 prototype, uses the same predicate but is outside the contract. `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it. Each corpus Document has one Chunk and no headings. Lexical and hybrid retrieval are not in the answer stream yet.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 4 checks the last prediction.

Create `docs/evidence/M07-T08-enforce-acls-in-lexical-search.md`:

```markdown
# M07-T08 — Enforce ACLs in lexical search evidence

## Invariant

Every search by words, English, Ukrainian by topic words, Ukrainian as typed,
or both at once, applies the same eligibility rule as vector search: a Chunk
is matched, ranked, counted, and highlighted only if its Document is
available and granted to everyone, to the reader, or to one of the reader's
Groups, decided in the ranked query's WHERE before its ORDER BY and LIMIT.
One security contract runs the same adversarial corpora through vector and
lexical search, and each must return exactly what the reference rule allows,
with no hidden title, text, or highlight.

## Expected failing check

Before the change, `uv run pytest tests/test_search_security_contract_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_search_security_contract_integration.py`.

## Predictions

- What English lexical search with limit 1 returns to a reader when a hidden Document has the question's words in its title and the reader's only Document has them in its text:
- Whether `ts_headline` ever reads the text of that hidden Document:
- Whether `"щорічна відпустка"` in quotes and `щорічна відпустка` without them run the same SQL condition, and why that matters for testing:
- Which tests fail if `eligibility` is removed from the ranked query's WHERE in `lexical_search_statement`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the permission check comes before ranking and highlighting

**In plain words:** this test builds the SQL for every lexical search shape, without running it, and reads the text of the query. It checks that the access grants are read inside the query that sorts and cuts the results, before the sort, and that highlighting only happens in the outer query, on what is left. It covers English, Ukrainian, and both, each with plain words and with search syntax.

In `tests/test_lexical_search.py`:

**1.** Find:

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    MAX_SIMPLE_WORDS,
    UKRAINIAN_STOP_WORDS,
    InvalidLexicalQuery,
```

Replace with:

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.language import Language
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    MAX_SIMPLE_WORDS,
    UKRAINIAN_STOP_WORDS,
    InvalidLexicalQuery,
```

**2.** Find:

```python
    # the rows LIMIT kept, never on every match.
    assert outer.count("ts_headline(") == 2
    assert "ts_headline" not in ranked
    assert "LIMIT" in ranked


def test_ties_are_broken_by_chunk_id_before_the_limit_cuts() -> None:
    statement = english_search_statement(text="leave", eligibility=sa.true(), limit=5)

    sql = str(statement.compile(dialect=postgresql.dialect()))
    ranked = sql.split("FROM (SELECT", 1)[1]
    order = ranked[ranked.index("ORDER BY") : ranked.index("LIMIT")]
```

Replace with:

```python
    # the rows LIMIT kept, never on every match.
    assert outer.count("ts_headline(") == 2
    assert "ts_headline" not in ranked
    assert "LIMIT" in ranked


@pytest.mark.parametrize(
    "text",
    ["leave", '"leave" -sick', "щорічна відпустка", '"щорічна відпустка"'],
    ids=["words", "syntax", "topic-words", "typed"],
)
@pytest.mark.parametrize(
    "configurations",
    [
        (LexicalConfiguration.ENGLISH,),
        (LexicalConfiguration.SIMPLE,),
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
    ],
    ids=["english", "ukrainian", "both"],
)
def test_the_permission_check_comes_before_ranking_and_highlighting(
    configurations: tuple[LexicalConfiguration, ...], text: str
) -> None:
    # Each configuration, and `simple` both by topic words and as typed,
    # builds its own match; the check must sit beside every one of them.
    statement = lexical_search_statement(
        configurations,
        text=text,
        eligibility=eligible_chunks_predicate(CONTEXT),
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    outer, rest = sql.split("FROM (SELECT", 1)
    ranked = rest.split(") AS ranked", 1)[0]

    # The grants are read in the ranked query's WHERE, before its own last
    # ORDER BY and LIMIT; the outer query only highlights the rows kept.
    assert "access_grants" not in outer
    assert ranked.index("access_grants") < ranked.rindex("ORDER BY")
    assert ranked.rindex("ORDER BY") < ranked.rindex("LIMIT")
    assert outer.count("ts_headline(") == len(configurations)


def test_ties_are_broken_by_chunk_id_before_the_limit_cuts() -> None:
    statement = english_search_statement(text="leave", eligibility=sa.true(), limit=5)

    sql = str(statement.compile(dialect=postgresql.dialect()))
    ranked = sql.split("FROM (SELECT", 1)[1]
    order = ranked[ranked.index("ORDER BY") : ranked.index("LIMIT")]
```

The test passes at once, because the code is already right; it is there to fail the day someone moves the check. Step 4 shows it failing.

**Check:** `uv run pytest tests/test_lexical_search.py -q` gives `80 passed`.

### Step 3 — Coding step: one security contract for every search

**In plain words:** this file builds thirty small hostile corpora, where Documents the reader may not see are usually the best matches by meaning and by words, and runs each one through six searches. For four different readers, every search must return exactly the Documents a plain Python rule allows, put the right one first, and never show a hidden Document's name in any text, title, heading, or highlight. It replaces last lesson's vector-only version.

| Part | What it does |
|---|---|
| `Planted`, `corpus` | One seeded corpus: who may read each Document, its vector, whether its title holds the words, how often its text repeats them, and whether it is revoked or tombstoned |
| `may_read` | The reference rule, in a few lines of Python |
| `SEARCHES` | Six searches; each lexical one asserts the route it took |
| `store` | Publishes, embeds, revokes, and tombstones the corpus |
| `shown` | Everything in a Candidate a person could read, highlights included |
| The three tests | The contract; proof the corpora are hard; a query that forgets the join |

Delete last lesson's vector-only file, which this contract replaces:

```bash
git rm tests/test_vector_acl_adversarial_integration.py
```

Create `tests/test_search_security_contract_integration.py`:

```python
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
```

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_search_security_contract_integration.py -q
```

Expected: `182 passed`: six searches times thirty seeds, the corpus check, and the forgotten-join test. It takes about a minute.

### Step 4 — No coding in this step: break it on purpose

This step removes the permission check from the lexical query, watches both kinds of test fail, and puts it back.

**Action 1:** in `lexical_search_statement` in `src/knowledge_service/retrieval/lexical_search.py`, change

```python
        .where(matched, eligibility)
```

to

```python
        .where(matched)
```

**Action 2:** run

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_search_security_contract_integration.py tests/test_lexical_search.py -q
```

Expected: `162 failed, 100 passed`:

- `test_the_permission_check_comes_before_ranking_and_highlighting`, all 12 cases
- `test_every_search_returns_exactly_what_the_reader_may_see`, all 30 seeds of each of the five lexical searches; the 30 vector ones still pass

**Action 3:** put the check back and run the command again: `262 passed`.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 5 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model named the deleted test file and `M07-T08` as owners of invariant 2, and said lexical search would join the answer stream in this lesson; all three change.

**Action 1:** update the threat model.

In `docs/threat-model.md`:

**1.** Find:

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

Replace with:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
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
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

Replace with:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
uv run pytest tests/test_lexical_search.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2762 passed for `make test-unit`, 12 more than M07-T07, and 621 passed for `make test-integration`, 150 more.

**Action 3:** complete the evidence file:

- **Design:** where the check sits in the lexical query, why no code changed, and what the contract runs and asserts.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why a shared contract, why every branch needs its own search in it, and why the best result is checked against the reference model; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T08 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T09, and commit with `m07-t08: enforce acls in lexical search`.

## Reflection questions

1. A new search is added next year, say by image captions. What does the contract give its author for free, and what one thing must they add to be covered?
2. Someone speeds up highlighting by computing `ts_headline` for every matching row first and then sorting. What could go wrong for permissions and for cost, and which test would catch it?
3. The Ukrainian search has two SQL branches. Why did a test suite with dozens of Ukrainian cases still miss a check removed from only one of them?
4. The contract checks that the first lexical result has a title match whenever an allowed Document does. Why not check the exact `ts_rank` order instead?
5. A hidden Document is titled "Щорічна відпустка 2027" and alice may not read it. Write what each of the six searches returns to her if her only Document mentions leave once in its text.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] The SQL test shows the check before ranking and highlighting for every configuration and both ways of reading Ukrainian (80 in the file).
- [ ] The vector-only corpus file from M07-T07 is deleted, and the shared contract passes against PostgreSQL (182).
- [ ] You ran the deliberate break, saw the 162 tests fail, and undid it.
- [ ] `docs/threat-model.md` names the new tests, the contract, and its limits.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
