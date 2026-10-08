# M02-T08 — Access Grants evidence

Completed: 2026-10-07

## Invariant

A grant names exactly one subject: everyone, one user, or one group. It never
names a group and a user at once, and it never names nobody. `subject_id` is
null exactly when the subject is public, so those two shapes cannot be stored.
One Document holds at most one grant per subject, and deleting a Document takes
its grants with it. Because the kind is a column, one SQL query tells public,
user, group, and no-access apart without any application filtering.

## Prediction

I expect the revision to create one `access_grants` table with a CHECK on
`subject_kind`, a CHECK tying `subject_id is null` to the public kind, a
`UNIQUE NULLS NOT DISTINCT (document_id, subject_kind, subject_id)`, and a
cascading foreign key to `documents`. The `NULLS NOT DISTINCT` part should be
what stops a second public grant for the same Document, since PostgreSQL
otherwise treats each NULL as distinct. A `CASE` query over `EXISTS` should
return `public`, `user`, `group`, and `none` for four Documents that carry one
grant each except the last. Four bad rows should be rejected by the table
itself: a public grant with an id, a user grant with no id, a duplicate public
grant, and an unknown kind.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_access.py tests/test_indentifiers.py -q`
  — 91 passed. The 6 access tests cover the stable grant order, a subject
  granted twice, a grant for another Document, a public subject that names
  someone, a user kind holding a `GroupId`, and a naive grant time. The
  identifier suite now covers `AccessGrantId` through the same parametrized
  cases as every other identifier type.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 7 passed in 1.14s against PostgreSQL 17.11, covering migrations, Sources,
  Documents, Chunks, and Access Grants. The access test passed on two
  consecutive runs. It proves one `CASE` query returns `public` for the
  publicly granted Document, `user` for the user-granted one, `group` for the
  group-granted one, and `none` for the Document with no grants, and that
  `load_access_grants` returns `Subject.group(GROUP_ID)` for the group
  Document. It also proves the table rejects four bad rows with
  `IntegrityError`: `public` with a `subject_id`, `user` with a null
  `subject_id`, a second `public` grant for the same Document, and an unknown
  `subject_kind`. No connection URL or credentials are recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 213 passed, 6 skipped, 2 deselected, total
  coverage 84%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/identifiers.py`,
`src/knowledge_service/access.py`,
`migrations/versions/9f4e3aa29479_access_grants.py`,
`src/knowledge_service/persistence.py`, `tests/test_indentifiers.py`,
`tests/test_access.py`, and `tests/test_access_integration.py`. The table stores
subject kinds and internal identifiers only, so no credential, connection
string, or private source content entered a tracked artifact; the integration
test reads only the dedicated disposable-database variable.

## Reflection

1. `replace_access_grants` checks that every grant belongs to the Document being
   written, that no subject appears twice, and that the set covers the whole
   Document rather than merging with what was there before. The `Subject` value
   checks that each grant names exactly one reader. A caller writing its own SQL
   would most likely forget the "one row per subject" rule, because the database
   catches it only if the caller remembers to include the unique constraint —
   and would probably forget to clear the old grants at all, leaving a Document
   open to a user who was removed.

2. Letting one row name both a user and a group would move the "any of these may
   read" rule into application code. Then the decision could no longer be made
   inside the retrieval query: every caller would have to fetch grants, expand
   the subject list, and apply the rule itself, and each of those paths could
   differ. Worse, a query that filters Candidates in SQL would have to treat a
   two-subject row as an OR, which a single equality predicate cannot express.

3. Without that assertion, a `user` grant with a null `subject_id` could sit in
   the table looking like a restriction while matching nobody, or — depending on
   how a later query was written — matching everyone. Nobody would notice until
   a user either could not reach a Document they should read, or read one they
   should not.
