# M04-T04 — pgvector schema evidence

Completed: 2026-10-23

## Invariant

No stored vector can have a length other than its space's dimensions,
whatever the application does. A Chunk has at most one vector per space, and
vectors of several spaces can coexist. At most one space is current. A vector
lives exactly as long as its Chunk and its space.

## Expected failing check

Written before the schema exists.

**Migrating the current `postgres:17` test container.** The new migration's
first statement, `CREATE EXTENSION IF NOT EXISTS vector`, fails because the
image does not ship pgvector: PostgreSQL reports that extension "vector" is
not available and must first be installed. The migration runs in a
transaction, so nothing of it is applied. Because the test fixtures run every
migration at the start of a run, every integration test fails, not only the
new ones.

**Tables declared in `persistence.py` with no migration.** The deterministic
tests still pass, including the pinning tests in
`tests/test_embedding_schema.py`, which read only the declarations. With the
database:

- the drift test in `tests/test_migrations_integration.py` fails, because
  `alembic check` finds new upgrade operations: two tables the migrations
  never create;
- every new schema test fails with `relation "embedding_spaces" does not
  exist`.

**Predictions for the database,** once the migration exists:

- **A 4-number vector in a 3-dimension space,** with the row claiming 3
  dimensions: refused with an `IntegrityError` naming
  `ck_chunk_embeddings_dimensions`, because `vector_dims(embedding)` is 4.
- **A row that claims 4 dimensions for that space:** refused with an
  `IntegrityError` naming `fk_chunk_embeddings_space_dimensions`, because no
  space has the pair (that space, 4).
- **A vector containing NaN or infinity:** refused by pgvector itself with a
  `DataError`, before any constraint runs.
- **A second current space:** refused with an `IntegrityError` naming
  `uq_embedding_spaces_current`.
- **Switching the current space:** works as two updates in one transaction,
  first clearing the old space, then setting the new one. Both spaces keep
  their vectors for the same Chunk. Setting the new one first fails, because
  the unique index is checked row by row.
- **A new version of a Chunk's text:** `reconcile_document` replaces the
  Chunks with new IDs, and the old Chunk's vectors are removed by the cascade
  from `chunks`, leaving no vector attached to text it was not made from.
- **Deleting a space:** removes that space's vectors only; another space's
  vectors for the same Chunk stay.
- **Changing a space's dimensions while it holds vectors:** refused with an
  `IntegrityError` naming `fk_chunk_embeddings_space_dimensions`, because the
  existing vector rows would no longer match any space.

## Design

Changed files:

- `pyproject.toml` and `uv.lock`: `pgvector>=0.5.1,<0.6`, locked at 0.5.1.
- `src/knowledge_service/persistence.py`: the `embedding_spaces` and
  `chunk_embeddings` tables.
- New migration `migrations/versions/e3f4a5b6c7d8_chunk_embeddings.py`, after
  `d2e3f4a5b6c7`.
- `tests/test_indexes.py`: the two new indexes, and a foreign-key check over
  whole constraints.
- New `tests/test_embedding_schema.py` (4 tests) and
  `tests/test_embedding_schema_integration.py` (18 tests, counting
  parametrized cases).
- `CONTEXT.md` (**Embedding Space**), `docs/architecture.md` (identifier,
  diagram, rules), `docs/operations.md` (the extension privilege).
- Outside the lesson: `make test-integration` now builds
  `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` from the running
  `knowledge-service-postgres` container when it is unset, through new
  `scripts/test_database_url.py`, and fails instead of skipping every
  integration test when there is no database. The script refuses to write
  the URL, which holds the password, to a terminal, and its errors name only
  the exception class.

How it works:

| Rule | Enforced by |
|---|---|
| The extension exists | `CREATE EXTENSION IF NOT EXISTS vector`; `downgrade` keeps it. |
| One space per model identity | `uq_embedding_spaces_identity` on `(provider, model, dimensions)`, plus `ck_embedding_spaces_identity` so padded or empty names cannot make a second space. |
| Dimensions are possible | `ck_embedding_spaces_dimensions`: 1 to 16000, pgvector's limit. |
| At most one current space | `uq_embedding_spaces_current`, unique on `is_current` `WHERE is_current`. |
| One vector per Chunk per space | Primary key `(chunk_id, space_id)`. |
| A vector has its space's length | The row repeats `dimensions`; `fk_chunk_embeddings_space_dimensions` proves the pair is the space's own, and `ck_chunk_embeddings_dimensions` compares it with `vector_dims(embedding)`. |
| A vector lives as long as its Chunk and space | `ON DELETE CASCADE` from `chunks` and from the composite key; changing a space's dimensions under existing vectors is refused by the same key. |
| Several spaces coexist | The `embedding` column is a plain `vector`, with no fixed length. |

The drift check does not compare `CHECK` bodies, index conditions, or a
vector's length, so two tests pin those rules on both sides:
`tests/test_embedding_schema.py` reads the declarations, and
`test_the_migrated_database_holds_exactly_these_rules` reads PostgreSQL's own
rendering.

The index policy now walks foreign-key constraints, not columns: a composite
key needs an index that starts with its first column, so `dimensions`, the
second column of the composite key, is no longer flagged on its own.

Notes for later lessons:

- **pgvector's insert needs a list.** Its SQLAlchemy type refuses a tuple,
  and the port's `Vector` is a tuple: M04-T05 must convert with `list(...)`.
- **Similarity indexes need one partial index per space,** cast to that
  space's length, such as `(embedding::vector(1536)) WHERE space_id = ...`;
  pgvector's HNSW index also allows at most 2000 dimensions.
- **Nothing stops relabelling a vector** to another space of the same
  length, or editing a Chunk's text in place; the application never does
  either.
- **A current space may have no vectors yet,** and deleting the current
  space leaves none current: M04-T10 must backfill before switching.
- **Tombstoned Documents keep their vectors until the purge,** so search in
  M04-T06 must filter on availability.

## Verification

Test server: `knowledge-service-postgres` runs `pgvector/pgvector:pg17` with
extension `vector` 0.8.7, migrated to `e3f4a5b6c7d8`. The old `postgres:17`
container is kept, stopped, as `knowledge-service-postgres-pg17`.

Commands, run in order with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set and
`UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embedding_schema.py tests/test_embedding_schema_integration.py tests/test_indexes.py -q` | 26 passed |
| `make format-check lint typecheck` | formatted; all checks passed; 0 errors |
| `uv run pytest -m "not integration and not live" -q` | 660 passed, 72 deselected |
| `make test-integration` | 69 passed, 663 deselected |
| `make check` | 729 passed, 3 deselected; coverage 94% |
| `make docs-check` | passed |
| `git diff --check` | clean |

Authorization and telemetry: not applicable here. This task adds only
schema, with no read path that returns vectors or text and no new logging,
so nothing leaves storage. Search over these vectors (M04-T06) must still
filter on Grants and availability.

`tests/test_migrations_integration.py` passes, including the upgrade,
downgrade, and upgrade-again round trip and the drift check.

Deliberate breaks, each run on a freshly dropped test database and then
undone:

| Break | Failed |
|---|---|
| Remove `ck_chunk_embeddings_dimensions` | `test_a_vector_of_another_length_is_refused`, `test_the_migrated_database_holds_exactly_these_rules` |
| Foreign key on `space_id` alone | `test_a_vector_of_another_length_is_refused`, `test_a_space_cannot_change_its_dimensions_while_it_holds_vectors`, the rules test |
| Remove `uq_embedding_spaces_current` | `test_only_one_space_can_be_current`, the rules test |
| Remove `ondelete="CASCADE"` from `chunk_id` | `test_a_new_chunk_run_removes_the_vectors_of_the_old_chunks`, `test_purging_a_deleted_document_removes_its_vectors` |
| `Vector(3)` in `persistence.py` only | `test_the_declared_vector_column_accepts_any_dimensions` |

Every prediction above held: the wrong-length vector and the lying row were
refused by the named constraints, NaN and infinity by pgvector with a
`DataError`, a second current space by the partial unique index, and the
dimension change by the composite key; the two-step switch kept both spaces'
vectors, and replacing, purging, or deleting removed exactly the expected
vectors.

## Reflection

1. **Why the database enforces the length too.** The port checks the
   vectors its adapter returns, but the port is only one writer. A backfill
   script, a manual `INSERT` during an incident, a future adapter that skips
   the check, or a migration that copies vectors between spaces all write
   to the table without passing through it. The database is the one place
   every write passes, so the rule holds whatever the application does.
2. **Why each row repeats `dimensions`.** A `CHECK` sees only its own row;
   it cannot look up the space's dimensions in another table. Repeating the
   number brings it into the row, and the composite foreign key proves the
   copy is the space's real value, so a row cannot simply claim whatever
   length its vector happens to have.
3. **Why clear the old space first.** PostgreSQL checks a unique index after
   each row it changes, not at the end of the statement, and the index
   cannot be deferred. Setting the new space first leaves two current rows
   for a moment, and the update fails. Clearing the old one first never
   passes through that state; one transaction makes the two steps look like
   one to readers.
4. **Why the column has no fixed length.** A `vector(1536)` column would
   hold only one model's vectors, so moving to a model of another size would
   need a new table or a rewrite. The cost is that similarity indexes need a
   fixed length: each space needs its own partial index with a cast, such as
   `(embedding::vector(1536)) WHERE space_id = ...`, and queries must use
   the same expression to reach it.
5. **What the missed drift says about one check.** `alembic check` passed
   while the declarations and the database disagreed, because it compares
   only what it knows how to compare. A green check proves only the
   properties it actually inspects, so the rules it cannot see need their
   own tests, ideally from both sides.
6. **A test whose removal would let a real bug through.**
   `test_two_spaces_keep_their_vectors_while_the_current_one_switches`. It
   is the test that states "several spaces coexist". With the primary key
   shrunk to `chunk_id` alone, a break run on a fresh database, the drift
   check and the migration round trip still passed; only this test and
   `test_deleting_a_space_removes_only_its_vectors` failed, and the second
   stores two vectors only as setup for something else. Without them the
   bug would surface in M04-T10: backfilling the new model's vectors would
   fail on the key, so retrieval could not move to a new model.

The answers in this evidence were drafted by Claude at the learner's request
and have not yet been rewritten in the learner's own words.
