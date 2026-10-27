# M04-T10 — Embedding migration evidence

## Invariant

At every moment exactly one Embedding Space is current, and search reads
only that space, so a query is never compared with vectors of another
model or length, and search is never without a space. A migration moves
only `dual_writing → switched → cleaned`. While dual-writing, publication
writes to both spaces and search reads the source. The switch makes the
target current in one transaction, and only once the target has a vector
for every searchable Chunk the source has. Clean-up deletes only the old
space. No vector is written into a space search will never read again.

## Expected failing check

Before the change, the new test files fail to import
`knowledge_service.embedding_migration` and
`knowledge_service.embedding_migration_workflow`, and the migration drift
test would fail once `embedding_migrations` is declared without its
migration.

Predictions once it exists:

- Moving 3 Documents from an 8-dimension model to a 4-dimension one: after
  every step, a search with the current model's query finds them all, and a
  query of the other length raises `NoCurrentEmbeddingSpace`.
- A Document published while dual-writing has vectors in both spaces.
- Switching before the backfill: `IncompleteBackfill` with the number of
  missing vectors, and the old space stays current.
- Cleaning up before switching, or switching twice:
  `InvalidMigrationTransition`.
- Two starts at once: one migration, one `MigrationInProgress`.
- A Document the new model cannot embed: listed in `failed`; the others are
  still backfilled.

## Design

Completed: 2026-10-27

Changed files:

- `src/knowledge_service/identifiers.py`: `EmbeddingMigrationId`.
- New `src/knowledge_service/embedding_migration.py`: `MigrationState`
  (`dual_writing`, `switched`, `cleaned`), `MigrationStep`, `SpaceRole`,
  `next_state`, `written_spaces`, `EmbeddingSpace`, `EmbeddingMigration`,
  `EmbeddingMigrationError`, and `InvalidMigrationTransition`. Pure domain
  code; no database import.
- `src/knowledge_service/persistence.py`: the `embedding_migrations` table
  (space foreign keys `ON DELETE SET NULL`, a state check, an `updated_at`
  check, three foreign-key indexes) and the space and migration helpers,
  including `space_accepts_writes`, `make_embedding_space_current`, and
  `count_chunks_missing_from_target`.
- New migration `b6c7d8e9f0a1_embedding_migrations.py`.
- `src/knowledge_service/embedding_publication.py`: the compare-and-swap
  also returns `STALE` when the space accepts no writes.
- `src/knowledge_service/vector_search.py`: the statement reads the space
  only while it is current (`EXISTS`), and an empty result is rechecked so a
  cutover raises `NoCurrentEmbeddingSpace` instead of finding nothing.
- New `src/knowledge_service/embedding_migration_workflow.py`:
  `start_embedding_migration`, `embedding_models_to_write`,
  `backfill_embeddings` (with `BackfillProgress`), `switch_to_target_space`,
  and `clean_up_source_space`, each step in its own short transactions.
- `tests/test_indexes.py`, new `tests/test_embedding_migration.py`
  (14 tests) and `tests/test_embedding_migration_integration.py` (23 tests).
- `docs/architecture.md`: ER lines, the table's rule, and a state diagram.

What each state does:

| State | Publication writes to | Search reads | Leaves by |
|---|---|---|---|
| `dual_writing` | source and target | source | switch, only when the target lacks no vector the source has |
| `switched` | target | target | clean-up, which deletes the source space and its vectors |
| `cleaned` | target | target | none; a new migration may start |

The switch locks the migration and both space rows (in a fixed order),
counts the missing vectors, and flips `is_current` from the old space to the
new one in one transaction, so exactly one space is current before and
after the commit.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embedding_migration.py -q` | 14 passed |
| `uv run pytest tests/test_embedding_migration_integration.py tests/test_indexes.py tests/test_migrations_integration.py -q` | 30 passed |
| `uv run pytest -m "not integration and not live" -q` | 831 passed, 189 deselected |
| `make test-integration` | 186 passed, 834 deselected |
| `make format-check`, `make lint`, `make typecheck` | 140 files formatted; all checks passed; 0 errors |
| `make check` | 1017 passed, 3 deselected; coverage 94%, `embedding_migration_workflow.py` 95% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

Deliberate breaks, each undone afterwards (run on an identical copy):

| Break | Failed |
|---|---|
| `space_accepts_writes` removed from publication | `test_a_late_write_into_the_old_space_is_refused_after_the_switch` |
| The switch skips the missing-vector check | `test_switching_before_the_backfill_is_done_is_refused`, `test_a_vector_written_only_to_the_old_space_blocks_the_switch`, `test_one_document_the_model_cannot_embed_does_not_stop_the_others` |
| Search skips the recheck of an empty result | `test_a_search_across_the_cutover_says_so_instead_of_finding_nothing` |
| Search drops the current-space `EXISTS` | nothing failed; see the gaps below |

Every prediction above held, as each case has its own test.

Adversarial review: an independent review attacked the first version on
the real database. Its breaks, each fixed with a regression test that fails
when the fix is removed:

- One Document the model could not embed stopped the whole backfill; it is
  now listed in `failed`, and a cursor (`resume_after`) moves past it.
- A search that read the current space just before a switch and clean-up
  returned an empty result with no error; it now raises
  `NoCurrentEmbeddingSpace`.
- A publication that started before the switch could still write into the
  retired space; such a write is now `STALE`.
- Unchecked concurrency and timeout values, an invalid target configuration
  escaping as another error type, and a step dated before the previous one.

Standards and Spec reviews found nothing blocking. Gaps and limits they
noted, left for later:

- The `EXISTS` guard is untested: it matters only when a switch commits
  between the search's space lookup and its query, before clean-up; no test
  reaches that window.
- Dual-writing is advice to callers: `embedding_models_to_write` has no
  caller in `src/` yet. If a publisher skips the target, the switch stays
  safe because it raises `IncompleteBackfill` until backfill catches up.
- The test models differ in length (8 and 4), so mixing is detected through
  pgvector's dimension error; a same-length swap would not be.
- No test checks authorization after a switch, or that a second migration
  can start after clean-up.
- A dual-writing migration cannot be cancelled, and a Document the target
  model can never embed blocks the switch.
- No database constraint enforces a single open migration (the lock on the
  current space serialises starts), and the switch and clean-up hold locks
  while they count or delete.
- Non-blocking style notes: the workflow's module docstring calls every
  function a step, though `embedding_models_to_write` is a query run in the
  caller's transaction; bad backfill settings raise the base
  `EmbeddingMigrationError` rather than a named subclass.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **Why not delete and re-embed.** Search would have no vectors, or a mix
   of old and new ones, for hours. Users would see empty or wrong results
   until the re-embedding finished, and a failure halfway would leave
   nothing to go back to.
2. **Why dual-write.** Backfill only covers Documents that existed when it
   ran. Without dual-writing, every Document published during the backfill
   would be missing from the target, so the switch would keep refusing, or,
   without its check, search would lose those Documents.
3. **Before and after the switch.** Just before the commit a search sees
   the old space as current; just after, the new one. Clearing the old flag
   and setting the new one happen in one transaction, and PostgreSQL shows
   other readers either all of it or none of it, so there is never zero or
   two.
4. **Checking "still current" in the same statement.** The search looked
   up the space and then queried it as two separate reads. Asking "is this
   space still current?" inside the query itself means the answer comes
   from the same snapshot as the vectors. If the space was retired in
   between, the query finds nothing, and the recheck turns that into an
   error instead of a silent empty answer.
5. **A Document that can never be embedded.** Blocking is the safe choice:
   switching anyway would make that Document unsearchable without anyone
   being told. In practice `failed` and `remaining` show it; the fix is to
   repair or split the Document, or remove it from search, then rerun the
   backfill.
