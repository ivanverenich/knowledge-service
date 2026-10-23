# M04-T05 — Embedding publication evidence

Completed: 2026-10-23

## Invariant

A Document's Chunks have vectors in an Embedding Space for all of them or
for none of them. Each vector was computed from the text its Chunk holds
now. No database connection is held while the model works. The text of a
Document that was not available when read is never sent to the provider.

## Expected failing check

Written before the module exists.

**Before the module exists,** the new test file fails to import:
`knowledge_service.embedding_publication` does not exist, so pytest reports
a collection error and runs none of its tests. The rest of the suite still
passes, because nothing else imports the module.

**Predictions,** once the module exists:

- **A Document published twice:** the first call returns `PUBLISHED` with
  one vector per Chunk. The second finds no Chunk without a vector and
  returns `UNCHANGED` without calling the model.
- **An access-only change:** reconciling keeps the Chunks, so their vectors
  stay. Publishing returns `UNCHANGED`, and the model is not called.
- **A text change:** reconciling replaces the Chunks with new IDs, and the
  cascade removes the old vectors. Publishing sends every new Chunk and
  returns `PUBLISHED`.
- **A text change committed while the model works:** the reconcile does not
  wait, because no lock is held during the call. The publish step finds
  other Chunks than it read and returns `STALE`; nothing is written. The
  next run publishes the new Chunks.
- **A tombstone committed while the model works:** the publish step finds
  the Document unavailable and returns `STALE`; nothing is written.
- **Two publishers of one Document at once:** both read the same state and
  call the model. The first to lock the row writes and returns `PUBLISHED`.
  The second then sees Chunks already embedded and returns `STALE`, so
  exactly one vector per Chunk is stored.
- **The second of three batches failing** (for example
  `EmbeddingUnavailable`): batches one and three succeed, but
  `IncompleteEmbedding` is raised with `failed=1`, and no vector is stored,
  not even the first batch's.
- **A rate limit on the second batch:** the batcher stops starting batches,
  so batch three is deferred. `IncompleteEmbedding` has `failed=1` and
  `deferred=1`; nothing is stored.
- **A model with no Embedding Space:** `UnknownEmbeddingSpace` is raised in
  the first transaction, before any text is sent.
- **A tombstoned Document:** the first read returns `UNAVAILABLE`, and the
  model is never called.
- **A space replaced while the model works** (deleted and created again for
  the same model): the publish step finds a different space ID and returns
  `STALE` instead of failing on the foreign key.

## Design

Changed files:

- `src/knowledge_service/identifiers.py`: `EmbeddingSpaceId`.
- `src/knowledge_service/persistence.py`: `load_embedding_space_id`
  (share-locks the space's row), `load_embedded_chunk_ids`, and
  `insert_chunk_embeddings` (every vector in one `INSERT`, converted with
  `list(...)` for pgvector; nothing when there are no vectors).
- New `src/knowledge_service/embedding_publication.py`:
  `publish_document_embeddings`, `EmbeddingPublication`,
  `EmbeddingPublicationStatus`, `UnknownEmbeddingSpace`, and
  `IncompleteEmbedding`.
- New `tests/test_embedding_publication_integration.py`: 16 tests, counting
  parametrized cases.
- `docs/architecture.md`: an "Embedding publication as built in M04" flow.

How it works:

| Part | Behaviour |
|---|---|
| Read | One short transaction share-locks the space, locks the Document's row, and takes a snapshot: content version, every Chunk as `(chunk_id, text)`, the Chunks already embedded, and the pending ones. A missing space raises `UnknownEmbeddingSpace`; a missing or tombstoned Document returns `UNAVAILABLE`; nothing pending returns `UNCHANGED`. The model is not called in any of these cases. |
| Embed | `embed_in_batches` with no connection held. Any batch that did not succeed raises `IncompleteEmbedding` with counts and error class names only. |
| Publish | A second short transaction takes the same locks and a new snapshot. A different space ID or snapshot returns `STALE` and writes nothing; otherwise every vector is inserted and the result is `PUBLISHED`. |

Decisions:

- **The snapshot is the compare-and-swap key.** It holds the Chunk text, not
  only the IDs, because `publish_document` can put new text under the same
  IDs and the same content version. `pending` is excluded from comparison,
  because it follows from the other fields. `chunks` and `pending` both hold
  Chunk text, so both are excluded from `repr`, and no snapshot ever logs it.
- **Both reads lock the Document's row,** the same lock every writer of
  Chunks takes, so each read sees one state of the Document, and a
  tombstone committed during the first read waits instead of letting its
  text be sent.
- **The space's row is share-locked and checked again,** so a space deleted
  or recreated during the model call gives `STALE`, not a foreign-key error.
- **All or nothing per Document.** A failed batch discards the vectors of
  the batches that succeeded. A stale result is a status, because it is
  expected under concurrent edits; a failed batch, a missing space, and a
  Chunk the model cannot take (`EmbeddingInputRejected`) are errors.
- **Whether a space is current does not matter here,** so M04-T10 can fill a
  space that is not yet current the same way.

**Adversarial review and fixes.** Before the guide was written, an
independent review attacked the prototype on the real database. It
confirmed three breaks, each fixed with a regression test that fails
without its fix:

- comparing Chunk IDs without text published old vectors on new text written
  by `publish_document` under the same IDs;
- the first read took no lock, so a tombstone committing between reading the
  Document and its Chunks let a tombstoned Document's text reach the model;
- a space replaced during the model call made the insert fail with a raw
  `IntegrityError`.

It also confirmed that cancellation during the model call or while waiting
for the lock leaves no connection or lock behind, that `publish_document`
waits for the publish step's lock, that no Chunk text appears in errors,
tracebacks, or logs, and that a 5000-Chunk Document publishes in one
statement.

**The closing standards review found one more leak.** `_Snapshot.chunks`
holds every Chunk's text but kept its default `repr`, so printing a snapshot
showed the text, although the code comment claimed otherwise. It now has
`repr=False` and stays part of the comparison.
`test_a_snapshot_never_shows_chunk_text` records the snapshots a real call
takes and failed before the fix.

Limits for later lessons:

- **A gap after every edit.** Between a reconcile and the next publication,
  the new Chunks have no vectors, so dense search does not find the
  Document. M04-T06 should know that "no vectors" can mean "not published
  yet".
- **No resume.** A failed batch or a stale result throws away vectors
  already paid for. Under constant edits a large Document may never finish.
- **One Chunk the model cannot take blocks its Document on every run,** and
  `EmbeddingInputRejected.index` counts pending Chunks, not ordinals.
- **Memory grows with the Document:** all its texts and vectors are held at
  once.
- **Nothing calls this yet,** and nothing in the application creates an
  Embedding Space; the tests insert one. `synchronize_source` does not embed.

## Verification

Commands, run in order with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set and
`UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embedding_publication_integration.py -q` | 16 passed |
| `make format-check lint typecheck` | formatted; all checks passed; 0 errors |
| `uv run pytest -m "not integration and not live" -q` | 660 passed, 88 deselected |
| `make test-integration` | 85 passed, 663 deselected |
| `make check` | 745 passed, 3 deselected; coverage 94%, `embedding_publication.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

The first integration run failed all 15 tests then in the file with
`AttributeError: dimentions`: a misspelled column in
`load_embedding_space_id`, which Pyright cannot see because `table.c.<name>`
accepts any name. It was fixed, with an error-message typo and formatting,
at the learner's request.

Deliberate breaks, each undone afterwards:

| Break | Failed |
|---|---|
| Snapshot `chunks` built from `(chunk_id, "")` | `test_new_text_under_the_same_chunk_ids_publishes_nothing` |
| `_snapshot` with `for_update=False` | `test_a_tombstone_waits_until_the_snapshot_is_read`, `test_two_publishers_of_one_document_write_one_set_of_vectors` |
| No `current_space != space_id` check | `test_a_space_replaced_during_embedding_publishes_nothing` |
| The whole publish check replaced with `False` | six concurrency tests, from the content change to the replaced space |
| No `raise` on unfinished batches; keep the vectors that exist | both `test_a_failed_batch_publishes_no_vector` cases, `test_errors_and_logs_carry_no_chunk_text` |

Every prediction above held, as each case has its own test.

Authorization and telemetry: Chunk text leaves storage only for a Document
that is available while its row is locked
(`test_a_tombstoned_document_is_never_sent`,
`test_a_tombstone_waits_until_the_snapshot_is_read`). The module adds no
logging, and a planted marker appears in no error, `repr`, log record, or
printed snapshot (`test_errors_and_logs_carry_no_chunk_text`,
`test_a_snapshot_never_shows_chunk_text`). Who may read which Chunk is
not decided here; search (M04-T06) must still filter on Access Grants and
availability.

## Reflection

1. **Why the model call sits between two transactions.** A model call can
   take seconds per batch. Inside one transaction it would hold a pooled
   connection and the Document's row lock the whole time, so a reconcile of
   the same Document would wait for the model, or fail on the statement
   timeout. Between two short transactions, the reconcile goes ahead, and
   the publish step simply notices the change.
2. **Why the content version alone is not enough.** Several changes leave it
   the same. A restore after a tombstone re-chunks with new IDs and the same
   version; a second publisher adds vectors without touching the Document;
   and `publish_document` can rewrite the text under the same Chunk IDs and
   version, which `test_new_text_under_the_same_chunk_ids_publishes_nothing`
   covers. Only comparing the Chunks with their text, and the embedded set,
   catches all of them.
3. **Why the first read locks the row too.** Without the lock, its three
   reads (Document, Chunks, embedded Chunks) could see three different
   states. A tombstone committing in between would leave text read from a
   Document already tombstoned, and that text would be sent to the provider
   before the second read noticed. The second check stops the wrong vectors
   being stored, but it cannot unsend text.
4. **Why two of three vectors are thrown away.** Storing them would leave a
   Document whose search covers some sections and not others, with nothing
   marking it as incomplete; search would quietly miss the rest. All or
   nothing keeps the state simple: a Document is fully searchable or not
   yet. The cost is paying again for the batches that succeeded, on every
   retry.
5. **Why `STALE` is a status and a failed batch an error.** A stale result
   is normal under concurrent edits: nothing is wrong, and the next run
   embeds the new Chunks, so a scheduler just moves on. A failed batch means
   the provider or the configuration has a problem; the caller has to
   notice, retry later with backoff, or alert someone, so it must not be
   silently ignored.
6. **A test whose removal would let a real bug through.**
   `test_new_text_under_the_same_chunk_ids_publishes_nothing`. It is the
   only test that fails when the text leaves the comparison key. Without
   it, a later "optimization" that compares IDs only would pass every other
   test, and vectors of old text would be published on new text: search
   would rank passages by what they used to say.

The predictions and reflection in this evidence were drafted by Claude at
the learner's request and have not yet been rewritten in the learner's own
words.
