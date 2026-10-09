# M02-T13 — Essential relational indexes evidence

Completed: 2026-10-09

## Invariant

An index changes how fast a query reads, never what it returns. Each index here
serves a named access pattern the application runs: the retention sweep, the
audit trail of one target, the recent runs of one Source, and the Documents one
verified subject may reach. Every foreign key is the leading column of some
index, so deleting a parent row does not scan the child table to find the rows
it owns. No index is added for a column no query filters on, because every index
costs writes and storage on the path that publishes a Document.

## Access patterns

These four need an index, and each has a statement behind it:

| Access pattern | Where it runs | Index |
|---|---|---|
| Live Conversations whose retention deadline has passed, oldest first | `load_conversations_due_for_deletion` | `ix_conversations_retention_deadline`, partial on live rows |
| The trail recorded against one target, oldest first | `load_audit_events` | `ix_audit_events_target_occurred_at` |
| The recent Synchronization Runs of one Source | no reader yet | `ix_synchronization_runs_source_started_at` |
| The Documents one verified subject may reach | no reader yet | `ix_access_grants_subject_document` |

These filter on a column a constraint already leads with, so they need nothing
new. I checked this against the declared tables rather than by reading them:

| Foreign key | Already served by |
|---|---|
| `documents.source_id` | `uq_documents_source_external` |
| `messages.conversation_id` | `uq_messages_conversation_ordinal` |
| `access_grants.document_id` | `uq_access_grants_subject_document` |
| `chunks.document_id` | `uq_chunks_document_ordinal` |
| `message_feedback.message_id` | the primary key on `(message_id, user_id)` |
| `synchronization_runs.source_id` | nothing — this one needs an index |

Five of the six foreign keys are covered; `synchronization_runs.source_id` is
the only one that is not, which is why its index is on the list.

## Prediction

I expect every one of the four queries to read through its index once the tables
hold a few thousand rows, and I expect an index to go unused on a nearly empty
table, because the planner is right to scan the whole thing when it is small.
The retention index should be chosen as a plain index scan in deadline order,
the audit trail as an index-only scan because every column it needs is in the
index, the runs of one source as a backward index scan for the newest first, and
the grants of one subject as a bitmap scan. If the grants of one subject are a
large share of the table, I expect a sequential scan instead, and I expect that
to mean my sample data, not the index, is wrong. I also expect `alembic check`
to name any index that reaches the declared tables but not a migration, and the
reverse.

## Verification

Plans were read from a disposable database upgraded to head and seeded through
the same statements the plan test uses: 2000 rows each in `conversations`,
`audit_events`, `synchronization_runs`, `documents`, and `access_grants`, over
200 subjects and 9 sources, then `ANALYZE`. PostgreSQL 17.11.

- Conversations due for deletion, oldest first — `Index Scan using
  ix_conversations_retention_deadline`, index condition on
  `retention_deadline <= now()`, 10 rows in 0.009 ms. The partial predicate holds
  the index to live rows only.
- The trail recorded against one target — `Index Scan using
  ix_audit_events_target_occurred_at`, 10 rows in 0.009 ms. It is a plain index
  scan rather than the index-only scan I predicted, because the query selects
  every column of the table and only `target_id`, `occurred_at`, and `event_id`
  are in the index.
- The recent Synchronization Runs of one Source — `Index Scan Backward using
  ix_synchronization_runs_source_started_at`, 5 rows in 0.008 ms. Scanned
  backward, so `order_by(started_at.desc())` needs no sort.
- The Documents one verified subject may reach — `Index Only Scan using
  ix_access_grants_subject_document`, 10 rows in 0.017 ms. It reads only the
  indexed column, so it never touches the heap row.
- Already served, not re-indexed: `SELECT ... FROM documents WHERE source_id = ...
  AND external_id = ...` reads through `uq_documents_source_external`, and
  `SELECT ... FROM chunks WHERE document_id = ... ORDER BY ordinal` reads
  through `uq_chunks_document_ordinal` with a bitmap index scan. Adding a second
  index on those columns would cost writes and buy nothing.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_indexes_integration.py -vv`
  — 2 passed, repeated five times with the same result, because a plan that is
  chosen once and not the next time would mean the sample data is wrong.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 15 passed, and the full suite 307 passed with total coverage 89%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indexes.py -vv`
  — 4 passed without a database.
- `KNOWLEDGE_SERVICE_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic upgrade head`
  then `uv run alembic check` — "No new upgrade operations detected". Removing
  one index from the declared tables makes it name that index as a new
  operation, so an index that reaches a database but not the code, or the code
  but not a migration, is caught.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests -q` with no
  database URL — 293 passed, 14 skipped, 2 deselected. The plan tests skip
  cleanly with the rest of the integration suite.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 293 passed, 14 skipped, 2 deselected.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/persistence.py` (four indexes and
four named statements, with `load_conversations_due_for_deletion` and
`load_audit_events` calling theirs), `migrations/versions/89549119c381_access_pattern_indexes.py`,
`tests/test_indexes.py`, and `tests/test_indexes_integration.py`. The plan test
builds no SQL of its own: it explains the statements the application runs, and
its seed passes every value as a bound parameter. No connection URL, credential,
or private content enters any artifact.

## Reflection

1. The four statement builders give the log query and the plan test one
   definition. Inlining the SQL in the test would let the test keep passing
   after someone changed the loader's filter, and the index would then be
   measured against a query nothing runs. Two of the builders have no caller
   outside the test today, which is the same reason they are named: the access
   pattern exists before its reader does, and the index is chosen from the
   pattern, not from the column.

2. `chunks.document_id` is the first column of `uq_chunks_document_ordinal`, so
   PostgreSQL's index on that constraint already answers a lookup by document
   and a foreign key check needs nothing more.
   `synchronization_runs.source_id` leads no constraint at all: the table's
   primary key is `run_id`, so nothing indexed `source_id` until this lesson.
   The rule is about the constraint's *first* column, not about the column
   appearing somewhere in a constraint — `uq_documents_source_external` covers
   `source_id` because it comes first, and would not have if the order were
   reversed.

3. A plan test on an empty table reports success for a query that scans
   everything and, in production, would scan every row of a table nobody
   notices until it is large. The seed is what makes the plan meaningful: the
   index is chosen because the data justifies it. The ACL seed shows the other
   half of the same trap — with only nine subjects, each query matched a tenth
   of the table and a sequential scan was the *correct* plan, so the test would
   have failed while the index was fine. Both failures point at the data, which
   is why the assertion names the index rather than asserting a plan shape.
