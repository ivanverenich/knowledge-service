# M03-T12 — Transactional reconciliation evidence

Completed: 2026-10-21

## Invariant

After `reconcile_document` returns, the stored Document row, its Chunks, and
its Access Grants describe one version: every Chunk carries the row's content
version and content fingerprint, and the grants match the row's authorization
fingerprint. A reader sees either the previous complete version or the new
one, never a mixture. Nothing is written when nothing changed, an access-only
change keeps the same Chunks, and an observation older than the stored one is
refused without changing anything, even when two runs overlap.

## Prediction

Written before the reconciliation workflow and its tests exist. This is what
I expect `reconcile_document` to return, and what is stored, for each case.

- **A new document.** `Change.CREATED`. One Document row with content version
  1 and authorization version 1, its Chunks with heading paths and anchors
  such as `guides/handbook.md#offset=...`, and one Access Grant per reader.
- **The same document again.** `Change.UNCHANGED`. Nothing is written: the
  row's `updated_at`, every Chunk ID, and every grant ID stay exactly the
  same.
- **Changed text with the same readers.** `Change.CONTENT`. The content
  version rises to 2, the Chunks are replaced with new ones at version 2, and
  the grants keep their IDs because the authorization version stays at 1.
- **The same text with one more reader.** `Change.ACCESS`. The authorization
  version rises to 2 and the grants are replaced, but the Chunks keep the same
  IDs: nothing is re-chunked.
- **A new source version only.** `Change.SOURCE_VERSION`. Only the row's
  source version changes; the Chunks and grants stay the same.
- **A failure while writing the grants, after the new Chunks were written.**
  The error reaches the caller, and the transaction rolls back. The stored
  row, Chunks, and grants are exactly the previous version.
- **A reader looking while a reconcile is in progress.** It sees the previous
  version's Chunks, because the new ones are not committed yet. After the
  commit, it sees only the new ones.
- **An older observation after a newer one.** `InvalidDocument`, because
  `observed_at` is earlier than the stored `updated_at`. Nothing changes.
- **An older and a newer observation running at the same time.** The second
  run waits at the row lock until the first commits, then decides against the
  committed row. If the newer one commits first, the older one is refused
  with `InvalidDocument`; the newer version stays published either way.
- **Two runs creating the same new document at once.** Only one inserts the
  row. The other finds the conflict, locks the stored row, and decides against
  it, so exactly one valid row remains, with `created_at` no later than
  `updated_at`.

## Design

Changed files:

- `src/knowledge_service/persistence.py`: new `insert_document`, which stores
  a new Document with `ON CONFLICT DO NOTHING` and reports whether it did, and
  a `for_update` option on `load_document`, which locks the row with
  `SELECT ... FOR UPDATE`. Existing callers are unchanged.
- New `src/knowledge_service/reconciliation.py`: `reconcile_document`,
  `build_chunks`, `Reconciliation`, and `Change`.
- New `tests/test_reconciliation.py` (1 unit test) and
  `tests/test_reconciliation_integration.py` (14 integration tests).

How `reconcile_document` works:

1. Outside the transaction: the content fingerprint of the parsed document,
   and the authorization fingerprint of the readers (a provisional Document
   ID is enough, because only the readers count).
2. In one transaction, lock the stored row.
3. No row: `Document.register` and `insert_document`. If the insert wins,
   write Chunks and grants and return `CREATED`; if another run created the
   row first, lock that row and continue as below.
4. A stored row: `Document.reconcile` returns the same object when nothing
   changed (`UNCHANGED`, nothing written), raises `InvalidDocument` for an
   older observation, or returns a Document with only the changed versions
   raised.
5. Write the row, Chunks only when the content version rose or the Document
   was tombstoned, and grants only when the authorization version rose.

| `Change` | When | What is written |
|---|---|---|
| `CREATED` | a new Document | row, Chunks, grants |
| `UNCHANGED` | nothing changed | nothing |
| `CONTENT` | the text changed | row, Chunks |
| `ACCESS` | the readers changed | row, grants (same Chunk IDs) |
| `CONTENT_AND_ACCESS` | both changed | row, Chunks, grants |
| `RESTORED` | a tombstoned Document came back unchanged | row, Chunks |
| `SOURCE_VERSION` | only the source's version label changed | row |

Decisions:

- **One transaction for everything,** as ADR 0012 requires: a reader sees the
  previous complete version or the new one, and a failure anywhere rolls back
  every write.
- **Lock before deciding.** `Document.register` and `Document.reconcile` are
  only correct if the stored row cannot change underneath them, so the row is
  read with `FOR UPDATE`; a concurrent reconcile of the same document waits,
  then decides from what the first one committed.
- **Create without overwriting.** A row lock cannot protect a row that does
  not exist yet, so a new Document is stored with `ON CONFLICT DO NOTHING`; a
  run that loses the race locks the winner's row and decides again.
- **No re-chunking for access-only changes.** Chunks keep their IDs; once
  embeddings exist, this saves re-embedding the whole document just to change
  who may read it.
- **Readers are normalized subjects.** Mapping a Source's principals to users
  and groups is identity work for M07; an empty sequence means nobody may
  read the document.

**Adversarial review and fixes.** Two problems were found before the guide
was written:

- **A lost race on first creation.** Two runs that both found no stored row
  each made a new ID; the database kept the first row, and the second wrote
  Chunks under its own ID, which failed with a raw foreign-key violation.
- **Deciding from an unlocked row.** An independent review ran overlapping
  reconciles against real PostgreSQL and broke the unlocked version three
  ways: a published mix of two versions (the row from one run, the Chunks
  from the other), an older observation overwriting a newer one, which ADR
  0012 forbids, and a create race that left `updated_at` earlier than
  `created_at`, making the document unreadable for good.

Locking the row and inserting with `ON CONFLICT DO NOTHING` closed all four.
The tests were checked against broken versions: writing the Chunks in a
separate transaction fails exactly the two atomicity tests; the version before
the review fixes fails all three concurrency tests; removing only the row lock
fails the two lock-dependent tests. The reviewer's own stress test, twelve
concurrent reconciles of one document, passed five runs out of five against
the final version.

**Notes:**

- **The order key is `observed_at`, not the source version.** Two observations
  with the same `observed_at` are both accepted, and a replay of an older
  source version observed later is published. Later jobs should pass a
  trustworthy observation time.
- **Chunking runs inside the transaction.** It is local and fast today;
  embedding calls, when they come, must happen before the transaction.
- **The "document vanished" error is defensive.** Documents are never deleted,
  so it cannot happen today; it is the one line of `reconciliation.py` the
  tests do not reach.
- **The concurrency tests rely on a 0.2-second pause** for the second run to
  reach the lock. They passed repeatedly, but a very slow machine could make
  them pass without testing the overlap.
- **There is no sync loop yet.** Nothing yet pages through a Source and calls
  `normalize` and `reconcile_document` for every record; M03-T13 needs the
  set of external IDs seen in a full pass to tombstone missing documents.

The integration tests ran against the local disposable PostgreSQL 17 test
server. Its password was read from the container's settings for each run and
never written to any file. The fixtures hold only synthetic text and
identifiers, and the public types name no provider.

## Verification

Final run on 2026-10-21 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` and
`KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set to the local test server:

| Command | Result |
|---|---|
| `uv run pytest tests/test_reconciliation.py tests/test_reconciliation_integration.py -q` | 15 passed |
| `make format-check lint typecheck` | 102 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 527 passed, 34 deselected |
| `make test-integration` | 32 passed, 529 deselected |
| `make check` (with the database) | 559 passed, 2 deselected; total coverage 92%; `reconciliation.py` 98% |
| `make check` (without the database) | 528 passed, 31 skipped, 2 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held. Along the way:

- The first format check failed on `reconciliation.py` (a trailing comma, a
  line with only spaces, and no final newline); `make format` fixed it
  without changing behaviour.
- `_change` was written with keyword-only parameters
  (`_change(*, content=..., access=..., restored=...)`), which makes each call
  read clearly.

## Reflection

1. **Why one transaction.** The row, Chunks, and grants together are one
   version. If the Chunks were committed first and the grant write then
   failed, search would already return the new Chunks while the row still
   described the old version and the grants still named the old readers: new
   text could be shown to people who should no longer read it, and the next
   reconcile would compare against a row that does not match what is
   searchable. In one transaction, the failure rolls back everything, and
   readers keep seeing the old, consistent version.
2. **Why access-only changes keep the Chunks.** The text did not change, so
   new Chunks would be identical. Re-chunking would still mean new Chunk IDs
   and, once embeddings exist, an embedding call for every chunk of the
   document: slow, paid, and delaying the change that matters, which for a
   removed reader is urgent.
3. **How an older observation could overwrite a newer one.** Without a lock,
   both workers read the same stored row before either writes. The older one
   checks its time against that stale row, passes, and then waits for the
   newer one's write to finish, and overwrites it. With `SELECT ... FOR
   UPDATE`, the second worker cannot even read the row until the first one
   commits; it then sees the newer version and `Document.reconcile` refuses
   the older observation.
4. **A test whose removal would let a real bug through.**
   `test_concurrent_content_and_access_changes_never_publish_a_mix`. Without
   it, removing the row lock would pass every sequential test, and in
   production two overlapping runs could publish a row from one version with
   Chunks from another; the next run would then compare against the wrong
   row and never repair it.
5. **What complexity the workflow hides from its caller.** The caller passes
   a normalized document, its readers, and a time. It never computes
   fingerprints, decides which versions to raise, chunks, builds anchors,
   creates IDs, locks rows, handles a lost race, or chooses which tables to
   write.
6. **Which alternative would make the next change harder.** Writing the row,
   Chunks, and grants in separate transactions, or always rewriting all
   three, would look simpler, but M04 will add embeddings to every chunk
   write: separate transactions would let searchable vectors disagree with
   their row, and always rewriting would re-embed every document on every
   run.
7. **What would escape without the negative tests.** Without
   `test_an_older_observation_is_refused_and_changes_nothing`, a change that
   skipped `Document.reconcile`'s time check would let a delayed, redelivered
   job publish an outdated version over a newer one, silently undoing an edit.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
