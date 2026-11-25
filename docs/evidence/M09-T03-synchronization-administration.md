# M09-T03 — Synchronization administration evidence

## Invariant

A Source has at most one open synchronization Job, whoever asks and however
often: a start returns that Job while it is open (queued, running, or failed
with a retry planned) and queues a new one only after it reaches a final
state. Only an enabled Source gets a Job, from a start or from a retry, and
a disable never lands between a start's check and its write. Every
operation reads and decides from PostgreSQL; Redis only delivers, after the
Job and its audit event committed. A Source's history lists its own
synchronization Jobs once each, however they are retried while it is read.
Every refusal of start, status, history, retry, and cancel has one fixed
status, code, and message.

## Owners

- Start and history: `src/knowledge_service/synchronization_administration.py`
  (`start_synchronization`, `load_synchronization_history`,
  `DatabaseSynchronizations`).
- One open Job per Source: the open-intent unique index on `jobs` (M08-T03),
  through `enqueue_job`.
- Enabled Sources only: `lock_enabled_sources` (`FOR SHARE`) in the start and
  in `job_decisions.retry_job`, against the `FOR UPDATE` of
  `set_source_enabled`.
- Delivery: `job_dispatch.publish_job` after commit, and the relay for any
  Job still unpublished.
- Authorization: `start_synchronization_runs` (content-manager) for start,
  history, and status; `recover_jobs` (administrator) for retry and cancel.
- Retries: the stored Job's own policy (M08-T04) and an administrator's
  retry; a client may repeat a start safely.

## Expected failing check

Before the change, `uv run pytest tests/test_synchronization_administration.py -q`
reports `no tests ran` with exit code 4, because the file does not exist.
Observed on 2026-11-25 at `b188bed`: `no tests ran in 0.08s`, exit 4.

## Predictions

Claude wrote these while preparing the lesson, from the prototype; they are
not blind predictions by the learner.

- Four starts at once: one Job, one `job.queued` event, statuses
  `[200, 200, 200, 202]` (observed over HTTP).
- A Job of a disabled Source that failed for good, retried: 409
  `source_disabled`, the Job stays failed (before the fix: 200 and queued).
- History pages J4 and J3, then J1 is retried: page 2 still shows J1 (before
  the fix it never did).

## Operations

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

| Operation | Route | Role | Success | Refusals |
|---|---|---|---|---|
| start | `POST /v1/sources/{source_id}/synchronizations` | content-manager | 202 queued, 200 open Job; `Location` | 404 `source_not_found`, 409 `source_disabled`, 422 ID |
| history | `GET /v1/sources/{source_id}/synchronizations` | content-manager | 200 `{items, next_cursor}` | 404, 422 ID or page |
| status | `GET /v1/jobs/{job_id}` | content-manager | 200 | 404 `job_not_found` |
| retry | `POST /v1/jobs/{job_id}/retry` | administrator | 200 | 404, 409 `job_already_open`, `decision_not_allowed`, `source_disabled` |
| cancel | `POST /v1/jobs/{job_id}/cancel` | administrator | 200 | 404, 409 `job_running`, `decision_not_allowed` |

A start that queues a Job records `job.queued` with the operator as actor
and `{"kind": "synchronize_source"}`; a start that finds an open Job records
nothing. The history pages on the Job's ID, a UUIDv7 that never changes,
because a retry rewrites `queued_at`; each page reads Jobs and their newest
attempts (`DISTINCT ON`) in one `REPEATABLE READ` snapshot, along the index
`ix_jobs_synchronization_target (target_id, job_id)`.

## Design

Changed files:

- `src/knowledge_service/synchronization_administration.py` (new).
- `src/knowledge_service/source_administration.py`: `SourceDisabled`.
- `src/knowledge_service/job_decisions.py`: a synchronization retry checks
  the Job's status, then share-locks its Source.
- `src/knowledge_service/persistence/jobs.py`: `load_target_jobs`.
- `src/knowledge_service/persistence/job_attempts.py`: `load_latest_attempts`.
- `src/knowledge_service/persistence/schema.py` and
  `migrations/versions/b4c5d6e7f8a9_synchronization_history_index.py`: the
  index becomes `(target_id, job_id)`.
- `src/knowledge_service/contracts.py`: `SynchronizationHistory`.
- `src/knowledge_service/app.py`: `Synchronizations`, the two routes, the
  retry's 409 `source_disabled`.
- `src/knowledge_service/api_routes.py`: two rows served.
- Tests: `tests/test_synchronization_administration.py` (39),
  `tests/test_synchronization_administration_integration.py` (20);
  `test_api_routes.py` and `test_app_authentication.py` updated.
- `docs/architecture.md`, `docs/threat-model.md`, `docs/operations.md`.

Replay and cancellation: a repeated start is a 200 for the same Job; a
cancelled Job is final and the next start queues a new one; a message sent
twice runs nothing twice (M08). A start makes at most one Redis call,
bounded by the publisher's own timeouts; no paid call.

## Verification

Narrow checks (2026-11-25): `uv run pytest tests/test_synchronization_administration.py -q`:
39 passed. `tests/test_synchronization_administration_integration.py -m integration`:
20 passed.

Deliberate break (guide Step 9): with `if source_id not in enabled:` changed
to `if False:` in `start_synchronization`, `PYTHONDONTWRITEBYTECODE=1`, the
integration file gave `1 failed, 19 passed`
(`test_a_disabled_or_missing_source_queues_nothing`). Restored: 20 passed.
While preparing the lesson, eleven mutations were each caught: a disabled
Source starting, every start recording `job.queued`, history of every kind,
a returned open Job answering 202, a full last page claiming another, the
oldest attempt shown, the ID read before the role, the `source_disabled`
code changed, a start publishing nothing, a retry ignoring a disabled
Source, and history paging on `queued_at`.

Gates (2026-11-25), one after another on one test database, with the new
files staged: `uv run ruff format --check .` (371 files already formatted),
`uv run ruff check .` (all checks passed), `uv run pyright` (0 errors),
`make docs-check` (exit 0), `make test-unit` (3681 passed),
`make test-integration` (948 passed), `git diff --cached --check` (clean),
`uv run pre-commit run` (every hook passed, gitleaks included).

Security and privacy: no request value reaches a log line; a store failure
is logged by its error's type only, and a marker planted in it appears in no
response or log, for start and history. The audit event names only the
Job's kind.

## Review

While preparing the lesson, an adversarial review found that history skipped
a Job retried between two pages (it paged on `queued_at`, which a retry
rewrites; now it pages on the UUIDv7 ID), and that a retry queued a Job for
a disabled Source (now refused with 409 `source_disabled`). Both fixes have
tests that failed first.

At completion, a Standards review and a Spec review ran independently;
neither found a blocking problem. Fixed: a retry of a Job whose status never
fits answered `source_disabled` for a disabled Source (now the Job's status
is checked first: `decision_not_allowed`); each history page sorted every
synchronization Job of the Source (now the partial index is
`(target_id, job_id)`); docstrings and docs said a retry checks the Source
"as a start does" though a missing Source is 404 for a start and 409 for a
retry. Added HTTP-level tests for concurrent starts, open and final states,
and disabled or missing Sources; a cancel-of-a-missing-Job row in the error
table; a leak test for history; and a test that a disable waits for a start
holding the Source. The first two fixes have tests that failed first.

## Known limits

- A start cannot choose a full or an incremental pass.
- `POST .../synchronizations` takes no body and ignores a query string.
- A history cursor is a position only, not tied to its Source.
- UUIDv7 order follows the creating machine's clock.
- Without a publisher, or when Redis refuses, the Job waits for the next
  `make worker-relay` pass.
- A 403 is not audited.

## What would falsify the design

- Two open synchronization Jobs for one Source.
- A Job stored for a disabled Source by a start or a retry.
- A 202 that queued nothing, or a 200 that queued something.
- A Job listed twice, or never, while a history is paged.
- A Job lost because a send failed after its start answered.
- A refusal whose status, code, or message changed.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

The start needed no check of its own for "already running": the partial
unique index already makes a second open Job impossible, so the code only
reports whether it stored one (202) or found one (200), which also makes a
client's retry after a timeout harmless. The two review findings had one
root: a value that looks stable but is not. `queued_at` is rewritten on every
retry, so it could not hold a page boundary, while a UUIDv7 ID never
changes; and "disabled means read nothing" had to hold at every door that
queues a Job, not only the new one.
