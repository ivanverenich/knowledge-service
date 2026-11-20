# M07-T14 — Write durable audit events evidence

## Invariant

Each of the six audited actions writes exactly one durable audit event per
object it acts on: who acted, the action, the object, `allowed` or
`denied`, how it ended, the request or Synchronization Run, and when. An
event that describes a change commits in the same transaction as the
change; a Question's event commits before its Citations and completion are
sent, or the stream ends with an error. No event holds a Question, an
Answer, a title, or a Document's text.

## Expected failing check

Before the change, `uv run pytest tests/test_audit_events_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_audit_events_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a client receives when the audit store is down at the moment the stream decides: the text already streamed, `checking`, the usage, and an `error` event with `dependency_unavailable`; no `citation` and no `completion`.
- Which audit events one answered Question writes, and in which order: one `document.read` (`allowed`) per cited Document, written by the Citation check, then one `question.asked` (`answered`), all with the request ID and the reader as actor.
- What the event of a failed Synchronization Run holds: `synchronization.finished` against the Source, outcome `failed`, the run ID, no actor, and only `{"failure": "<ErrorType>"}`; never the error's message.
- How many tests in `tests/test_audit_events_integration.py` fail if Access Grant writes stop recording their event: 3 (grant change, synchronization, and the UTC read-back).

## Design

- **The event.** `AuditEvent` (`audit.py`) now holds `decision` (`AuditDecision`: `allowed`, `denied`), `outcome` (`AuditOutcome`, moved from `stream_failures` and extended with `succeeded`, `failed`), `request_id`, and `run_id`, beside the actor, action, target kind and ID, and UTC time. Every field must have its exact type. Metadata holds at most eight labels, counted while read: snake_case keys of at most 32 characters, and values that are booleans, integers below 2**53, or one word of ASCII letters, digits, and `_ . : / -`, so no sentence or encoded text fits.
- **The table.** Migration `b1c2d3e4f5a6` adds the four columns, fills old rows once with `allowed`/`succeeded` and drops the defaults, widens the action and target-kind checks, adds decision and outcome checks, and a partial index `ix_audit_events_request_occurred_at` for `load_request_audit_events`. A downgrade deletes rows the old schema cannot hold truthfully. Rows load back in UTC whatever the session time zone.
- **Same transaction as the change.** `_write_grants` (every Access Grant write in `reconcile_document` and `narrow_access`) and `publish_document` record `document.access_changed` (`access_event`, `ingestion/audit_events.py`) with the Authorization Version and reader count; `rate_message` records `message.rated`; `start_embedding_migration` and `switch_to_target_space` record their step with the administrator's `actor_id`.
- **Synchronization.** `synchronize_source` takes or makes a Synchronization Run ID, passes it to every grant change, and records one `synchronization.finished` when the pass ends, in its own transaction: `succeeded` with counts, or `failed` with the error type only; the pass's own error is always raised.
- **Reads.** `confirm_citations` records one `document.read` per cited Document in its own as-reader transaction: `allowed` when all its Citations were confirmed, `denied` when one failed.
- **The Question.** `AnswerStreamer` takes an `AuditTrail` (`DatabaseAuditTrail`, own committed transaction). `_Span.record` writes one `question.asked` once the decision is known, before any Citation, usage, or completion is sent; a failed or slow (> `AUDIT_SECONDS`) write turns the decision into a `dependency` error. Failures and cancellations are recorded as the stream closes. The write runs as its own task, awaited inside an anyio shield, and a cancellation from asyncio is re-raised as the same object, so neither kind of cancellation loses the record. Telemetry gains `audited`.

## Verification

Completed 2026-11-20.

- Expected failing check: before the change, `uv run pytest tests/test_audit_events_integration.py -q` collected nothing (`ERROR: file or directory not found`).
- Step checks, applied from the guide: `uv run pyright src` 0 errors; `tests/test_audit.py tests/test_indexes.py` 29 passed; migrations, records, and index integration tests passed; deletion, reconciliation, synchronization, and publishing integration tests passed; conversations and embedding migration integration tests passed; citation tests passed; streaming, stream failure, and telemetry tests passed.
- `uv run pytest tests/test_audit.py tests/test_query_audit.py -q`: 36 passed. `uv run pytest -m integration tests/test_audit_events_integration.py -q`: 13 passed.
- Deliberate break (no record in `_write_grants`): 3 failed, 10 passed; restored: 13 passed. The prediction was right.
- Gates: `make format-check` (297 files), `make lint`, `make typecheck` (0 errors), `make docs-check`, `git diff --check` clean; `make test-unit` 2896 passed (+28); `make test-integration` 654 passed (+13) with PostgreSQL and Keycloak running.
- The guide was verified by applying it to a clean copy of HEAD step by step: every step check passed and the result equals the implementation byte for byte; the Ukrainian guide's code blocks equal the English ones.
- Privacy: tests plant a marker in a Question, a Document title, and a Source error, and search every audit row and every log record; it appears in none.
- Known limits: a 403 role refusal leaves no event; `document.read` records the check's decision, not delivery; only embedding migration start and switch count as administration, with the actor passed by hand; ratings and migration steps carry no request ID yet; the service's database user can still update or delete audit rows; a failed final stream record shows only as `audited: false`; a cancelled pass records nothing, and a pass whose success cannot be recorded raises although its work is committed; no production entry point builds the stream with `DatabaseAuditTrail` yet.

## Review

- **Adversarial review of the prototype** (before the guide): a native `task.cancel()` during the decision's write lost the record (blocking); huge integers could encode text or crash the insert; a mapping lying about its length passed more than eight labels; a downgrade rewrote `denied` rows as `allowed`; `publish_document` wrote grants without a record; events failed to load under a non-UTC session. All fixed, each with a test shown to fail on the unfixed code.
- **Closing Standards review**: the hand-made re-raise in `_Span.record` escaped anyio cancel scopes and spun the wait (about 12,900 iterations in a probe) — blocking, fixed with an anyio shield and re-raising the caught object, plus `test_a_cancel_scope_that_ends_the_stream_mid_record_absorbs_it`, which fails on the previous code; `publishing` imported from `reconciliation` (moved `access_event` to `ingestion/audit_events.py`); the unaudited-run warning gained the run, Source, and error type.
- **Closing Spec review**: no blocking gaps; added a rollback test for grant writes, a log scan on the HTTP test, the pass's end time for `synchronization.finished`, and the remaining limits to the threat model.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Labels instead of free text.** A length cap still lets a short Question through; a rule that values are single words or small numbers makes text impossible to store, whatever a later caller passes.
- **The same transaction as the change.** An audit row in a file or another store can disagree with the data; in the same PostgreSQL transaction, the change and its record commit or roll back together.
- **No decision leaves unrecorded.** The record is written before Citations and completion; an unrecorded Answer becomes an error, because an audit trail that may miss delivered answers proves nothing.
- **The write as its own task, shielded.** Cancellation is how the server reacts to a client leaving; the write must outlive it, and the original cancellation must continue afterwards so its scope still recognises it.

**Falsified assumption.** I assumed that `anyio.move_on_after(..., shield=True)` protects a write from any cancellation. The adversarial review shows the server also cancels with plain `asyncio` `task.cancel()`, which that shield does not stop, and the closing review shows that replacing it with a hand-made `CancelledError` breaks anyio's own scopes. So "one shield covers every cancellation" is wrong: both kinds need handling, and a caught cancellation must be re-raised as the same object.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It hides the audit event entirely: building it, choosing the actor and labels, and writing it on the same connection. If every caller wrote it, one forgotten call would leave a rating with no trace, and a caller could write it in another transaction.
2. A complete Answer with Citations that no `question.asked` row shows: if the write then failed or the process stopped, the trail would claim nothing was answered.
3. The trail is read only by the service's operators, who need to see that access was refused; the client must not learn whether a Document exists or why an Answer was withdrawn.
4. `failure` holds an exception type name; a later change that put a message or a file name there would leak it. `test_a_failed_synchronization_run_records_only_the_error_type` plants a marker in the error message and would catch it.
5. An `AuditTrail` (or runtime) available to `admit` in `app.py`, recording `question.asked` with `denied` before the 403 is returned; a test would call the stream endpoint with a token lacking the `user` role and read the request's trail back.
