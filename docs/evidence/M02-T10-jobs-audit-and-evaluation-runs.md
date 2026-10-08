# M02-T10 — Jobs, audit, and Evaluation Runs evidence

Completed: 2026-10-08

## Invariant

A Job moves only through the transitions its state machine allows: queued to
running, running to succeeded or failed, and failed back to queued while
attempts remain. It carries no payload and no raw error text, only a target
reference and a short error code. An audit event records an actor, an action, a
target, and a small bag of short scalar labels, and nothing larger, so raw
Question, Answer, or Document text has no place to go. An Evaluation Run names
the dataset version and configuration fingerprint it used, and it carries an
artifact location only when it succeeded, so a result can be traced back to what
produced it. No function updates or deletes an audit event.

## Prediction

I expect one revision to create `jobs`, `audit_events`, and `evaluation_runs`.
Each table should carry a CHECK mirroring every rule the matching value
enforces, so a direct insert cannot produce a row the domain would refuse.
`audit_events` should add a size guard on its metadata that PostgreSQL enforces
on its own. A Job should round-trip through queued, failed, and retried with its
attempt count intact and its error code cleared, two audit events against one
target should read back oldest first, and a raw insert of an oversized audit bag
and of a succeeded run with no artifact should both be refused.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_jobs.py tests/test_audit.py tests/test_evaluation.py tests/test_indentifiers.py -q`
  — 146 passed. The Job tests cover running to success, the retry loop until
  attempts run out, the `JobNotRetryable` refusal, an illegal transition from
  queued to succeeded, and an over-long error code. The audit tests cover short
  labels, a service actor, an over-long value, a structured value, an empty
  value, the bag ceiling, a naive time, and a frozen bag. The Evaluation Run
  tests cover starting and recording an artifact, a failed run keeping none, an
  illegal restart, and a succeeded run with no artifact location. The identifier
  suite now covers `EvaluationDatasetId` and `AuditEventId` through the same
  parametrized cases as every other type.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 9 passed in 1.44s against PostgreSQL 17.11, covering migrations, Sources,
  Documents, Chunks, Access Grants, Conversations, and the three record tables.
  The records test passed on two consecutive runs. Reading the tables after the
  run shows one Job at `queued` with attempt 1 of 2 and no error code, after
  having been failed and retried; two audit events against one target, the first
  from the User with `{"kind": "synchronize_source"}` and the second from the
  service with `{"attempt": 1}`, read back oldest first; and one Evaluation Run
  at `succeeded` over dataset version 3 with its artifact location. Two raw
  inserts the domain would never produce were both refused with
  `IntegrityError`: an audit bag far past the size limit, and a succeeded run
  with a null artifact location. No connection URL or credentials are recorded.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project make check`
  — Ruff format and lint passed, Pyright strict clean, 289 passed, 2 deselected,
  total coverage 88%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/identifiers.py`,
`src/knowledge_service/jobs.py`, `src/knowledge_service/audit.py`,
`src/knowledge_service/evaluation.py`,
`migrations/versions/2553cef719b4_jobs_audit_events_and_evaluation_runs.py`,
`src/knowledge_service/persistence.py`, `tests/test_indentifiers.py`,
`tests/test_jobs.py`, `tests/test_audit.py`, `tests/test_evaluation.py`, and
`tests/test_records_integration.py`. The tables store identifiers, statuses,
counts, thumbs-free labels, and short metadata values only, so no credential,
connection string, or private source content entered a tracked artifact; the
integration test reads only the dedicated disposable-database variable.

## Reflection

1. The caps stop a caller from having to decide, in the moment, how much is too
   much. Without them a queue row would eventually hold a full exception
   traceback and an audit row would hold a copied Question, so a table meant to
   be small and safe would quietly become a second copy of the content it is
   supposed to describe — with the retention and access rules of neither.

2. A retryable run would blur which result to trust. Two rows would describe the
   same dataset version and configuration fingerprint with different artifact
   locations, and a reader comparing them could not tell a re-run from a
   correction. Keeping a run one-shot makes "this configuration over this
   dataset version" point at exactly one artifact; a rerun is a new run with its
   own id, and the two can be compared honestly.

3. Without that assertion, a run could be marked succeeded with nowhere to look
   for its output. The status would read as a completed measurement while the
   artifacts were missing or overwritten, and nobody would notice until someone
   tried to reproduce a number and found no result behind it.
