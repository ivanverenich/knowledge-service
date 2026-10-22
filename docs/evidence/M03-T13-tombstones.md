# M03-T13 — Tombstone evidence

Completed: 2026-10-22

## Invariant

Once `tombstone_missing` commits, no read through `load_searchable_chunks`
returns a Chunk of a Document the complete pass did not see, even though its
Chunks are still stored. The Document keeps its row, ID, and Access Grants,
and an audit event and a queued purge Job exist for it. The purge deletes
Chunks only of a Document that is still tombstoned. A failed purge deletes
nothing, and the Chunks are eventually deleted by a retry or a new Job. A pass
that fails part-way tombstones nothing.

## Prediction

Written before the deletion workflows and their tests exist. This is what I
expect to be stored, and what reads return, for each case.

- **A file deleted from the folder, before the purge.** The next complete pass
  returns one `Tombstone` for it. Its Document row keeps the same ID, its
  availability is `tombstoned`, and `updated_at` is the pass's observation
  time. `load_searchable_chunks` no longer returns its text, but
  `load_chunks` still finds its Chunks. One audit event,
  `document.availability_changed` with `availability=tombstoned`, and one
  queued `purge_document_chunks` Job targeting the Document exist.
- **The same file, after the purge.** The Job is `succeeded`, the Document
  has no Chunks, and the row and its Access Grants are unchanged.
- **A document the pass saw.** Stays available; nothing is written for it.
- **A document of another Source.** Untouched, and still returned by reads
  for that Source.
- **Tombstoning twice.** The second call returns no tombstones and queues no
  second Job, because the Document is no longer available.
- **A document updated after the pass began.** Kept available, with no audit
  event and no Job: a later observation saw it, so this pass's view is stale.
- **A purge whose delete fails.** The Job is stored as `failed` with attempt 1
  and the error code `chunk_purge_failed`; the Chunks are still stored but
  still not readable. After `Job.retry`, running the purge again succeeds and
  deletes them.
- **A purge that fails three times.** `Job.retry` raises `JobNotRetryable`.
  The next `requeue_unfinished_purges` queues one new purge Job, which
  deletes the Chunks; after that it queues nothing.
- **A purge of a document that came back before the purge ran.** Reconcile
  returns `Change.RESTORED` with the same Document ID and records a second
  audit event, `availability=available`. The purge succeeds but deletes
  nothing, so the new Chunks stay searchable.
- **A purge Job run twice.** The second run raises `InvalidJob`, because only
  a queued Job can run.
- **A pass whose second page fails.** `SourceUnavailable` reaches the caller
  before anything is tombstoned; every document stays readable.
- **A file that stops parsing.** It is quarantined but counted as seen, so it
  is not tombstoned and its last good version stays searchable.
- **A Source that keeps returning the same checkpoint.** `InvalidSource`
  instead of paging forever.

## Design

Changed files:

- New migration `migrations/versions/c1d2e3f4a5b6_purge_document_chunks_job_kind.py`:
  adds `purge_document_chunks` to the `ck_jobs_kind` check constraint; the
  downgrade deletes purge Jobs first, because the old constraint refuses them.
- `src/knowledge_service/jobs.py`: `JobKind.PURGE_DOCUMENT_CHUNKS`.
- `src/knowledge_service/persistence.py`: the matching `ck_jobs_kind`, and
  `load_document_by_id`, `load_available_documents`,
  `load_unpurged_documents`, `searchable_chunks_statement`,
  `load_searchable_chunks`, `delete_chunks`, and a `for_update` option on
  `load_job`. Existing callers are unchanged.
- New `src/knowledge_service/deletions.py`: `tombstone_missing`,
  `requeue_unfinished_purges`, `purge_document_chunks`,
  `availability_event`, `Tombstone`, and `PURGE_FAILED`.
- `src/knowledge_service/reconciliation.py`: a restore records an
  `availability=available` audit event.
- New `src/knowledge_service/synchronization.py`: `synchronize_source`,
  `source_readers`, and `SynchronizationSummary`.
- New `tests/test_deletions.py` (2 unit tests),
  `tests/test_deletions_integration.py` (9), and
  `tests/test_synchronization_integration.py` (7).

How deletion works:

| Step | Transaction | What is written |
|---|---|---|
| `tombstone_missing` after a complete pass | one for the whole pass | for each missing Document: the row as `tombstoned`, an audit event, a queued purge Job |
| `purge_document_chunks` | one; on failure a second | the Chunks deleted if the Document is still tombstoned, and the Job `succeeded`; or only the Job `failed` with `chunk_purge_failed` |
| `requeue_unfinished_purges` at the end of every pass | one | a new purge Job for each tombstoned Document that still has Chunks and no purge that can still clean them |

Decisions:

- **Unavailable first, deleted later.** A tombstone is one small row update,
  so content leaves reads at once. The heavy delete runs as a Job that can
  fail and retry without ever making the text readable again; once Chunks
  have embeddings, deleting them gets slower still.
- **One read for retrieval.** `load_searchable_chunks` joins Chunks to their
  Document and keeps only available ones. Retrieval in M04 must read through
  it, or through the same filter.
- **Identity is kept.** The row, its ID, and its Access Grants stay, so a
  file that comes back is restored with the same ID, and the audit trail
  records both changes of availability.
- **Lock only what changes.** `tombstone_missing` reads without a lock, then
  locks each missing row and checks it again: a pass where nothing is missing
  locks nothing. A row updated after `observed_at` was seen by a later
  observation and is kept.
- **The purge re-checks under a lock.** It locks the Job, then the Document,
  and deletes only if the Document is still tombstoned, so a restore and a
  purge cannot interleave.
- **The error code is a fixed string.** A Job stores no raw error text, so
  nothing private can reach the queue. `InvalidJob` (a missing Job, another
  kind, a Job that is not queued) is a caller error and is not recorded as a
  failed attempt.
- **Tombstone only after a complete pass.** An exception on any page leaves
  `synchronize_source` before `tombstone_missing`. Quarantined records count
  as seen; a record reported deleted is removed from `seen`; a repeated
  checkpoint raises `InvalidSource`.
- **Readers deny by default.** Public items get `Subject.public()`; mapping
  principals to users and groups is M07, so other items get no grants.

**Adversarial review and fixes.** An independent review ran 13 attack tests
against real PostgreSQL, including overlapping restores and purges (15
rounds), two purges of one Job at once, two tombstone passes over 20
documents at once, a failure while recording the failure, cancellation
mid-purge, and a migration downgrade and upgrade. Reads, locking, and
cancellation held: a cancelled purge leaves the Job queued and the Chunks
stored. It found three real problems, all fixed with a test that fails
without the fix:

- **An endless loop.** A Source that kept returning the same checkpoint made
  `synchronize_source` page forever, reconciling on every page.
- **Chunks stored forever.** A purge that used all three attempts was never
  queued again, because later passes only look at available documents.
  `requeue_unfinished_purges` closes this.
- **An unsorted import** that failed Ruff.

It also led to four smaller fixes: lock only the missing rows, keep a
document updated after the pass began instead of failing the whole batch,
let a deletion reported later in the same pass win, and audit restores.

When the learner's code was finished, the four deliberate breaks from the
guide were run against it, one at a time: removing the availability filter
failed 6 tests, purging regardless of availability failed 1, tombstoning
after every page failed 4, and not counting quarantined records as seen
failed 1. Each file was restored afterwards.

**Known limits:**

- **An empty listing tombstones everything.** If the folder exists but is
  empty, for example an unmounted network share, the pass is complete with
  nothing seen. The documents come back with the same IDs on the next good
  pass, but a guard is worth adding before a real remote Source.
- **No single-run guard.** `synchronize_source` creates no Synchronization
  Run, so two overlapping passes over different views of a folder could flip
  documents between tombstoned and available. M08 adds runs.
- **Duplicate purge Jobs are possible** if two passes call
  `requeue_unfinished_purges` at the same moment; the second purge deletes
  nothing.
- **External IDs that differ only in Unicode form** are two documents; macOS
  file names can do this.
- **The cause of a purge failure is not logged;** only the fixed code is
  stored.
- **One line is not reached by the tests:** the re-raise in
  `purge_document_chunks` when another worker finished the Job between its two
  transactions.

The integration tests ran against the local disposable PostgreSQL 17 test
server. Its password was read from the container's settings for each run and
never written to any file. The fixtures hold only synthetic text and
identifiers, and the public types name no provider.

## Verification

Final run on 2026-10-22 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` and
`KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set to the local test server:

| Command | Result |
|---|---|
| `uv run pytest tests/test_deletions.py tests/test_deletions_integration.py tests/test_synchronization_integration.py -q` | 18 passed |
| `make format-check lint typecheck` | 108 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 529 passed, 50 deselected |
| `make test-integration` | 48 passed, 531 deselected |
| `make check` (with the database) | 577 passed, 2 deselected; total coverage 93%; `deletions.py` 98% |
| `make check` (without the database) | 530 passed, 47 skipped, 2 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held, including the error code `chunk_purge_failed`. Along
the way:

- `deletions.py` first stored the error code as `chuck_purge_failed` and named
  the transaction's connection `connnection`; both typos were corrected. The
  tests compare against the `PURGE_FAILED` constant, so they would not have
  caught the first one.
- The first lint run failed on extra blank lines after the imports of the
  migration and `deletions.py`; `ruff check --fix` and `make format` fixed
  them without changing behaviour.

## Reflection

1. **Why tombstone first and delete later.** If the Chunks were deleted in
   the same transaction, a large deletion would hold locks for a long time,
   and a failure would roll back the tombstone too, so the deleted text would
   stay searchable. Tombstoning is one small row update that hides the text
   at once; the delete can then fail and retry as often as needed, and readers
   never see the text again in the meantime.
2. **Why tombstone only after the last page.** Until the last page is read,
   "not seen" also means "not reached yet". If `tombstone_missing` ran after
   page 1, every document on page 2 would be tombstoned and then restored
   when page 2 arrived, disappearing from answers for a while and leaving
   audit events and purge Jobs for documents that were never deleted. If page
   2 then failed, they would stay hidden.
3. **Why a file that fails to parse counts as seen.** A parse failure says
   the reader is broken, not that the file is gone. If a parser bug made every
   PDF look deleted, one bad release would hide every PDF and queue a purge
   for each, deleting their Chunks. Keeping the last good version means a
   bug costs freshness, not content.
4. **A purge that runs after a restore.** The purge locks its Job, then the
   Document row. Because the restore wrote the row as available, the purge
   sees `available`, deletes nothing, and marks the Job succeeded. Without the
   lock, the purge could read `tombstoned` just before the restore commits
   its new Chunks and then delete them, leaving an available document with
   nothing to search.
5. **What `synchronize_source` hides from its caller.** The caller passes a
   Source, its ID, and a time. It never pages or tracks checkpoints, guards
   against a repeated checkpoint, normalizes, handles quarantines, maps
   access to readers, reconciles, collects the seen IDs, tombstones, writes
   audit events, or queues and re-queues purge Jobs.
6. **Which alternative would make later work harder.** Deleting Chunks
   inside the tombstone transaction would tie the speed of a deletion to the
   vector index once M04 adds embeddings, and a failed delete would keep
   deleted text searchable. Filtering by availability in each future search
   query instead of one shared read would let one forgotten filter return
   deleted content.
7. **A test whose removal would let a real bug through.**
   `test_a_failed_pass_tombstones_nothing`. Without it, a change that
   tombstoned after each page would pass the single-page tests, and in
   production one network error on page 2 would hide every document on the
   later pages.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
