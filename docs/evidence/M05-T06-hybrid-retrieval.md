# M05-T06 — Hybrid retrieval evidence

Completed: 2026-11-02

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

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- Chunks 3, 1, 2, and the result is truncated. Chunk 3 is found by both
  branches, so it scores 1/63 + 1/61 and comes first. Chunk 1 is first in
  dense only (1/61). Chunks 2 and 4 are both second in one list (1/62), an
  exact tie, so the lower Chunk ID, 2, wins the last place. Four
  different Chunks are cut to 3, so `truncated=True`.
- After 0.2 seconds the lexical branch is cancelled. The caller still gets
  the dense Chunks, and the result is degraded. The lexical diagnostic
  says `timed_out`, with no Candidates and no error type.
- Only Chunk 4 comes back, and the result is degraded. The dense
  diagnostic says `failed` and names the error type, not its message.
- No branch succeeded, so the caller gets `HybridRetrievalUnavailable`,
  which carries both diagnostics: dense `failed`, lexical `timed_out`.
- Cancelling the branch cancels the query on the server too: PostgreSQL
  stops it instead of letting it run for 30 seconds, and the connection
  goes back to the pool.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions held, and none was wrong. That is expected, not a
good guess: they were drafted after the prototype had run. The Step 5
command printed:

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

| Prediction | What happened |
|---|---|
| Chunks 3, 1, 2, truncated | Yes: `Chunks [3, 1, 2], truncated=True`; Chunk 4 tied with Chunk 2 and lost on Chunk ID |
| Lexical too slow: the dense Chunks, degraded, lexical `timed_out` | Yes: `Chunks [1, 2]`, `degraded=True`, lexical `timed_out` with `error=None` |
| Embedding down: Chunk 4 only, degraded, dense `failed` by type | Yes: `Chunks [4]`, `degraded=True`, dense `failed` with `EmbeddingUnavailable` |
| Nothing works: `HybridRetrievalUnavailable` with both reports | Yes: raised, dense `failed`, lexical `timed_out` |
| The cancelled `pg_sleep` stops on the server | Yes: `test_a_slow_query_is_cancelled_on_the_server_at_the_deadline` returns in under 10 seconds with lexical `timed_out`, finds no `hybrid-slow-branch` query still running, and the pool has no connection checked out |

## Design

Changed files:

- `src/knowledge_service/retrieval/hybrid.py` (new):
  - `HybridRetrievalError`, `InvalidHybridQuery`, and
    `HybridRetrievalUnavailable`, which carries every branch's diagnostics
    under the fixed message "no retrieval branch succeeded"
  - `BranchStatus` (`succeeded`, `timed_out`, `failed`)
  - `BranchRequest`: the Question (out of repr), its language, the
    reader's `AuthorizationContext`, the limit, a request ID, and the
    seconds left until the deadline
  - `BranchResult`, a protocol with `candidates` and `truncated`, and
    `BranchSearch`, an async function from `BranchRequest` to
    `BranchResult`
  - `BranchDiagnostics`: branch, status, elapsed seconds, number of
    Candidates, the branch's own `truncated`, and an error type name
  - `HybridRetrieval`: limit, `k`, the fused Candidates, `truncated`, the
    diagnostics, and a `degraded` property
  - `retrieve_hybrid(question, *, context, branches, limit,
    timeout_seconds, request_id, language=None, k=DEFAULT_RRF_K)`
  - `dense_branch(runtime, model)` and `lexical_branch(runtime)`, the two
    real branch searches
- `tests/test_hybrid_retrieval.py` (new): 56 unit tests with fake
  branches, no database.
- `tests/test_hybrid_retrieval_integration.py` (new): 4 integration tests
  with the real searches and database.
- `docs/architecture.md`: the `retrieval` row names `retrieve_hybrid`.

How it works:

1. It checks the input first and raises `InvalidHybridQuery` before any
   branch starts: a blank Question, one over 1,000 characters (the most
   word search accepts), a NUL or a broken character, a wrong reader or
   language, a bad limit, timeout, or `k`, and an empty or wrong set of
   branches.
2. It computes one deadline, now plus `timeout_seconds`, and builds one
   `BranchRequest` for every branch.
3. It starts each branch in an `asyncio.TaskGroup`, in enum order. Each
   runs under `asyncio.timeout_at(deadline)` and is told the seconds it
   has left.
4. A branch that answers in time is `succeeded`. One that raises a known
   dependency or refusal error is `failed`, with the error's type name.
   Anything that ends after the deadline is `timed_out`, and its answer
   is dropped. Any other error propagates, and the task group cancels the
   other branch.
5. If no branch succeeded, it raises `HybridRetrievalUnavailable`.
   Otherwise it fuses the branches that succeeded with
   `fuse_ranked_candidates`, cuts the list to `limit`, and sets
   `truncated` if the cut removed anything or a succeeded branch said
   more exist.

Why branches are plain functions: `retrieve_hybrid` only needs "ask a
branch, get Candidates". Taking async functions keyed by
`RetrievalBranch` lets the unit tests pass fakes that are slow, broken,
or quick on purpose, without a database or mocks. This is the task's
first hint: shrink the interface before reaching for mocks. The real
`dense_branch` embeds the Question outside any transaction, then opens
its own connection for `search_similar_chunks`; `lexical_branch` opens
its own for `search_lexical_chunks`. One connection runs one query at a
time, so each branch needs its own to search at the same moment.

Why one deadline: the caller asked for one time limit. With one shared
deadline, the whole call ends by it however the branches behave, and a
branch started a little later gets a little less time. When the deadline
passes, the branch's task is cancelled, and psycopg sends PostgreSQL a
cancel request, so the query stops on the server too.

Why bugs propagate: a `RuntimeError`, or SQL that no longer compiles
after a migration (`ProgrammingError`), is a mistake in our code, not an
outage. Reporting it as `failed` would let the service run on one branch
for ever without anyone noticing. Only a short list counts as `failed`:
embedding errors, `NoCurrentEmbeddingSpace`, `InvalidLexicalQuery`,
SQLAlchemy's `OperationalError`, `InterfaceError`, and pool
`TimeoutError`, `ConnectionError`, and a branch's own `TimeoutError`.

Why `HybridRetrievalUnavailable` and not an empty list: an empty list
means "searched, nothing matched". When no branch worked, the honest
answer is a typed failure, and it still carries the diagnostics.

Permissions: the same `AuthorizationContext` goes to every branch, and
each branch applies it in its own SQL statement, before ranking and
`LIMIT`. Fusion only reorders what the branches returned, so no path can
add a Chunk the reader may not see. A degraded result has fewer Chunks,
all of them permission-checked.

The adversarial review of the prototype found five problems, each fixed
with a test that fails on the unfixed code: every SQLAlchemy error and
every `OSError` counted as `failed`, which hid bugs (now only the short
list does); a branch that ignored the cancel or blocked the event loop
was reported as `succeeded` and fused (anything after the deadline is now
`timed_out`, its answer dropped); an error raised while a branch was
being cancelled showed as `failed` (now `timed_out`); Questions up to
4,000 characters passed the check although word search refuses more than
1,000 (the limit is now 1,000, and NUL or broken characters are refused);
and a timeout too large for a float, a language given as a string, a
missing reader, or a branch that is not a function slipped past the check
(all now `InvalidHybridQuery`). It also showed three tests that could not
fail: a constant `elapsed_seconds`, a per-branch `truncated` that was
always False, and a dense branch that held a connection while embedding;
each now has a test that catches it.

## Verification

Commands, run one after another, with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL`
set for the database suites:

| Command | Result |
|---|---|
| `uv run pytest tests/test_hybrid_retrieval.py -q` | 56 passed |
| `uv run pytest tests/test_hybrid_retrieval_integration.py -q -m integration` | 4 passed |
| `make format-check`, `make lint`, `make typecheck` | 177 files formatted; all checks passed; 0 errors |
| `make test-integration` | 288 passed, 1100 deselected |
| `make check` | 1385 passed, 3 deselected; coverage 95%, `hybrid.py` fully covered (111 statements, 26 branches) |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

No migration and no database change.

What the tests prove, at `retrieve_hybrid`:

| Spec item | Tests |
|---|---|
| Authorization reaches both branches | `test_every_branch_gets_the_same_question_reader_and_deadline` (fakes record the same `AuthorizationContext`); `test_both_real_branches_search_as_the_reader` (real SQL: each reader gets only the Chunks they may see, from both branches) |
| Concurrency | `test_the_branches_run_at_the_same_time`: each fake waits for the other to start, which only works if both run at once |
| Timeout policy is observable | `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported` (either branch: cancelled, `timed_out`, the other kept, `degraded`); `test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out`; `test_a_slow_query_is_cancelled_on_the_server_at_the_deadline` (a real 30-second `pg_sleep` stops on the server, the pool has nothing checked out) |
| Partial failure | `test_a_branch_that_fails_is_reported_by_its_error_type` (eight failure types); `test_a_model_the_current_space_does_not_hold_fails_only_dense` (real database) |
| Typed failure | `test_no_branch_succeeding_raises_unavailable_with_diagnostics`; `test_bad_input_is_refused_before_any_branch_starts` (25 bad inputs, no branch called); `test_a_branch_that_is_not_a_retrieval_branch_is_refused` |
| Bugs are not hidden | `test_a_bug_in_a_branch_propagates_and_cancels_the_other` (five kinds of bug); `test_cancelling_the_caller_cancels_every_branch` |
| Fusion, limit, `truncated` | `test_both_branches_are_fused_and_reported`, `test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist`, `test_k_reaches_the_fusion`, `test_a_branch_left_out_is_neither_run_nor_reported` |
| Resources | `test_dense_holds_no_connection_while_it_waits_for_the_embedding` |
| No sensitive text | `test_nothing_shows_the_question_or_an_error_message`: a planted marker never appears in a repr, a diagnostic, or the error |

The Step 5 output is in "Prediction check" above.

Deliberate break, undone afterwards: with the branches awaited one after
the other instead of in an `asyncio.TaskGroup`, 7 tests failed and 49
passed: `test_the_branches_run_at_the_same_time`,
`test_a_branch_that_misses_the_deadline_is_cancelled_and_reported[dense-lexical]`,
and the five `test_a_bug_in_a_branch_propagates_and_cancels_the_other`
cases (`RuntimeError`, `ProgrammingError`, `CompileError`,
`FileNotFoundError`, `InvalidVectorQuery`). After restoring, the file
matched its earlier content byte for byte and 56 passed. In the
prototype, other mutations each failed at least one test: catching every
`Exception`, keeping late answers, one timeout per branch instead of one
deadline, no limit cut, and the error message in diagnostics.

The highest-risk edge is a reader getting a Chunk they may not see.
`test_both_real_branches_search_as_the_reader` covers it through both
real branches: the private Document comes back from dense and lexical for
its owner and from neither for another reader, so removing the
permission filter from either branch fails it.

Security and privacy: every Chunk returned passed the reader's
permission check in its branch's own SQL statement, before ranking and
`LIMIT`; fusion and the limit only reorder and drop. Diagnostics carry an
error's type name only, never its message, because a database error can
repeat the Question. `HybridRetrievalUnavailable` has a fixed message and
no chained cause, and `BranchRequest` keeps the Question out of its repr.
Nothing is logged.

Reviews: the Standards review (format, lint, types, ADR 0012 transaction
ownership, ADR 0013 module layout, markers, no leaked text) and the Spec
review (every spec item traced to code and a test; it ran the unit and
integration suites once, 60 passed) found nothing blocking. Their
non-blocking notes: the Question checks repeat those in
`lexical_search.py` on purpose, so a bad Question is refused before any
branch starts; one unit test asserts that the fast branch took under
0.05 seconds, which could be flaky on a loaded machine; the shared
`BranchRequest` is built with the full timeout, which each branch's copy
then replaces; and the server-side cancel is proven with a `pg_sleep`
stand-in branch that uses the same `runtime.transaction()` path as the
real ones.

Known limits:

- asyncio cannot stop code that blocks the event loop or ignores a
  cancel. Such a branch still delays the whole call, although its answer
  is dropped.
- The pool's `TimeoutError` and a branch's own `TimeoutError` show the
  same name in the diagnostics.
- A branch that returns more than `limit` Candidates is not trimmed before
  fusion; the final list is still cut to `limit`.
- `KeyboardInterrupt` and `SystemExit` escape on their own, as they do
  everywhere in asyncio.
- The API accepts Questions up to 4,000 characters, but retrieval refuses
  more than 1,000. The task that connects retrieval to the API must
  settle this.
- Each branch reads in its own transaction, so the two can see different
  snapshots. If a Document is renamed or reindexed during a request, the
  branches can describe the same Chunk differently, and fusion raises
  `InvalidFusionInput` instead of returning a result.
- `truncated` speaks only for branches that succeeded; a branch that
  timed out or failed may have had more, which `degraded` signals.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `retrieve_hybrid` hides.** A caller passes a Question, a
   reader, the two branches, a limit, and a timeout. It never thinks
   about opening a connection per branch, running both at once, one
   shared deadline, cancelling a slow branch and its database query,
   telling an outage from a bug, merging two ranked lists, cutting to the
   limit, or keeping private text out of reports. It gets one ranked list
   plus a report on each branch, or one typed error.
2. **A full timeout per branch.** Each branch would get the whole
   `timeout_seconds` from the moment it starts, not from when the call
   started. A branch that starts late, such as dense after a slow
   embedding call, could run past the caller's limit, so the call could
   take longer than asked. The dense branch is affected most, because it
   spends part of its time on the network before its query starts.
3. **Catching every `Exception` as `failed`.** A real bug, such as SQL
   broken by a migration, would look like an outage: the branch would be
   `failed` on every request, and the service would quietly run on the
   other branch with worse results and no crash to notice.
   `test_a_bug_in_a_branch_propagates_and_cancels_the_other` catches it.
4. **Answering from a degraded result.** It depends on which branch is
   missing and on the Question. I would answer from it but record that it
   was degraded, and measure on the M05-T07 set how often a single
   branch still finds the Chunk the answer needs, separately for English
   and Ukrainian. If one branch alone misses it often, the answering step
   should warn or abstain.
5. **The 750 ms goal.** Time each branch on real Questions, and look at
   the 95th percentile of the whole call, not the average. I expect the
   dense branch to be slower, because it waits for the embedding service
   before its query. If that call alone often takes most of 750 ms, the
   design is too slow and needs a cache or a faster model.
