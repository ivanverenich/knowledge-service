# M06-T09 — Stream and cancel Answers evidence

## Invariant

`POST /v1/answer/stream` finds and judges Evidence before the first byte,
so its failures are plain HTTP errors. After it, the response is SSE in
the M06-T08 order: the answer's text as the model writes it, then, only
after the policy decides `answer`, the Citations; the billed usage before
the terminal event; and always exactly one terminal event, `completion`
or `error`. A client keeps text only when it is exactly the checked
Answer. One deadline covers the search, the model's stream, and the
checks. Each event is made only when the server asks for it. When the
client leaves, at any point, the work is cancelled, the model's stream is
closed within `CLOSE_SECONDS`, and the stream's record ends `cancelled`.
Records hold no Question, Evidence, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.streaming"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a client receives when the model has written half its Answer and the deadline runs out: the `text_delta` events of that half, then an `error` event with `dependency_unavailable` and no `usage`, because the billed reply never arrived; the client drops the half text and shows "A required service is unavailable", and the model's stream is closed.
- What happens to the model's stream when the browser tab is closed while the model is still thinking, and how a test can prove it: `EventStreamResponse` receives `http.disconnect`, cancels the writing once, the stream gets `CancelledError` where it waits for the model and closes the model's stream within `CLOSE_SECONDS`, and the record ends `cancelled`; a test plays a client that leaves after two events while a fake model is quiet, and checks that the model's stream was closed, the model never finished, no `completion` was sent, and the record says `cancelled`, under ASGI 2.3 and 2.4.
- How many tests fail if the endpoint uses Starlette's plain `StreamingResponse` instead of `EventStreamResponse`: 5 of 40 in `tests/test_streaming.py`: under ASGI 2.4 a client that leaves while the model is quiet is never noticed, a model that awaits while closing is cut short under both versions, and a client that stops reading holds the stream and its stuck close.

## Design

- **Changed files.** New `src/knowledge_service/answering/streaming.py`, `tests/test_streaming.py`, and `tests/test_openai_streaming.py`. `reply_result` is taken out of `generate_answer` in `answering/generation.py`; `OpenAIAnswerModel` gains `stream` and a shared `_request`; `app.py` gains `EventStreamResponse`, `_unless_client_leaves`, and `POST /v1/answer/stream`. The `answering` row of `docs/architecture.md` names `AnswerStreamer`.
- **Before the first byte.** `AnswerStreamer.open` finds Evidence through an `EvidenceSource`, judges it with `assess_evidence`, and fits the prompt with `allocate_context`, under the request's deadline. Its failures map to HTTP errors through `classify_failure`; a client that leaves meanwhile cancels it, and the endpoint answers 499.
- **After it.** An `AnswerStream` yields `metadata`, then a `text_delta` for each piece of the answer as the model writes it. When the `ModelReply` arrives, `reply_result` checks it; if the checked answer does not start with the streamed text, the stream abstains with `ANSWER_FAILED`. Otherwise the `AnswerFinisher` decides (`PolicyFinisher`: `check_support`, then `decide`). Only after an `answer` decision come the missing end of the text and the Citations; then the usage, then `completion`. Any failure, also an event the writer refuses, ends with the usage, if billed, and an `error`.
- **Text out of JSON.** `AnswerFieldReader` reads the `answer` string at the start of the model's JSON as it arrives and decodes its escapes, also across chunks; at any surprise, half a character, or a reply over `MAX_REPLY_CHARACTERS`, it stops and the end sends the checked text.
- **One deadline.** `timeout_seconds` starts with the request; each wait for the source, the model, and the finisher runs under the time left, and no `yield` is inside a timeout.
- **Leaving.** `EventStreamResponse` writes in one task and listens for `http.disconnect` in another; on a disconnect it cancels the writing once with asyncio, then closes the `AnswerStream`. The model's stream is closed in a `finally`, shielded and bounded by `CLOSE_SECONDS`; the OpenAI adapter closes the SDK stream the same way.
- **One record per stream.** "answer stream started" and "answer stream ended" hold the request ID, outcome (`completed`, `failed`, `cancelled`), failure type, duration, counts, and tokens; never text.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_streaming.py tests/test_openai_streaming.py -q` | 49 passed, 1 deselected |
| `make format-check` | 249 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 2170 passed |
| `make test-integration` | 342 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `knowledge_service.answering.streaming` failed with `ModuleNotFoundError`.
- **Deliberate break.** Starlette's plain `StreamingResponse` failed 5 of 40 tests in `tests/test_streaming.py`; restored, 40 passed. Details under Decision.
- **Not run live.** There is no `OPENAI_API_KEY`, so `test_openai_live_stream_answers_in_pieces` was skipped; the adapter is covered by stubs of the SDK stream.
- **Known limits.** Nothing calls `confirm_citations` yet, and no real `EvidenceSource` exists; both need the reader's identity (M07). The usage event holds the answering model's tokens, not a model judge's. A model whose own cleanup blocks after it is cancelled can outlast the deadline by that cleanup; the OpenAI adapter's close is bounded. During the adversarial review, one full unit run had one failure that did not repeat in six later runs; a timing-sensitive test under load is the likely cause.

## Review

An adversarial review attacked the prototype with 4,000 random replies, disconnects at every stage under ASGI 2.3 and 2.4, and both stubbed and real HTTP streams. It found no way for a client to keep text that is not the checked Answer and no Citation sent before an abstention. It found seven problems, each fixed with a test that failed before the fix (15 tests in all): an event the writer refused after the decision escaped the stream, leaving no terminal event; a model whose close hung held the stream open; a client that left during the search did not stop it, and the stream left no record; a cancelled anyio scope cut the model's own cleanup short; a reply too long to be an Answer lost its usage; half a character in the raw JSON ended in an internal error instead of an abstention; and the stream record's repr held the text.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Search before the first byte, the model after it.** Once the first byte is sent, the status is 200 for good. Finding and judging Evidence can fail with "access denied" or a timeout, and those must stay a 403 or a 503 a client can act on. Only the model's part, which takes most of the time, streams.
- **Pulled, not pushed.** The stream is an async generator: it makes the next event only after the last one was written. A queue between the model and the client would let a slow or stalled client fill memory while the model keeps generating and billing; pulling makes a slow client slow the model's stream instead.
- **Text only if it is the checked Answer.** Text streams before the reply is checked, so the client may already show words. When the checked answer does not start with what was streamed, the stream abstains and the client drops the text, so nothing a user keeps was skipped by the checks of M06-T04 to M06-T07.
- **Listen for the disconnect, and cancel once.** Under ASGI 2.4 Starlette's response notices a disconnect only on its next write, so a quiet model would run on for nobody. Cancelling with an anyio scope cancels again at every wait and cuts the model's own cleanup short; one asyncio cancellation, then a shielded close bounded by `CLOSE_SECONDS`, lets the cleanup finish without letting it hang.
- **Left for later.** Rechecking Citations against the database needs the reader's identity (M07); a real `EvidenceSource` comes with it. M06-T13 maps each failure to its retry advice.
- **Deliberate break.** Using Starlette's plain `StreamingResponse` failed 5 of 40 tests in `tests/test_streaming.py`: a client leaving while the model is quiet under ASGI 2.4, a model that awaits while closing under 2.3 and 2.4, a stuck close, and a client that stops reading and leaves. The run took 13 seconds here and 72 in the reference run, because the stuck close waits for its test's timeout. Restored; 40 passed.
- **Falsified assumption.** I assumed that once the response cancels the stream, the model's stream is closed. The review shows that inside a cancelled anyio scope every await is cancelled again, so a provider's close that awaits even once was cut short and its record never written. So the close must be shielded, and the response must cancel only once.
- **Second falsified assumption.** I assumed disconnects matter only while events are being sent. The review shows a client that left during the search kept the search running for the whole deadline and left no record, because nothing listened before the response started. So the endpoint listens during `open` too, and closing a stream before its first event still ends its record.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What `AnswerStreamer` hides from the endpoint.** The endpoint never sees the Evidence, the prompt, the budget, the model's JSON, the escapes inside it, the check of the reply, the support judge, the policy, the order of events, the deadline arithmetic, or the usage. It gets an `AnswerStream` of bytes, or an error to map before the first byte.
2. **Reading the whole stream into a list first.** The user would wait for the whole Answer again, which is what streaming removes. Memory would hold every reply in full, and a client that left would no longer stop anything until the model finished, because nothing would be waiting on a write that can fail.
3. **Easiest to break in M06-T13.** "Always exactly one terminal event": adding retry advice to `error` events in a new `except` path could yield an `error` after a `completion`, or none at all. `test_an_event_the_writer_refuses_still_ends_in_an_error` and the reader's `AFTER_TERMINAL` and `TRUNCATED` checks would catch it.
4. **Streamed text that is not the checked Answer.** With a real model it would take a reply whose JSON the reader read differently than `json.loads`, such as an answer field that is not first, or a reply with a broken escape. The user sees words appear, then the fixed message "I could not produce an answer right now." in their place.
5. **Proving the provider stops on a running server.** Open the stream with `curl -N`, stop it with Ctrl+C after the first events, then look for "answer stream ended" with `outcome` `cancelled` and "answer model call finished" with `cancelled`, both within a second, and check that the provider's usage for that request shows only the output tokens generated before the stop.
