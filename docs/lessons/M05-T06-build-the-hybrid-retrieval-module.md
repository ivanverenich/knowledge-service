# M05-T06 — Build the hybrid retrieval module

Source task: [M05-T06](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T06-build-the-hybrid-retrieval-module.md)

## What you will do

The project has two searches and a way to merge their lists, but nothing that runs them together for one Question. This lesson builds that piece. It starts both searches at the same moment, gives them one shared time limit, merges what comes back, and reports how each search did. It also decides what happens when one search is slow or broken.

- A new module, `hybrid.py`, holds `retrieve_hybrid` and two small adapters, `dense_branch` and `lexical_branch`, that plug the real searches in.
- Both searches run at the same time, each on its own database connection, so the answer takes as long as the slower one, not the sum of both.
- One **deadline** covers both. A search still running when time is up is stopped, and its database query is cancelled on the server.
- If one search times out or fails, the other one's results are still returned and marked as **degraded**. If none succeeds, a typed error says so.
- Each result carries **diagnostics**: for every search, whether it succeeded, timed out, or failed, how long it took, and how many Candidates it found.

| The task asks for | Where it happens |
|---|---|
| Run dense and lexical retrieval concurrently | Step 2: `retrieve_hybrid` starts every branch in one `asyncio.TaskGroup` |
| Under one deadline | Step 2: one absolute deadline, applied to each branch with `asyncio.timeout_at` |
| Fuse results | Step 2: `fuse_ranked_candidates` from M05-T05, then cut to `limit` |
| Attach diagnostics | Step 2: `BranchDiagnostics` per branch, `degraded` on the result |
| Define partial-failure behavior | Step 2: the policy table in "Before you begin" |
| Authorization context reaches both branches | Step 3: `test_every_branch_gets_the_same_question_reader_and_deadline`; Step 4: `test_both_real_branches_search_as_the_reader` |
| Timeout policy is observable | Step 3: `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported`; Step 4: the slow-query test |
| Success, the highest-risk edge, one typed failure | Steps 3–4: both branches fused; a slow query cancelled on the server; `HybridRetrievalUnavailable` and `InvalidHybridQuery` |

This lesson includes coding: Steps 2, 3, and 4 write code. Steps 1, 5, and 6 do not.

Terms used in this lesson:

- **Branch:** one way of searching. Here there are two: dense (vector search, by meaning) and lexical (by words).
- **Concurrently:** at the same time. Like two people each fetching one book in a library, instead of one person fetching both in turn.
- **Deadline:** one moment in time by which every branch must have answered. It is fixed once at the start, so a branch that starts late does not get extra time.
- **Cancel:** stop a running task. In `asyncio`, cancelling raises `CancelledError` inside the task at its next `await`, and the code around it cleans up.
- **Degraded:** a result built from fewer branches than were asked for, so some good Chunks may be missing.
- **Diagnostics:** a short report per branch, safe to log: status, time taken, number found, and the error type if it failed.

## Before you begin

This section explains the decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** `search_similar_chunks` needs a query vector, so the dense branch must first embed the Question through an `EmbeddingModel`, a network call. `search_lexical_chunks` needs only the text. Both take an `AsyncConnection` and an `AuthorizationContext`, and both return Candidates plus `truncated`. `fuse_ranked_candidates` merges ranked lists by position.
- **Why each branch gets its own connection.** One database connection runs one query at a time. To search at the same moment, each branch opens its own connection with `runtime.transaction()`. The pool has room for this: by default it keeps 5 connections and may open 5 more.
- **Why branches are plain functions.** `retrieve_hybrid` does not know how a branch searches. It takes a mapping from `RetrievalBranch` to a **branch search**: an async function that receives a `BranchRequest` and returns something with `candidates` and `truncated`. The real ones come from `dense_branch` and `lexical_branch`. Tests pass small fakes instead, so they can make a branch slow, broken, or quick on purpose, without a database. This is the task's first hint: shrink the interface before reaching for mocks.
- **What every branch is asked.** The same `BranchRequest`: the Question, its stated language, the reader's `AuthorizationContext`, the limit, a request ID, and the time left until the deadline. The dense branch passes that time on to the embedding model, which has its own timeout. The Question is kept out of the request's repr.
- **One deadline, not one per branch.** The deadline is computed once, as "now plus `timeout_seconds`". Each branch runs inside `asyncio.timeout_at(deadline)`, which cancels it when that moment passes. Cancelling also cancels the database query on the server, because psycopg sends PostgreSQL a cancel request when its task is cancelled. Step 4 proves this with a real slow query. A branch can still answer late if it ignores the cancel or blocks the event loop with slow synchronous work. Such a late answer is dropped and reported as timed out.
- **The partial-failure policy.** Each branch ends in one of three states:

  | What happened | Status | What the caller gets |
  |---|---|---|
  | The branch answered in time | `succeeded` | its Candidates go into the fusion |
  | The deadline passed first | `timed_out` | the branch is cancelled and left out |
  | It answered or failed only after the deadline | `timed_out` | its answer is dropped |
  | It raised a known dependency or refusal error in time | `failed` | the error's type name is recorded, the branch is left out |
  | It raised any other error | not caught | the error propagates and the other branch is cancelled |

  The known errors are a short, deliberate list:
  - any embedding error, such as the provider being down or slow;
  - `NoCurrentEmbeddingSpace`, when the vector store holds another model, as during a model change;
  - `InvalidLexicalQuery`, when word search refuses this Question, for example one made only of dashes;
  - `OperationalError` and `InterfaceError` from SQLAlchemy, a lost connection or a query the server stopped, and the pool's `TimeoutError` when no connection is free;
  - `ConnectionError`, and a branch's own `TimeoutError`.

  When at least one branch succeeds, the result is fused from those that did, and `degraded` is True if any did not. When none succeeds, `retrieve_hybrid` raises `HybridRetrievalUnavailable`, which carries the same diagnostics.
- **Why bugs are not caught.** A `RuntimeError` in a branch, or SQL that no longer compiles after a migration (`ProgrammingError`), is a mistake in our code, not a slow service. Hiding it as `failed` would make a broken branch look like an outage forever, and the service would quietly run on one branch. So it propagates. Because the branches run in an `asyncio.TaskGroup`, the other branch is cancelled first, and the caller gets an `ExceptionGroup` that holds the error.
- **Checked first.** `InvalidHybridQuery` is raised before any branch starts for: a blank Question, one over 1,000 characters (the most word search accepts), or one holding a NUL or a broken character; a reader that is not an `AuthorizationContext`; a language that is not a `Language`; a bad limit, timeout, or `k`; and an empty or wrong set of branches. Without this, a blank Question would make both branches fail, and the caller would see "unavailable" for its own mistake.
- **Limit and `truncated`.** Each branch is asked for `limit` Candidates. The fused list can hold up to twice that, so it is cut to `limit`. `truncated` is True when the fused list was cut, or when any branch that succeeded said more exist.
- **Permissions.** `retrieve_hybrid` hands the same `AuthorizationContext` to every branch. Each branch applies it in its own SQL statement, before ranking and `LIMIT`, and fusion only reorders what came back. So no path can add a Chunk the reader may not see. A degraded result is still fully permission-checked; it just has fewer Chunks.
- **Quiet.** Diagnostics name an error by its type only. A database error's message can repeat the Question, as earlier lessons found, so messages never reach the diagnostics. `HybridRetrievalUnavailable` has a fixed message and no chained cause.
- **What the review found.** An independent review attacked the prototype. It found no way to leak a hidden Chunk, no leaked connection, and no hang past the deadline when the database was involved. It did find five problems, each now fixed, with tests that fail on the old code:
  - Bugs were hidden. Every SQLAlchemy error and every `OSError` counted as `failed`, so SQL broken by a migration, or a missing file, looked like an outage. Only the short list of dependency and refusal errors above counts as `failed` now; anything else propagates.
  - Late answers counted. A branch that caught the cancel and answered anyway, or blocked the event loop past the deadline, was reported as `succeeded` and fused. Now anything that ends after the deadline is `timed_out`, and its answer is dropped.
  - An error raised while a branch was being cancelled, such as a failed rollback, showed as `failed`. It is `timed_out` now.
  - A valid-looking Question could never be searched by words: up to 4,000 characters passed the check, but word search refuses more than 1,000. The limit is now 1,000, and a NUL or broken character is refused too.
  - Some bad input slipped past the first check: a timeout too large for a float, a language given as a string, a missing reader, or a branch that is not a function. All raise `InvalidHybridQuery` now.

  The review also showed that some tests could not fail: a constant `elapsed_seconds`, a per-branch `truncated` that was always False, and a dense branch that held a connection during the embedding call all passed. Each now has a test that catches it.

  Some findings are known limits, written in the evidence instead of fixed:
  - asyncio cannot stop code that blocks the event loop or ignores a cancel. Such a branch still delays the whole call, although its answer is dropped.
  - The pool's `TimeoutError` and a branch's own `TimeoutError` show the same name in the diagnostics.
  - A branch that returns more than `limit` Candidates is not trimmed before fusion. The final list is still cut to `limit`.
  - `KeyboardInterrupt` and `SystemExit` escape on their own, as they do everywhere in asyncio.
  - The API accepts Questions up to 4,000 characters, but retrieval refuses more than 1,000. The task that connects retrieval to the API must settle this.

  The review also confirmed what held. 40 random Questions over 12 hidden Documents never returned one. With a pool of one connection and seven calls at once, every call ended by its deadline, and the pool recovered afterwards.

- **Running the tests.** The unit tests need no database. The integration tests do; read the database URL inline in each command, so the password is never printed or saved:

  ```bash
  uv run pytest tests/test_hybrid_retrieval.py -q
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_hybrid_retrieval_integration.py -q -m integration
  ```

- **Files you will touch:**
  - new `src/knowledge_service/retrieval/hybrid.py`
  - new `tests/test_hybrid_retrieval.py` and `tests/test_hybrid_retrieval_integration.py`
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

No migration and no settings change this time.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results in Step 5.

**Action:** create `docs/evidence/M05-T06-hybrid-retrieval.md`:

```markdown
# M05-T06 — Hybrid retrieval evidence

Completed: pending

## Invariant

`retrieve_hybrid` starts every branch at the same time with the same
Question, reader, and limit, and gives them one deadline. A branch that
answers in time is fused; one that times out is cancelled, its database
query with it; one that fails with a dependency or refusal error is left
out and named by its error type. If no branch succeeds,
`HybridRetrievalUnavailable` is raised. Every returned Chunk passed the
reader's permission check in its branch's own SQL statement. At most
`limit` Candidates come back, and no Question text or error message
appears in diagnostics, errors, or reprs.

## Expected failing check

Before the change, `tests/test_hybrid_retrieval.py` fails to import
`knowledge_service.retrieval.hybrid`.

## Predictions

Before running anything, write what you expect:

- Dense answers with Chunks 1, 2, 3 and lexical with 3, 4; the limit is 3.
  Which Chunks come back, in what order, and is the result truncated?
- Lexical never answers and the deadline is 0.2 seconds. What does the
  caller get, and what does the lexical diagnostic say?
- The embedding service is down, and lexical finds Chunk 4. What comes
  back, and is the result degraded?
- The embedding service is down and lexical never answers. What does the
  caller get?
- A branch is cancelled in the middle of a 30-second `pg_sleep` query.
  What happens to that query on the server?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: write the hybrid retrieval module

This step builds the function that runs both searches together and decides what to do when one of them is slow or broken.

**In plain words:** This file is the conductor of the two searches. It first checks the Question and the settings, then starts both searches at the same moment and gives them one shared time limit. When each search ends, it notes whether it succeeded, ran out of time, or failed, and how long it took. Finally it merges the lists of the searches that succeeded and returns them with that report, or raises a clear error if none did.

| Part | What it does |
|---|---|
| `_BRANCH_FAILURES` | the errors that mean "this branch cannot answer now", recorded as `failed` |
| `InvalidHybridQuery`, `HybridRetrievalUnavailable` | the two typed errors, both `HybridRetrievalError`s; the second carries the diagnostics |
| `BranchStatus` | `succeeded`, `timed_out`, `failed` |
| `BranchRequest` | what every branch is asked; the Question is out of its repr |
| `BranchResult`, `BranchSearch` | the shape a branch returns, and the type of a branch function |
| `BranchDiagnostics` | the report on one branch |
| `HybridRetrieval` | the fused Candidates, `limit`, `k`, `truncated`, the diagnostics, and `degraded` |
| `retrieve_hybrid` | checks, sets the deadline, runs the branches, fuses, cuts to `limit` |
| `dense_branch`, `lexical_branch` | wrap the real searches as branch functions, each on its own connection |
| `_run` | runs one branch under the deadline and turns how it ended into diagnostics |
| `_check`, `_encodable` | refuse bad input before anything starts |

A few lines need a word of explanation:

- `asyncio.TaskGroup` starts both branches as tasks and waits for both. If one raises an error that `_run` does not catch, the group cancels the other and raises an `ExceptionGroup`. If the caller is cancelled, the group cancels both. So no branch is ever left running in the background.
- `loop.time() + timeout_seconds` fixes the deadline once. `asyncio.timeout_at(deadline)` in `_run` uses that same moment for every branch.
- After a branch ends, `budget.expired() or loop.time() > deadline` asks whether the deadline has passed. If it has, whatever the branch did counts as `timed_out`: the cancel itself, an error raised while being cancelled, or a late answer. If not, a known error counts as `failed`, including the branch's own `TimeoutError`.
- `replace(request, timeout_seconds=remaining)` gives each branch the time actually left, for the embedding call's own timeout.
- `type(failure).__name__` records only the error's class name, never its message.
- `type(timeout) not in (int, float)` rejects `True` and strings, as elsewhere in the project. `0 < timeout <= sys.float_info.max` rejects NaN, infinity, and an int too large to become a float deadline.
- `MAX_QUERY_CHARACTERS` comes from lexical search, so a Question that word search would always refuse is refused up front instead.

**Action:** create `src/knowledge_service/retrieval/hybrid.py`:

```python
"""Search by meaning and by words at once, under one deadline, and fuse.

`retrieve_hybrid` starts every branch at the same time, each with its own
database connection, and gives them all one deadline. Each branch that
answers in time contributes its ranked Candidates; Reciprocal Rank Fusion
(see `retrieval.fusion`) combines them into one list.

What a caller can rely on in a `HybridRetrieval`:

- **Checked first.** A bad question, reader, language, limit, timeout,
  `k`, or set of branches raises `InvalidHybridQuery` before any branch
  starts. A question must also fit the lexical search's limit of
  `MAX_QUERY_CHARACTERS`, so no valid question is refused by a branch for
  its length.
- **One deadline.** Every branch must finish within `timeout_seconds` of
  the start. A branch still running then is cancelled, its database query
  with it, and is reported as timed out. A branch that answers only after
  the deadline, because it ignored the cancel or blocked the event loop,
  is also reported as timed out, and its answer is dropped.
- **Partial failure.** A branch that times out, or fails because a
  dependency is down or slow (`EmbeddingError`, `NoCurrentEmbeddingSpace`,
  `OperationalError`, `InterfaceError`, a pool `TimeoutError`, a
  `ConnectionError`, or its own `TimeoutError`) or because it refuses the
  question (`InvalidLexicalQuery`), is reported in `branches` and left out
  of the fusion. The others still count, and `degraded` says the result is
  incomplete. When no branch succeeds, `HybridRetrievalUnavailable` is
  raised with the same diagnostics.
- **Bugs are not hidden.** Any other error propagates. Raised inside a
  branch, it cancels the other branches first and arrives inside the
  `ExceptionGroup` of the task group; a malformed branch result raises
  from fusion.
- **Eligible.** Each branch applies the reader's permission check in its
  own SQL statement, and fusion only reorders what the branches returned.
- **Bounded.** Each branch asks for `limit` Candidates; at most `limit`
  fused Candidates come back, and `truncated` says whether more exist.
- **Quiet.** Diagnostics name an error by its type, never by its message,
  which may repeat the question; the question is kept out of every repr.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusedCandidate,
    RetrievalBranch,
    fuse_ranked_candidates,
)
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    NoCurrentEmbeddingSpace,
    search_similar_chunks,
)

# Errors that mean a branch cannot answer this Question now: a dependency
# is down or slow, or the branch refuses this Question. Anything else, such
# as SQL that no longer compiles or a missing file, is a bug and propagates.
_BRANCH_FAILURES = (
    EmbeddingError,
    NoCurrentEmbeddingSpace,
    InvalidLexicalQuery,
    exc.OperationalError,
    exc.InterfaceError,
    exc.TimeoutError,
    ConnectionError,
    TimeoutError,
)


class HybridRetrievalError(Exception):
    """Hybrid retrieval could not return Candidates."""


class InvalidHybridQuery(HybridRetrievalError):
    """The question or a setting is not valid; no branch was started."""


class HybridRetrievalUnavailable(HybridRetrievalError):
    """No branch returned Candidates in time; `branches` says why."""

    def __init__(self, branches: tuple[BranchDiagnostics, ...]) -> None:
        self.branches = branches
        super().__init__("no retrieval branch succeeded")


class BranchStatus(StrEnum):
    """How one branch ended."""

    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BranchRequest:
    """What every branch is asked: the same question, reader, and deadline.

    `timeout_seconds` is the time left until the shared deadline, for a
    branch that calls a provider with its own timeout.
    """

    question: str = field(repr=False)
    language: Language | None
    context: AuthorizationContext
    limit: int
    request_id: UUID
    timeout_seconds: float


class BranchResult(Protocol):
    """What a branch returns: ranked Candidates and whether more exist."""

    @property
    def candidates(self) -> Sequence[Candidate]: ...

    @property
    def truncated(self) -> bool: ...


type BranchSearch = Callable[[BranchRequest], Awaitable[BranchResult]]


@dataclass(frozen=True, slots=True)
class BranchDiagnostics:
    """How one branch ended, how long it took, and what it found.

    `error` is the type name of the error that failed the branch, or None.
    """

    branch: RetrievalBranch
    status: BranchStatus
    elapsed_seconds: float
    candidates: int
    truncated: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class HybridRetrieval:
    """The fused Candidates of one Question, and how each branch ended."""

    limit: int
    k: int
    candidates: tuple[FusedCandidate, ...]
    truncated: bool
    branches: tuple[BranchDiagnostics, ...]

    @property
    def degraded(self) -> bool:
        """True when a branch timed out or failed, so Candidates may be missing."""
        return any(b.status is not BranchStatus.SUCCEEDED for b in self.branches)


@dataclass(slots=True)
class _Ended:
    diagnostics: BranchDiagnostics
    result: BranchResult | None


async def retrieve_hybrid(
    question: str,
    *,
    context: AuthorizationContext,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout_seconds: float,
    request_id: UUID,
    language: Language | None = None,
    k: int = DEFAULT_RRF_K,
) -> HybridRetrieval:
    """Run every branch at once under one deadline and fuse what returns."""
    _check(
        question,
        context=context,
        language=language,
        branches=branches,
        limit=limit,
        timeout=timeout_seconds,
        k=k,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    request = BranchRequest(
        question=question,
        language=language,
        context=context,
        limit=limit,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_run(branch, branches[branch], request, deadline))
            for branch in RetrievalBranch
            if branch in branches
        ]
    ended = [task.result() for task in tasks]
    diagnostics = tuple(item.diagnostics for item in ended)
    succeeded = {
        item.diagnostics.branch: item.result
        for item in ended
        if item.result is not None
    }
    if not succeeded:
        raise HybridRetrievalUnavailable(diagnostics)
    fused = fuse_ranked_candidates(
        {branch: tuple(result.candidates) for branch, result in succeeded.items()},
        k=k,
    )
    return HybridRetrieval(
        limit=limit,
        k=k,
        candidates=fused[:limit],
        truncated=len(fused) > limit
        or any(result.truncated for result in succeeded.values()),
        branches=diagnostics,
    )


def dense_branch(runtime: DatabaseRuntime, model: EmbeddingModel) -> BranchSearch:
    """Embed the question, then search the current Embedding Space."""

    async def search(request: BranchRequest) -> BranchResult:
        batch = await model.embed(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_similar_chunks(
                connection,
                identity=batch.identity,
                query=batch.vectors[0],
                context=request.context,
                limit=request.limit,
            )

    return search


def lexical_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """Search the stored words, routed by the question's language."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            return await search_lexical_chunks(
                connection,
                text=request.question,
                context=request.context,
                limit=request.limit,
                language=request.language,
            )

    return search


async def _run(
    branch: RetrievalBranch,
    search: BranchSearch,
    request: BranchRequest,
    deadline: float,
) -> _Ended:
    loop = asyncio.get_running_loop()
    started = loop.time()
    remaining = max(deadline - started, 0.0)
    budget = asyncio.timeout_at(deadline)
    status = BranchStatus.FAILED
    error: str | None = None
    result: BranchResult | None = None
    try:
        async with budget:
            result = await search(replace(request, timeout_seconds=remaining))
        status = BranchStatus.SUCCEEDED
    except _BRANCH_FAILURES as failure:
        error = type(failure).__name__
    # Past the deadline, whatever the branch did counts as timed out: the
    # cancel itself, an error raised while it was being cancelled, or a late
    # answer from a branch that ignored the cancel or blocked the event loop.
    if budget.expired() or loop.time() > deadline:
        status, error, result = BranchStatus.TIMED_OUT, None, None
    return _Ended(
        BranchDiagnostics(
            branch=branch,
            status=status,
            elapsed_seconds=loop.time() - started,
            candidates=len(result.candidates) if result is not None else 0,
            truncated=result.truncated if result is not None else False,
            error=error,
        ),
        result,
    )


def _check(
    question: str,
    *,
    context: AuthorizationContext,
    language: Language | None,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout: float,
    k: int,
) -> None:
    if type(question) is not str or not question.strip():
        raise InvalidHybridQuery("the question must be text that is not blank")
    if len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidHybridQuery(
            f"the question must be at most {MAX_QUERY_CHARACTERS} characters"
        )
    # PostgreSQL text cannot hold NUL, and UTF-8 cannot hold a lone surrogate.
    if "\x00" in question or not _encodable(question):
        raise InvalidHybridQuery("the question must be valid text")
    if type(context) is not AuthorizationContext:
        raise InvalidHybridQuery("context must be an AuthorizationContext")
    if language is not None and type(language) is not Language:
        raise InvalidHybridQuery("language must be a Language or None")
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidHybridQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    # The deadline is a float, so a huge int must not overflow it.
    if type(timeout) not in (int, float) or not 0 < timeout <= sys.float_info.max:
        raise InvalidHybridQuery("timeout_seconds must be finite and positive")
    if type(k) is not int or k < 1:
        raise InvalidHybridQuery("k must be a positive int")
    # Checked at run time too, because a caller may pass any object.
    if not isinstance(cast(object, branches), Mapping) or not branches:
        raise InvalidHybridQuery("at least one branch is required")
    for branch, search in branches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise InvalidHybridQuery(
                "every branch must map a RetrievalBranch to a search"
            )


def _encodable(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True
```

**Check:**

```bash
uv run ruff check src/knowledge_service/retrieval/hybrid.py
uv run pyright src/knowledge_service/retrieval/hybrid.py
```

Expected: no issues, and `0 errors`.

### Step 3 — Coding step: test the policy with fake branches

This step proves every rule of the policy, using fake branches whose timing you control exactly.

**In plain words:** These tests replace the real searches with small fakes. A fake can answer at once, wait forever, raise an error, or tell the other fake it has started. With them the tests check that both searches run at the same time, that a slow one is stopped at the deadline, that a broken one is reported and skipped, that a real bug is not hidden, and that bad input is refused before anything starts.

`FakeBranch` is the fake. It records every `BranchRequest` it gets, so a test can check what it was asked. `wait_for` makes it wait for an event before answering; `_never()` gives an event nobody sets, so the branch waits until it is cancelled. `started` is set as soon as the branch starts, which lets two fakes wait for each other. `cancelled` turns True when the fake is cancelled. `_ranked(1, 2, 3)` makes a ranked list with Chunk 1 first, as in M05-T05. Three small functions, such as `_answers_despite_the_cancel`, play branches that misbehave at the deadline.

| Test | What it proves |
|---|---|
| `test_both_branches_are_fused_and_reported` | Success: the fused order, contributions, and diagnostics for both branches |
| `test_every_branch_gets_the_same_question_reader_and_deadline` | The reader's context, the Question, language, limit, and request ID reach both branches |
| `test_the_branches_run_at_the_same_time` | Each fake waits until the other has started; one after the other, both would miss the deadline |
| `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported` | For either branch: cancelled, `timed_out`, the other's results kept, `degraded` |
| `test_a_branch_that_fails_is_reported_by_its_error_type` | Eight kinds of failure become `failed` with the type name, and the other branch still counts |
| `test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out` | A branch that ignores the cancel, blocks the event loop, or fails while cancelled is `timed_out`, and its answer is dropped |
| `test_no_branch_succeeding_raises_unavailable_with_diagnostics` | The typed failure, carrying both diagnostics |
| `test_a_bug_in_a_branch_propagates_and_cancels_the_other` | Five kinds of bug, such as `ProgrammingError` and `FileNotFoundError`, are not hidden, and the other branch is cancelled |
| `test_cancelling_the_caller_cancels_every_branch` | No branch is left running when the caller gives up |
| `test_a_branch_left_out_is_neither_run_nor_reported` | A mapping with one branch runs only that branch |
| `test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist` | The limit cut and both reasons for `truncated` |
| `test_k_reaches_the_fusion` | `k` changes the order, as in M05-T05 |
| `test_bad_input_is_refused_before_any_branch_starts` | 25 bad inputs raise `InvalidHybridQuery`, and no branch was called |
| `test_a_branch_that_is_not_a_retrieval_branch_is_refused` | A plain `"lexical"` key is refused |
| `test_nothing_shows_the_question_or_an_error_message` | A planted marker never shows in a repr, a diagnostic, or the error |

**Action:** create `tests/test_hybrid_retrieval.py`:

```python
"""Checks on running both retrieval branches at once and fusing the result.

The branches here are fakes, so each test decides exactly when a branch
answers, fails, or never answers.
"""

import asyncio
import contextlib
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingUnavailable
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    HybridRetrievalUnavailable,
    InvalidHybridQuery,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import InvalidLexicalQuery
from knowledge_service.retrieval.vector_search import (
    InvalidVectorQuery,
    NoCurrentEmbeddingSpace,
    VectorCandidate,
)

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
REQUEST_ID = UUID(int=99)
QUESTION = "How do I request parental leave?"
MARKER = "zebramarker"
# Long enough for every fake that answers, short enough to keep tests fast.
SHORT = 0.05


def _candidate(number: int, rank: int) -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=number)),
        document_id=DocumentId(UUID(int=10_000 + number)),
        source_id=SourceId(UUID(int=1)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=rank / 100,
        external_id=f"doc-{number}.txt",
        title=f"Title {number}",
        heading_path=None,
        source_anchor=None,
        text=f"Text of Chunk {number}",
    )


def _ranked(*numbers: int) -> tuple[VectorCandidate, ...]:
    return tuple(_candidate(number, rank) for rank, number in enumerate(numbers, 1))


@dataclass(frozen=True, slots=True)
class Found:
    candidates: Sequence[Candidate]
    truncated: bool = False


@dataclass(slots=True)
class FakeBranch:
    """A branch that records what it was asked and answers as told."""

    found: Sequence[Candidate] = ()
    truncated: bool = False
    error: Exception | None = None
    # When set, the branch waits for it before answering.
    wait_for: asyncio.Event | None = None
    # When set, the branch sets it as soon as it starts.
    started: asyncio.Event | None = None
    requests: list[BranchRequest] = field(default_factory=list[BranchRequest])
    cancelled: bool = False

    async def __call__(self, request: BranchRequest) -> BranchResult:
        self.requests.append(request)
        if self.started is not None:
            self.started.set()
        try:
            if self.wait_for is not None:
                await self.wait_for.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return Found(self.found, self.truncated)


def _never() -> asyncio.Event:
    return asyncio.Event()


async def _retrieve(
    branches: Mapping[RetrievalBranch, BranchSearch],
    *,
    question: str = QUESTION,
    limit: int = 10,
    timeout_seconds: float = 5.0,
    k: int = 60,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        question,
        context=CONTEXT,
        branches=branches,
        limit=limit,
        timeout_seconds=timeout_seconds,
        request_id=REQUEST_ID,
        language=Language.ENGLISH,
        k=k,
    )


def _order(result_candidates: Sequence[Candidate]) -> list[int]:
    return [candidate.chunk_id.value.int for candidate in result_candidates]


async def test_both_branches_are_fused_and_reported() -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3))
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [3, 1, 2, 4]
    assert [part.branch for part in result.candidates[0].contributions] == [
        DENSE,
        LEXICAL,
    ]
    assert [(b.branch, b.status, b.candidates) for b in result.branches] == [
        (DENSE, BranchStatus.SUCCEEDED, 3),
        (LEXICAL, BranchStatus.SUCCEEDED, 2),
    ]
    assert all(b.error is None and b.elapsed_seconds >= 0 for b in result.branches)
    assert not result.degraded
    assert not result.truncated
    assert (result.limit, result.k) == (10, 60)


async def test_every_branch_gets_the_same_question_reader_and_deadline() -> None:
    dense, lexical = FakeBranch(), FakeBranch()

    await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=7, timeout_seconds=5.0)

    for branch in (dense, lexical):
        (request,) = branch.requests
        assert request.question == QUESTION
        assert request.context is CONTEXT
        assert request.language is Language.ENGLISH
        assert request.limit == 7
        assert request.request_id == REQUEST_ID
        assert 0 < request.timeout_seconds <= 5.0


async def test_the_branches_run_at_the_same_time() -> None:
    # Each branch waits until the other has started, so run one after the
    # other they would both miss the deadline.
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(
        found=_ranked(1), started=dense_started, wait_for=lexical_started
    )
    lexical = FakeBranch(
        found=_ranked(2), started=lexical_started, wait_for=dense_started
    )

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=1.0)

    assert [b.status for b in result.branches] == [BranchStatus.SUCCEEDED] * 2


@pytest.mark.parametrize(("slow", "fast"), [(DENSE, LEXICAL), (LEXICAL, DENSE)])
async def test_a_branch_that_misses_the_deadline_is_cancelled_and_reported(
    slow: RetrievalBranch, fast: RetrievalBranch
) -> None:
    late = FakeBranch(found=_ranked(1), wait_for=_never())
    on_time = FakeBranch(found=_ranked(2, 3))
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await _retrieve({slow: late, fast: on_time}, timeout_seconds=SHORT)

    assert loop.time() - started < 1.0
    assert late.cancelled
    assert _order(result.candidates) == [2, 3]
    report = {b.branch: b for b in result.branches}
    assert report[slow].status is BranchStatus.TIMED_OUT
    assert (report[slow].candidates, report[slow].error) == (0, None)
    assert report[slow].elapsed_seconds >= SHORT * 0.9
    assert report[fast].status is BranchStatus.SUCCEEDED
    assert report[fast].elapsed_seconds < SHORT
    assert result.degraded


@pytest.mark.parametrize(
    "error",
    [
        EmbeddingUnavailable(),
        NoCurrentEmbeddingSpace("the current embedding space is another model"),
        InvalidLexicalQuery("the query has no words"),
        exc.OperationalError("SELECT 1", {}, Exception("server closed")),
        exc.InterfaceError("SELECT 1", {}, Exception("connection already closed")),
        exc.TimeoutError("QueuePool limit reached"),
        ConnectionResetError(),
        TimeoutError(),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_branch_that_fails_is_reported_by_its_error_type(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(found=_ranked(5))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [5]
    assert result.branches[0].status is BranchStatus.FAILED
    assert result.branches[0].error == type(error).__name__
    assert result.degraded


async def test_no_branch_succeeding_raises_unavailable_with_diagnostics() -> None:
    dense = FakeBranch(error=EmbeddingUnavailable())
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=SHORT)

    assert [(b.status, b.error) for b in caught.value.branches] == [
        (BranchStatus.FAILED, "EmbeddingUnavailable"),
        (BranchStatus.TIMED_OUT, None),
    ]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("a bug"),
        exc.ProgrammingError("SELECT no_such_column", {}, Exception("no column")),
        exc.CompileError("cannot compile"),
        FileNotFoundError("config.toml"),
        InvalidVectorQuery("the query has the wrong length"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_bug_in_a_branch_propagates_and_cancels_the_other(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(ExceptionGroup) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert caught.group_contains(type(error))
    assert lexical.cancelled


async def _answers_despite_the_cancel(request: BranchRequest) -> BranchResult:
    with contextlib.suppress(asyncio.CancelledError):
        await _never().wait()
    return Found(_ranked(7))


async def _blocks_the_event_loop(request: BranchRequest) -> BranchResult:
    # Blocks on purpose, as CPU-bound or synchronous work would.
    time.sleep(SHORT * 3)  # noqa: ASYNC251
    return Found(_ranked(7))


async def _fails_while_cancelled(request: BranchRequest) -> BranchResult:
    try:
        await _never().wait()
    except asyncio.CancelledError:
        raise exc.OperationalError("ROLLBACK", {}, Exception("lost")) from None
    return Found(_ranked(7))


@pytest.mark.parametrize(
    "late",
    [_answers_despite_the_cancel, _blocks_the_event_loop, _fails_while_cancelled],
    ids=lambda search: search.__name__,
)
async def test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out(
    late: BranchSearch,
) -> None:
    # Dense starts first and answers at once, before the late branch runs.
    on_time = FakeBranch(found=_ranked(2))

    result = await _retrieve({DENSE: on_time, LEXICAL: late}, timeout_seconds=SHORT)

    assert _order(result.candidates) == [2]
    assert [(b.status, b.candidates, b.error) for b in result.branches] == [
        (BranchStatus.SUCCEEDED, 1, None),
        (BranchStatus.TIMED_OUT, 0, None),
    ]


async def test_cancelling_the_caller_cancels_every_branch() -> None:
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(started=dense_started, wait_for=_never())
    lexical = FakeBranch(started=lexical_started, wait_for=_never())
    task = asyncio.create_task(_retrieve({DENSE: dense, LEXICAL: lexical}))
    await dense_started.wait()
    await lexical_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert dense.cancelled
    assert lexical.cancelled


async def test_a_branch_left_out_is_neither_run_nor_reported() -> None:
    lexical = FakeBranch(found=_ranked(4, 2))

    result = await _retrieve({LEXICAL: lexical})

    assert _order(result.candidates) == [4, 2]
    assert [b.branch for b in result.branches] == [LEXICAL]
    assert not result.degraded


@pytest.mark.parametrize(
    ("limit", "dense_truncated", "expected", "truncated"),
    [
        (2, False, [3, 1], True),
        (4, False, [3, 1, 2, 4], False),
        (4, True, [3, 1, 2, 4], True),
        (10, False, [3, 1, 2, 4], False),
    ],
)
async def test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist(
    limit: int, dense_truncated: bool, expected: list[int], truncated: bool
) -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3), truncated=dense_truncated)
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=limit)

    assert _order(result.candidates) == expected
    assert [b.truncated for b in result.branches] == [dense_truncated, False]
    assert [candidate.rank for candidate in result.candidates] == list(
        range(1, len(expected) + 1)
    )
    assert result.truncated is truncated


async def test_k_reaches_the_fusion() -> None:
    dense = FakeBranch(found=_ranked(1, 11, 12, 2))
    lexical = FakeBranch(found=_ranked(21, 22, 23, 2))

    by_default = await _retrieve({DENSE: dense, LEXICAL: lexical})
    by_one = await _retrieve({DENSE: dense, LEXICAL: lexical}, k=1)

    assert _order(by_default.candidates)[0] == 2
    assert _order(by_one.candidates)[0] == 1
    assert by_one.k == 1
    assert by_one.candidates[0].contributions[0].contribution == 1 / 2


@pytest.mark.parametrize(
    "change",
    [
        {"question": ""},
        {"question": "   \n"},
        {"question": None},
        {"question": 5},
        {"question": "x" * 1_001},
        {"question": "parental" + chr(0) + "leave"},
        {"question": "leave" + chr(0xD800)},
        {"context": None},
        {"language": "english"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1.0},
        {"timeout_seconds": math.nan},
        {"timeout_seconds": math.inf},
        {"timeout_seconds": True},
        {"timeout_seconds": 10**400},
        {"timeout_seconds": "1"},
        {"k": 0},
        {"k": True},
        {"branches": {}},
        {"branches": "dense"},
        {"branches": {DENSE: None}},
    ],
    ids=repr,
)
async def test_bad_input_is_refused_before_any_branch_starts(
    change: dict[str, object],
) -> None:
    dense, lexical = FakeBranch(), FakeBranch()
    arguments: dict[str, object] = {
        "question": QUESTION,
        "context": CONTEXT,
        "branches": {DENSE: dense, LEXICAL: lexical},
        "limit": 10,
        "timeout_seconds": 5.0,
        "request_id": REQUEST_ID,
        "k": 60,
    }
    arguments.update(change)

    with pytest.raises(InvalidHybridQuery):
        await retrieve_hybrid(**cast(dict[str, Any], arguments))

    assert dense.requests == lexical.requests == []


async def test_a_branch_that_is_not_a_retrieval_branch_is_refused() -> None:
    lexical = FakeBranch()
    branches = cast(Mapping[RetrievalBranch, BranchSearch], {"lexical": lexical})

    with pytest.raises(InvalidHybridQuery):
        await _retrieve(branches)

    assert lexical.requests == []


async def test_nothing_shows_the_question_or_an_error_message() -> None:
    question = f"{MARKER} parental leave"
    dense = FakeBranch(error=EmbeddingUnavailable(MARKER))
    lexical = FakeBranch(found=_ranked(1))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, question=question)

    assert MARKER not in repr(result)
    assert MARKER not in repr(dense.requests[0])

    failing = FakeBranch(error=InvalidLexicalQuery(MARKER))
    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({LEXICAL: failing}, question=question)
    error = caught.value
    shown = [str(error), repr(error.args), repr(error.branches)]
    assert all(MARKER not in text for text in shown)
    assert error.__cause__ is None
    assert error.__context__ is None
```

**Check:**

```bash
uv run pytest tests/test_hybrid_retrieval.py -q
```

Expected: `56 passed`.

**Deliberate break.** Make the branches run one after the other. In `hybrid.py`, replace:

```python
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_run(branch, branches[branch], request, deadline))
            for branch in RetrievalBranch
            if branch in branches
        ]
    ended = [task.result() for task in tasks]
```

with:

```python
    ended = [
        await _run(branch, branches[branch], request, deadline)
        for branch in RetrievalBranch
        if branch in branches
    ]
```

Run the tests again. Expected: 7 failed, 49 passed:

- `test_the_branches_run_at_the_same_time`, because each fake waits for the other to start;
- `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported[dense-lexical]`, because the fast lexical branch now starts only after the slow dense one used up the deadline;
- the five `test_a_bug_in_a_branch_propagates_and_cancels_the_other` cases, because without the task group the bug arrives on its own, not inside an `ExceptionGroup`, and nothing cancels the other branch.

Undo the change and run the tests again: all pass.

### Step 4 — Coding step: test with the real searches and the real database

This step proves that the real branches use the reader's permissions and that the deadline really stops a query on the server.

**In plain words:** These tests store a few Documents, one of them visible to another user only, and run the real hybrid search through the real database. They check that each reader gets only what they may see, from both searches. They also plug in a branch whose query sleeps for 30 seconds and check that, at the deadline, PostgreSQL really stops that query and the connection goes back to the pool. Another test makes the vector search fail the way it does during a model change, and checks that word search still answers. The last one checks that the vector search borrows no database connection while it waits for the embedding.

| Test | What it proves |
|---|---|
| `test_both_real_branches_search_as_the_reader` | A Document for another user never reaches this reader from either branch; that user gets it from both |
| `test_a_slow_query_is_cancelled_on_the_server_at_the_deadline` | At a 1-second deadline the sleeping branch is `timed_out`, `pg_stat_activity` shows no running `pg_sleep`, and no connection is left checked out |
| `test_a_model_the_current_space_does_not_hold_fails_only_dense` | A 4-dimension model against a 3-dimension space fails dense with `NoCurrentEmbeddingSpace`; lexical still answers, and the result is degraded |
| `test_dense_holds_no_connection_while_it_waits_for_the_embedding` | While the embedding call waits, no database connection is checked out, so a slow provider cannot use up the pool |

A few names need a word of explanation. `FakeEmbeddingModel` from `tests/fakes.py` turns text into a fixed vector, so `question_vector()` can store "the Question's own vector" on a Chunk to make it the dense branch's best match. `pg_stat_activity` is PostgreSQL's list of running queries. The comment `/* hybrid-slow-branch */` in `SLOW_QUERY` makes the slow query easy to find there. `checkedout()` counts the pool's connections currently lent out.

**Action:** create `tests/test_hybrid_retrieval_integration.py`:

```python
"""Hybrid retrieval against the real database, with both real branches.

The dense branch embeds the question with a fake model and searches the
stored vectors; the lexical branch searches the stored words. Both run on
their own connections from one runtime.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.pool import QueuePool

from fakes import FakeEmbeddingModel
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import embedding_spaces
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.settings import Settings

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000046")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000f1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000f2")
QUESTION = "parental leave"
# Tags the slow query, so the test can look for it on the server.
SLOW_QUERY = "SELECT pg_sleep(30) /* hybrid-slow-branch */"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=model.identity.provider,
                model=model.identity.model,
                dimensions=model.identity.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    text: str,
    vector: Sequence[float],
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    """Store a Document and give each of its Chunks the same vector."""
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title="Handbook", modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, model.identity)
        assert space_id is not None
        stored = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=model.identity.dimensions,
            vectors={chunk.chunk_id: vector for chunk in stored},
            created_at=OBSERVED_AT,
        )
    return document_id


async def question_vector() -> tuple[float, ...]:
    batch = await FakeEmbeddingModel(dimensions=3).embed(
        [QUESTION], request_id=UUID(int=1), timeout_seconds=1.0
    )
    return tuple(batch.vectors[0])


async def retrieve(
    branches: dict[RetrievalBranch, BranchSearch],
    *,
    reader: UserId = READER,
    timeout_seconds: float = 5.0,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        QUESTION,
        context=AuthorizationContext(user_id=reader),
        branches=branches,
        limit=10,
        timeout_seconds=timeout_seconds,
        request_id=UUID(int=2),
    )


@pytest.mark.integration
async def test_both_real_branches_search_as_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    near = await question_vector()
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy for staff.", vector=near
    )
    secret = await a_document(
        runtime,
        "secret.txt",
        text="Parental leave plans before the announcement.",
        vector=near,
        readers=(Subject.user(OTHER_USER),),
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: lexical_branch(runtime),
    }

    seen = await retrieve(branches)
    other = await retrieve(branches, reader=OTHER_USER)

    assert [b.status for b in seen.branches] == [BranchStatus.SUCCEEDED] * 2
    assert {c.document_id for c in seen.candidates} == {leave}
    assert [p.branch for p in seen.candidates[0].contributions] == [DENSE, LEXICAL]
    assert {c.document_id for c in other.candidates} == {leave, secret}
    found_secret = next(c for c in other.candidates if c.document_id == secret)
    assert [p.branch for p in found_secret.contributions] == [DENSE, LEXICAL]


def sleeping_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """A branch whose query outlasts any deadline in these tests."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            await connection.execute(sa.text(SLOW_QUERY))
        raise AssertionError("the slow query was expected to be cancelled")

    return search


async def running_slow_queries(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        found = await connection.execute(
            sa.text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE state = 'active' AND query = :query"
            ),
            {"query": SLOW_QUERY},
        )
        return int(found.scalar_one())


@pytest.mark.integration
async def test_a_slow_query_is_cancelled_on_the_server_at_the_deadline(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: sleeping_branch(runtime),
    }
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await retrieve(branches, timeout_seconds=1.0)

    assert loop.time() - started < 10
    report = {b.branch: b.status for b in result.branches}
    assert report == {DENSE: BranchStatus.SUCCEEDED, LEXICAL: BranchStatus.TIMED_OUT}
    assert len(result.candidates) == 1
    # The server may take a moment to act on the cancel request.
    for _ in range(50):
        if await running_slow_queries(runtime) == 0:
            break
        await asyncio.sleep(0.1)
    assert await running_slow_queries(runtime) == 0
    assert cast(QueuePool, runtime.engine.pool).checkedout() == 0


@pytest.mark.integration
async def test_a_model_the_current_space_does_not_hold_fails_only_dense(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=4)),
        LEXICAL: lexical_branch(runtime),
    }

    result = await retrieve(branches)

    assert [(b.status, b.error) for b in result.branches] == [
        (BranchStatus.FAILED, "NoCurrentEmbeddingSpace"),
        (BranchStatus.SUCCEEDED, None),
    ]
    assert {c.document_id for c in result.candidates} == {leave}
    assert result.degraded


@pytest.mark.integration
async def test_dense_holds_no_connection_while_it_waits_for_the_embedding(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    answer = asyncio.Event()
    model = FakeEmbeddingModel(dimensions=3, wait_for=answer)
    pool = cast(QueuePool, runtime.engine.pool)
    task = asyncio.create_task(retrieve({DENSE: dense_branch(runtime, model)}))
    # Wait until the dense branch is inside the embedding call.
    for _ in range(500):
        if model.batches:
            break
        await asyncio.sleep(0.01)
    assert model.batches

    held_while_embedding = pool.checkedout()
    answer.set()
    result = await task

    assert held_while_embedding == 0
    assert {c.document_id for c in result.candidates} == {leave}
    assert pool.checkedout() == 0
```

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_hybrid_retrieval_integration.py -q -m integration
```

Expected: `4 passed`.

### Step 5 — No coding in this step: watch the policy work

This step shows the four outcomes of the policy side by side, so you can compare them with your predictions.

**Action:** from the project root, run:

```bash
uv run python - <<'PY'
import asyncio
import sys
from uuid import UUID

sys.path.insert(0, "tests")
from test_hybrid_retrieval import CONTEXT, FakeBranch, _ranked

from knowledge_service.embeddings.port import EmbeddingUnavailable
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    HybridRetrievalUnavailable,
    retrieve_hybrid,
)

DENSE, LEXICAL = RetrievalBranch.DENSE, RetrievalBranch.LEXICAL


async def show(title, dense, lexical):
    print(title)
    try:
        result = await retrieve_hybrid(
            "parental leave",
            context=CONTEXT,
            branches={DENSE: dense, LEXICAL: lexical},
            limit=3,
            timeout_seconds=0.2,
            request_id=UUID(int=1),
        )
    except HybridRetrievalUnavailable as error:
        branches, found = error.branches, f"raised {type(error).__name__}"
    else:
        found = [c.chunk_id.value.int for c in result.candidates]
        found = f"Chunks {found}, truncated={result.truncated}, degraded={result.degraded}"
        branches = result.branches
    for b in branches:
        print(f"  {b.branch:<8} {b.status:<10} found={b.candidates} error={b.error}")
    print(f"  -> {found}")


async def main():
    await show("both answer", FakeBranch(found=_ranked(1, 2, 3)), FakeBranch(found=_ranked(3, 4)))
    await show("lexical is too slow", FakeBranch(found=_ranked(1, 2)), FakeBranch(wait_for=asyncio.Event()))
    await show("embedding is down", FakeBranch(error=EmbeddingUnavailable()), FakeBranch(found=_ranked(4)))
    await show("nothing works", FakeBranch(error=EmbeddingUnavailable()), FakeBranch(wait_for=asyncio.Event()))


asyncio.run(main())
PY
```

The reference run printed:

```text
both answer
  dense    succeeded  found=3 error=None
  lexical  succeeded  found=2 error=None
  -> Chunks [3, 1, 2], truncated=True, degraded=False
lexical is too slow
  dense    succeeded  found=2 error=None
  lexical  timed_out  found=0 error=None
  -> Chunks [1, 2], truncated=False, degraded=True
embedding is down
  dense    failed     found=0 error=EmbeddingUnavailable
  lexical  succeeded  found=1 error=None
  -> Chunks [4], truncated=False, degraded=True
nothing works
  dense    failed     found=0 error=EmbeddingUnavailable
  lexical  timed_out  found=0 error=None
  -> raised HybridRetrievalUnavailable
```

How to read it:

- **Both answer:** five Chunks in total, cut to the limit of 3, so `truncated=True`. Chunk 3 is first because both branches found it.
- **Lexical is too slow:** after 0.2 seconds lexical is `timed_out`, and the dense Chunks still come back, marked `degraded`.
- **Embedding is down:** dense `failed` with its error type only, and the lexical Chunk comes back, marked `degraded`.
- **Nothing works:** no branch succeeded, so the caller gets `HybridRetrievalUnavailable` with both reports.

Write in the evidence which of your Step 1 predictions were right. For the last prediction, the slow-query test in Step 4 is the answer.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 6 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition` |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_hybrid_retrieval.py -q
make format-check
make lint
make typecheck
make test-integration
make check
make docs-check
```

Expected: all pass. The reference run gave `make test-integration` 288 passed and `make check` 1385 passed, 3 deselected. Run the database suites with the database URL set, or their database tests are skipped instead of run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
```

**Action 3:** complete the evidence file:

- **Design:** the module and its types, why branches are plain functions, the deadline, the partial-failure policy and why bugs propagate, the limit and `truncated`, and what the review changed.
- **Verification:** the commands and counts, a table of the tests with what each proves, the Step 5 output, the deliberate break from Step 3, and the known limits from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T06 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T07, and commit with `m05-t06: build the hybrid retrieval module`.

## Reflection questions

1. What does `retrieve_hybrid` hide from the code that calls it? List what a caller never has to think about, such as connections, the deadline, or a search that fails.
2. Someone later speeds things up by giving each branch its own full `timeout_seconds` instead of one shared deadline. Why could that break the time limit the caller asked for, and which branch would it affect most?
3. Someone else "simplifies" `_run` by catching every `Exception` as `failed`. What would get harder to notice in production, and which test catches it?
4. A degraded result has fewer Chunks than a full one. Should the answering step later answer from it, warn the reader, or abstain? What would you measure to decide?
5. `docs/operations.md` asks for retrieval under 750 ms for 95% of Questions. What could prove this design too slow for that goal? Think about what you would time on real Questions, and which branch you expect to be slower.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `hybrid.py` defines `retrieve_hybrid`, `dense_branch`, `lexical_branch`, the diagnostics types, and the two typed errors.
- [ ] Both branches start at the same time, each on its own connection, under one deadline.
- [ ] A branch that misses the deadline is cancelled, its query stops on the server, and it is reported as `timed_out`.
- [ ] A branch that fails with a known error is reported by type and left out; a bug propagates; no branch succeeding raises `HybridRetrievalUnavailable`.
- [ ] Bad input raises `InvalidHybridQuery` before any branch starts.
- [ ] The reader's context reaches both branches, and a hidden Document never comes back.
- [ ] No Question text or error message appears in diagnostics, errors, or reprs.
- [ ] You ran the deliberate break and saw it fail, then undid it.
- [ ] You ran the Step 5 command and compared it with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
