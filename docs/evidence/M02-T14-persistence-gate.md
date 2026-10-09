# M02-T14 — Persistence gate evidence

Completed: 2026-10-09

## Invariant

Every stored row keeps the opaque identifier it was created with, and no
identifier is ever reused, so a Chunk or a Grant can name the Document it
belongs to across any number of content changes. A Document's content version
and authorization version move independently: a change to the text raises the
content version and rewrites the Chunks, and a change to who may read it raises
the authorization version and reuses every Chunk. One workflow owns one
transaction, so a reader sees a Document at one version rather than a mixture of
two, and a failure inside that transaction publishes nothing at all.

## Prediction

I expect the new invariant — a Source has at most one Synchronization Run in
flight — to refuse a second run that is still running for the same Source, and
to allow two Sources to run at the same time. I expect it to allow a Source to
start a new run once its previous run has finished, because the rule is about
running at the same time and not about how many runs a Source has ever had. I
expect the refusal to arrive as an integrity error from the database rather than
from application code, and I expect the record of a finished run to stay in the
table.

## Version separation

`upsert_document` writes the row with `ON CONFLICT ... DO UPDATE` on
`uq_documents_source_external`, and the values it updates do not include
`document_id` or `created_at`, so the identity and the creation time come from
the stored row and only the versions move. Both versions travel with that row:
`content_version` with `content_fingerprint`, and `authorization_version` with
`authorization_fingerprint`.

A Chunk carries the `content_version` it was written from, and `replace_chunks`
reads the highest version already stored before it writes. If the stored version
is higher, it raises `StaleChunkWrite` and writes nothing, which is what stops a
redelivered older publication from overwriting a newer one.

So an authorization-only change raises `authorization_version` and leaves the
content version alone: the same text is never split again, and a publication is
allowed to carry the Chunk run it already had. A content change raises
`content_version`, and `replace_chunks` deletes the stored run and inserts the
new one inside the caller's transaction. The separation is enforced on the way
in as well: `Publication.__post_init__` refuses any Chunk whose document id,
content version, or content fingerprint disagrees with the Document, and any
Grant that names another Document. A publication of mixed versions cannot be
constructed, so it cannot reach the database.

Observable outcome: after a publication that changes only the grants, the
Document's authorization version is higher and its Chunks still carry the
content version they carried before.

## Transaction ownership

`publish_documents` is the only place that opens a transaction, through
`DatabaseRuntime.transaction()`, and `publish_document` takes the connection it
is given. If `replace_chunks` committed on its own, publishing one Document
would become three separate commits: the row would be visible while its Chunks
still described the previous version, and a reader in that window would cite
Chunks from one content version beside a Document row from another. Nothing
would raise, so the fault would appear as an occasionally wrong citation. The
same boundary is what makes a batch all-or-nothing: a batch whose second
Document is refused by `StaleChunkWrite` publishes nothing, including the first
Document whose row had already been rewritten.

The model-provider call belongs outside the transaction because it is a network
call whose latency is not bounded by this system. Holding the transaction open
across it would hold the row locks `upsert_document` took and would keep a
connection from the pool for as long as the provider takes, so a slow provider
would turn into lock waits and pool exhaustion for unrelated work. Embedding and
generation results come back to a second, short transaction that does the
writes.

## Injected migration failure

The migration creating `uq_synchronization_runs_running_source` omitted the
partial predicate, so it created a plain unique index on `source_id`:

```
CREATE UNIQUE INDEX uq_synchronization_runs_running_source
    ON public.synchronization_runs USING btree (source_id)
```

1. The database enforced "at most one Synchronization Run per Source, ever".
   The tests expect "at most one run that is still running per Source", which
   allows a Source to run again once its previous run has finished.
2. The drift check reads the model, and it compares structure: tables, columns,
   indexes, and constraints. It reported no differences, because it does not
   compare a partial index's predicate. The test reads behaviour, so it is the
   one that noticed: two rows that the model allows were refused.
3. The migration was wrong. `persistence.py` declares the same index with
   `postgresql_where=sa.text("status = 'running'")`, and the test asserts the
   rule the model states, so the model and the test agreed and only the
   migration disagreed with both.
4. The smallest change is to add the predicate to the migration's
   `create_index`, and to its `drop_index`, so the index the migration builds is
   the index the model declares.

The two plan tests failed as well, with the same `UniqueViolation`. That was the
same defect rather than a second one: their seed writes two thousand
Synchronization Runs across nine Sources, so an index that allows one run per
Source for all time stopped a table from holding a history at all.

Editing the file was not enough. The migration had already run, and a revision
that is already applied does not run again, so the database kept the old index
while the file looked correct. Re-applying it — `alembic downgrade -1` then
`alembic upgrade head` — replaced the index, and the database then enforced:

```
CREATE UNIQUE INDEX uq_synchronization_runs_running_source
    ON public.synchronization_runs USING btree (source_id)
    WHERE ((status)::text = 'running'::text)
```

## Verification

- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 18 passed against PostgreSQL 17.11 on a database migrated from empty. The
  three tests that failed with the defect are among them: a Source running again
  after a finished run, and the two plan tests whose seed builds a run history.
- `KNOWLEDGE_SERVICE_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic check`
  — "No new upgrade operations detected", before and after the repair. It stayed
  silent about the defect, which is the point of the entry above.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests -q`
  — 310 passed, 2 deselected, total coverage 89%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests -q` with no
  database URL — 293 passed, 17 skipped, 2 deselected. The deterministic suite
  and the hooks were green while the defect was in place, because the only thing
  that could show it was a real PostgreSQL.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 293 passed, 17 skipped, 2 deselected.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed, including
  the new data model section.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed.
- `git diff --check` — passed.

Files changed: `src/knowledge_service/persistence.py` declares the index,
`migrations/versions/09aa0992e559_one_running_synchronization_run_per_.py`
creates it, `tests/test_sources_integration.py` covers the rule in three tests,
`tests/test_indexes.py` pins the justified index set, and `docs/architecture.md`
carries the data model. No connection URL, credential, or private content
entered any artifact.

## Reflection

1. A check constraint sees the row it is written on. This rule is about a set of
   rows for one Source: no two of them may both be running. That is why it is a
   partial unique index — the predicate narrows the set the uniqueness applies
   to, and no constraint could express it.

2. A structural check cannot see the meaning of a predicate, and it cannot see
   behaviour: it compares the shape of the schema, not what the schema does. Any
   defect whose whole effect lives in a predicate, a default, a type's range, or
   the interaction between two constraints is invisible to it. A migration
   therefore needs both: a structural check to keep it in step with the model,
   and a behavioural test that asserts each rule the model states. The drift
   check would have caught a missing index or a wrong column; it could not catch
   an index that indexes the wrong rows.

3. If I had changed only the file, `alembic upgrade head` would have reported
   that the database was already at the revision and done nothing. The test would
   have failed with the same `UniqueViolation` against a database whose index no
   longer matched any file in the repository — a failure with no visible cause,
   and the kind that costs an afternoon. Repairing a migration means repairing
   the databases it already produced; the check that proves it is reading
   `pg_indexes`, not reading the file.
