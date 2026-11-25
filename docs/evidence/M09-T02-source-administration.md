# M09-T02 — Source administration evidence

## Invariant

A Source is its kind and its location, and no two Sources share them. Only
a caller whose role permits `configure_sources` registers, reads, lists,
changes, enables, or disables one, and that is checked before any ID, page,
or body is read. Every setting is checked against its rule, and each value
has one accepted spelling. No secret passes through the API:
`credentials_reference` only names one. No request value, refused or not,
appears in an error, a log line, or an audit event. Each change is stored
together with exactly one audit event naming who changed which settings; a
change that changes nothing stores and records nothing.

## Owners

- The rules and operations: `src/knowledge_service/source_administration.py`
  (`SourceDefinition`, `register_source`, `change_source`,
  `set_source_enabled`, `DatabaseSourceAdministration`).
- One Source per kind and location: PostgreSQL's `uq_sources_kind_location`,
  through `INSERT ... ON CONFLICT DO NOTHING` in `persistence/sources.py`.
- Authorization: each route's `require(caller.roles,
  Operation.CONFIGURE_SOURCES)`, before input.
- The audit trail: `persistence.audit`, in the same transaction as the
  change; the three new actions are in `AuditAction` and in
  `ck_audit_events_action` (migration `a3b4c5d6e7f8`).
- Retries: none by the service. A repeated registration gets 409; a repeated
  change or switch records nothing the second time.

## Expected failing check

Before the change, `uv run pytest tests/test_source_administration.py -q`
reports `no tests ran` with exit code 4, because the file does not exist.
Observed on 2026-11-25 at `a190059`: `no tests ran in 0.08s`, exit 4.

## Predictions

Claude wrote these while preparing the lesson, from the prototype; they are
not blind predictions by the learner.

- Two registrations of `/srv/handbook` at the same moment: one row; the
  second caller gets 409 `source_exists` (four at once in
  `test_two_registrations_at_once_store_one_source`: one stored, three
  `SourceExists`).
- A `user`-role caller sends `POST /v1/sources` with a body that breaks every
  rule: 403, and the store is never asked.
- `PATCH {"default_principals": []}` on a Source with none: 200 and no new
  audit event.

## Routes

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

| Route | Success | Refusals |
|---|---|---|
| `GET /v1/sources` | 200 `{items, next_cursor}`, newest first | 422 bad `limit`, `cursor`, or other parameter |
| `POST /v1/sources` | 201 with `Location` | 409 `source_exists`, 413, 422 `validation_error` (shape) or `invalid_source_settings` (rule) |
| `GET /v1/sources/{source_id}` | 200 | 404 `source_not_found`, 422 bad ID |
| `PATCH /v1/sources/{source_id}` | 200 | 404, 409 `source_needs_repair`, 413, 422 |
| `POST /v1/sources/{source_id}/enable`, `/disable` | 200 with the new state | 404, 422 bad ID |

Every route also answers 400, 401, 403, 500, and 503 in the one
`ErrorResponse` shape, and all six rows of `api_routes.ROUTES` are served.

## Identity and secrets

A duplicate is refused by the database, not by a check before the insert:
the insert does nothing on the unique constraint and returns no row, which
becomes 409. A folder must be an absolute path with no `.`, `..`, doubled or
trailing `/`, in Unicode NFC, and not the root; a Confluence site must be
`https` with a lowercase host name (no IP address, no trailing `.`), no
default or empty port, and no user, password, query, fragment, `%`, or
trailing `/`. Another spelling is refused, not rewritten.

`credentials_reference` must be lowercase words joined by `-` in up to four
`/` parts, such as `confluence/acme-prod`; a folder takes none. Request
bodies are strict (`extra="forbid"`, `strict=True`). A rule's refusal is a
fixed sentence; a shape's refusal is the generic `validation_error`.

## Design

Changed files:

- `migrations/versions/a3b4c5d6e7f8_source_administration.py` (new): the
  three actions in `ck_audit_events_action`; the downgrade stops while any
  such event exists.
- `src/knowledge_service/audit.py`, `persistence/schema.py`:
  `source.updated`, `source.enabled`, `source.disabled`.
- `src/knowledge_service/contracts.py`: `SourceRegistration`,
  `SourceChanges`, `SourceView`, `SourcePage`; limits imported from the rules.
- `src/knowledge_service/persistence/sources.py`: `SourceRecord`,
  `insert_source_record`, `load_source_record`, `save_source_record`,
  `load_source_page`.
- `src/knowledge_service/source_administration.py` (new).
- `src/knowledge_service/app.py`: `SourceAdministration`, `source_view`, the
  six routes, `read_body` and `body_schema` (the Question route uses both).
- `src/knowledge_service/api_routes.py`: six rows served.
- Tests: `tests/test_source_administration.py` (114),
  `tests/test_source_administration_integration.py` (18); `test_api_routes.py`
  (the routes' error statuses) and `test_app_authentication.py` (a Source
  store fake, bodies, PATCH in the body-before-role test).
- `docs/architecture.md`, `docs/threat-model.md`.

Replay and cancellation: every operation is one transaction; a client that
repeats a request gets 409 or a no-op. Disabling takes `FOR UPDATE`, which
waits for the scheduler's `FOR SHARE`, so a Job is queued before the change
or not at all; it does not cancel a queued or running Job. No external,
network, model, or paid call.

## Verification

Narrow checks (2026-11-25): `uv run pytest tests/test_source_administration.py -q`:
114 passed. `tests/test_source_administration_integration.py -m integration`:
18 passed.

Deliberate break (guide Step 9): with
`.on_conflict_do_nothing(constraint="uq_sources_kind_location")` removed,
`PYTHONDONTWRITEBYTECODE=1`, the integration file gave `3 failed, 15 passed`
(`test_the_same_kind_and_location_is_registered_once`,
`test_two_registrations_at_once_store_one_source`,
`test_a_duplicate_over_http_is_a_409_and_stores_nothing`; the API answered
500 on the unexpected `IntegrityError`). Restored: all pass. While preparing
the lesson, nine more mutations were each caught: any reference accepted, no
audit on a change, a no-op change writing, the body read before the role, a
full last page claiming another, a refusal quoting the value, a PATCH passing
unnamed fields, the host spelling not compared, and uniqueness removed. At
completion: removing the PATCH rule mapping failed
`test_a_change_that_breaks_a_rule_over_http_names_the_rule_only`, and making
`/disable` enable failed the HTTP switching test.

Gates (2026-11-25), one after another on one test database:
`uv run ruff format --check .` (367 files already formatted),
`uv run ruff check .` (all checks passed), `uv run pyright` (0 errors),
`make docs-check` (exit 0), `make test-unit` (3606 passed),
`make test-integration` (928 passed), `git diff --check` (clean),
`uv run pre-commit run --all-files` (all passed, but it skipped the new
files, which were not yet added to git). Once they were staged,
`uv run pre-commit run` failed in gitleaks (`github-pat`) on a test value
shaped like a GitHub token; the value is now built in parts, and every hook
passed on the staged files.

Security and privacy: tests plant a marker in a refused reference, a site's
user, password, and query, an unknown field, a display name, and a store
error, and find it in no response, header, log line, or audit row. Logs
carry only an error's type. A 403 is not audited, as on every route.

## Review

While preparing the lesson, an adversarial review found one site stored
under several spellings (empty port, trailing dot, leading-zero port, IP
literals), one folder under two Unicode forms, and a change that blamed a
field the caller never sent on a stored Source that broke a rule (now 409
`source_needs_repair`); mutation checks added a full-last-page test. Each fix
has a test that failed first.

At completion, a Standards review and a Spec review ran independently. Fixed:
a NULL element in `default_principals` was hidden on read and silently
dropped by the next change (now shown as `null`, and a change answers 409);
the refusal property test could pass on valid input (now every input is
invalid and the refusal is required); the length limits were written twice
(the request models now import them, and a test pins the changeable fields);
the unknown-kind message listed kinds by hand. Added HTTP tests for a
rule-breaking PATCH (response, log, and storage) and for enable and disable.
The Spec reviewer's report that the integration gate failed was the Step 9
deliberate break, caught while it was briefly applied; the file was
restored and the full gate rerun.

## Known limits

- A symlink, a case-insensitive file system, or a second address of one
  site still make two Sources of one place.
- A secret that fits the name rule, such as one lowercase word, is stored as
  a name.
- The API does not check that a folder exists; the worker's first Job shows it.
- A Confluence Source registers, but its synchronization fails with
  `source_misconfigured` until M08-T11.
- New default readers reach stored Documents at the next pass.
- Disabling does not cancel a queued or running Job (M09-T03).
- `SourceRecord` and the ingestion `Source` model are parallel views of one
  row: operator writes go through `source_administration`, synchronization
  reads through `ingestion.synchronization_jobs`.

## What would falsify the design

- Two rows in `sources` for one folder or site.
- A 422, 404, or 409 to a caller without the content-manager role.
- A secret or refused value in any response, log line, or audit row.
- A change stored without its audit event, or an audit event holding a value.
- A PATCH that changes a field its body did not name.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

The local folder is a path on the worker's machine: the API stores the text
and never reads the folder, so a client's own files are out of reach, and in
containers the folder must be mounted into the worker. Confluence fits a
deployed service better: a site address and a bot account's token, named by
reference, with each page's own permissions carried over to its Documents.
Refusing a second spelling instead of rewriting it keeps what the operator
sees equal to what is stored, and lets the database's unique constraint
decide identity by plain text comparison.
