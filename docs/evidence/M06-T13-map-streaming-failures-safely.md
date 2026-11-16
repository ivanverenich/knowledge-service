# M06-T13 — Map streaming failures safely evidence

## Invariant

`stream_failure(kind, phase)` is the one policy for a failed streamed
Answer. Before the response headers a failure is an HTTP error with the
M01 status, code, and message, and a `Retry-After` header for an outage or
a limit. After them it is exactly one terminal `error` event whose code,
message, and retry the reader checks against the same policy: an outage or
a limit is the caller's to retry, a validation failure is internal. A
database that is unreachable, overloaded, or too slow is an outage; any
other database error is internal. Every stream is recorded once with its
phase, one audit outcome, and what became of its partial text. No
response, header, or record holds a Question, an Answer, SQL, or a
connection string.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.stream_failures"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a client gets when PostgreSQL is unreachable while Evidence is found, before and after this change: before, `500 internal_error` with no retry advice, because any database error was a bug; after, `503 dependency_unavailable` with `Retry-After: 5`, and the record says `before_headers` and `dependency_error`.
- What a client gets when the model fails after half of the Answer's text was streamed, and what it shows the user: status `200` stays, and the stream ends with one `error` event, `dependency_unavailable` with retry `caller`; the reader drops the streamed text and shows the fixed message, and the record says `after_headers`, `dependency_error`, `replaced`.
- What the stream's record says when the client reads the `completion` event and then leaves at once: `answered` and `kept`, because the end is recorded before the last event is handed over.
- How many integration tests fail if a database outage is classified as a bug again: both, the unreachable server and the statement timeout, because each becomes `500` without `Retry-After`.

## Design

- **Two phases, one table.** `answering/stream_failures.py` adds `stream_failure(kind, phase)`, which returns a `StreamFailure`: the kind the client sees, the phase, the HTTP status and `Retry-After` (before the headers only), the public code and fixed message, the retry owner, what becomes of the partial text (`PartialText`), and the `AuditOutcome`. The endpoint, the event writer, the event reader, and the stream's record all ask it.
- **Before the headers.** The M01 status, code, and message; `Retry-After: 5` (`RETRY_AFTER_SECONDS`) for an outage (`503`) or a limit (`429`); the retry owner stays M01's, so an outage is the service's, which says when through `Retry-After`. `failure_response` adds the header, so `/v1/answer` and `/v1/answer/stream` agree.
- **After the headers.** One terminal `error` event. An outage or a limit is the caller's to retry, because the service never retries a stream once text is out; everything else is nobody's. A validation failure is reported as `internal_error`, because the request was already accepted. The reader accepts an `error` event only with this guidance.
- **The database.** `classify_failure` now calls a database error an outage (`dependency`) when the pool had no free connection in time, SQLAlchemy marked the connection lost, or an `OperationalError` has no SQLSTATE (the server never answered) or one of classes `08`, `40`, `53`, `57`. Every other database error stays `internal`.
- **Partial text and the record.** "answer stream ended" now holds `phase`, `audit_outcome`, and `partial_text`: `kept` for a completed Answer, `replaced` for an abstention or an error after text, `abandoned` when the client left mid-text, `none` when nothing was streamed. Text is counted before it is handed over, and the terminal outcome is recorded before the terminal event is handed over.
- **When the phase changes.** `EventStreamResponse` calls `AnswerStream.headers_sent` as it hands over the start of the response; the stream also marks it when its first event is asked for.

## Verification

- `uv run python -c "import knowledge_service.answering.stream_failures"`: failed with `ModuleNotFoundError` before, imports now.
- `uv run pytest tests/test_stream_failures.py -q`: 42 passed. `pytest -m integration tests/test_stream_failures_integration.py`: 2 passed (a refused connection and a real statement timeout, both `503` with `Retry-After: 5`). The M01, M06-T08, M06-T09, and app tests: 153 passed.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2297 passed (+42); `make test-integration` 378 passed (+2); `make docs-check` passed. The first `make test-unit` run, under heavy machine load (52 s instead of about 20 s), failed `test_support.py::test_splitting_a_long_run_of_dots_takes_linear_time`, an M06-T06 wall-clock limit of 0.5 s; it passed alone and the full rerun passed. It is unrelated to this change but is a flaky timing test.
- Deliberate break: without the database rule in `classify_failure`, 2 of 2 integration tests failed (both became `500` without `Retry-After`), and 9 of 42 unit tests that inject a database error; restored, all pass.
- Known limits: the reader's stricter retry check kept protocol version `1`, because no client has shipped; `Retry-After` is a fixed 5 seconds; the audit outcome lives in the log record, not a stored Audit Event, until M07 gives a verified actor; a `503` for a missing streamer has no `Retry-After`.

## Review

An independent adversarial review injected failures and client departures at every point of a stream. Search failures gave the right status with `Retry-After` only for `503` and `429`; mid-stream failures ended in one `error` event; validation after the headers was internal; no stream lacked a terminal event; a marker planted in SQL, parameters, and driver messages never reached a response, a header, or a record. It found five problems, each fixed with a regression test that failed on the first version:

- Every `OperationalError` was an outage, including errors a retry cannot fix, such as `ProgramLimitExceeded`. Now only the SQLSTATE classes above, or no SQLSTATE, are (`test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after`).
- A client that left while the headers were being handed over was recorded as `before_headers`. Now the endpoint marks them as it hands them over (`test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers`).
- End text was counted after it was handed over, so a client that left with it was recorded as holding nothing (`test_text_sent_at_the_end_is_counted_when_the_client_leaves`).
- A client that read the terminal event and left was recorded as cancelled (`test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled`).
- `/v1/answer` gave `503` and `429` without `Retry-After` (`test_the_answer_endpoint_gives_the_same_retry_guidance`).

Documented, not fixed: the stricter reader is a protocol change without a new version.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The status is fixed once the headers are out.** The `200` and maybe some text are already with the client, so a later failure can only end the stream with an `error` event. The client must drop the streamed text and show the fixed message; the reader enforces it, because half an Answer without its checks is not an Answer.
- **Who retries.** Before the headers nothing was sent, so the service can say when to try again with `Retry-After`. After them the service never retries, because a second model call could write a different text into the same stream; telling the client "the service retries" would mean nobody does. So an outage or a limit after the headers is the caller's to retry, with a new request.
- **Validation after the headers is internal.** The request was accepted; anything that fails later, such as an input the support judge's embedding refuses, is the server's fault, and "fix your request" would send the client looking for a problem it does not have.
- **Only some database errors are outages.** A retry helps only when the database was unreachable, overloaded, shutting down, cancelled the statement, or hit a serialization conflict. A broken constraint or a value too large fails the same way every time, so it stays a `500` with no retry advice.
- **Recorded before it is handed over.** Handing over the last event can be the last thing that happens: a client that reads it and leaves stops the stream at once. Recording first makes the audit outcome describe what the client got.
- **Left for later.** A stored Audit Event per stream once M07 gives a verified actor, and a new protocol version for the next change a shipped client would notice.

**Falsified assumption.** I assumed every SQLAlchemy `OperationalError` meant the database was unavailable. The review shows psycopg also raises it for SQLSTATE classes `54` and `55`, such as `ProgramLimitExceeded`, which no retry fixes. So "OperationalError means outage" was wrong; the rule now reads the SQLSTATE.

**Break run.** Without the database rule: 2 of 2 integration tests failed, and 9 of 42 unit tests. Restored: all pass.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The endpoint no longer decides the status or whether to send `Retry-After`; the writer no longer decides the code, message, and retry of an `error` event; the reader no longer keeps its own list of what an error may say. None of them decides whether a validation failure is the caller's or the server's, or what becomes of the partial text.
2. The client would see text from the first call followed by text from the second, or a jump back. The policy would need a "restart" event telling the client to drop the text so far, a new protocol version, and a rule for the usage of both calls.
3. They all come back together and hit the slow database again: a thundering herd. I would add random jitter to `Retry-After`, or shed load earlier with a `429` while the pool is exhausted.
4. Moving the record update after the `yield` again, for example while merging the completion and usage writes, or batching events into one write. `test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled` and `test_text_sent_at_the_end_is_counted_when_the_client_leaves` catch it.
5. A failure while writing a Citation event, such as a Citation the writer refuses, combined with a client that leaves at the same moment. I expect one `error` event and an `internal_error` outcome if the client stays, and `cancelled` with `abandoned` text if it leaves.
