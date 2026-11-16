# M06-T08 — SSE event contracts evidence

## Invariant

A streamed Answer is `metadata text_delta* citation* usage?` followed by
exactly one terminal event, `completion` or `error`, with event IDs
counting up from 0 by one. Nothing follows the terminal event, and a
stream without one is cut off. `AnswerEventWriter` refuses any event the
rules forbid and leaves the stream unchanged; `AnswerEventReader` refuses
a stream out of order, with a lost or repeated event, cut off, malformed,
or over its limits, with a typed `InvalidEventStream`. A `completion`
with `answer` needs visible text, a Citation, and the usage; Citations
never precede an abstention; abstention and error messages are the fixed
messages of their reason or code. Whatever the writer sends, the reader
accepts and rebuilds into the same Answer, however the bytes are split.
Errors and reprs hold no text from the stream.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.events"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a client shows when the server streamed text and then sent `completion` with `abstain`: only the fixed message of the abstention's reason, such as "I could not verify an answer against the documents.", in English or Ukrainian; the streamed text and any Citations are dropped, and the outcome is `ABSTAINED`.
- What the reader says when the connection drops after the usage event, before the terminal event: `close` raises `InvalidEventStream` with `TRUNCATED`, so the client never shows the text as a complete Answer.
- How many tests fail if the reader stops refusing Citations before an abstention, and why that rule matters: 1 of 81, `test_citations_never_precede_an_abstention`; the fuzz test still passes because the writer and the reader share the rules. The rule matters because a Citation shows a Document's title and link, and an abstention may mean the Answer was withdrawn because a cited Document became hidden.

## Design

- **Changed files.** New `src/knowledge_service/answering/events.py` and `tests/test_answer_events.py`. The `answering` row of `docs/architecture.md` names `AnswerEventWriter` and `AnswerEventReader`. No existing code changes.
- **Six events.** `metadata` (protocol version `1` and the request ID), `text_delta` (the next non-empty piece of text), `citation` (a `CitationPayload`), `usage` (input, output, and total tokens), `completion` (the decision, its reason, its fixed message, and the policy version), and `error` (a public code, its fixed message, and who may retry). Each is a strict JSON object on one `data:` line under an `id:` and an `event:` line.
- **Order and terminal events.** `metadata text_delta* citation* usage?`, then exactly one `completion` or `error`. IDs count up from 0 by one. Nothing follows the terminal event; a stream without one is `TRUNCATED`.
- **What the client keeps.** `answer` keeps the joined text and the Citations and needs visible text, a Citation, and the usage. `abstain` keeps only the fixed message of its reason; `error` only the fixed message of its code. Usage is kept in every case.
- **Citations only after the decision to answer.** An abstention after a Citation is refused, so a server that cites before deciding fails its tests.
- **Fixed messages.** An abstention message must be its reason's message in English or Ukrainian; an error must have a known public code with its message and retry owner. A cancelled request has no error event.
- **One rule set, one spelling.** The writer and the reader share `_Rules`; a refused event changes nothing. The reader accepts only the writer's exact bytes: `\n` line endings, no byte-order mark, three lines per event, comments starting with `:` only between events, Evidence IDs and Document UUIDs as the server writes them.
- **Limits.** Text at most `MAX_ANSWER_CHARACTERS`, at most `MAX_EVIDENCE` Citations, one event at most `MAX_EVENT_CHARACTERS` on both sides, token counts at most 2**53 - 1. A line still arriving is kept in pieces, so reading costs time in proportion to the bytes.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_answer_events.py -q` | 81 passed |
| `make format-check` | 246 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 2121 passed |
| `make test-integration` | 342 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 2, importing `knowledge_service.answering.events` failed with `ModuleNotFoundError`.
- **Deliberate break.** Deleting the check that refuses Citations before an abstention failed 1 of 81 tests; restored, 81 passed. Details under Decision.
- **Known limits.** Nothing sends these events until M06-T09. The reader rebuilds an Answer only from a whole stream; resuming after a reconnect is later work. A refusal's traceback still holds the stream's bytes in local variables, so a logger that records locals must not see it. A stray carriage return after the terminal event is `MALFORMED` or `AFTER_TERMINAL` depending on where the bytes were cut. A Citation followed by an `error` is accepted, so withholding titles of a withdrawn Answer depends on M06-T09 deciding before it cites.

## Review

An adversarial review attacked the prototype with 3,000 random writer call sequences and 60,000 corrupted streams fed in 1-byte, 7-byte, and whole chunks. It found seven problems, each fixed with a test that failed before the fix (16 tests in all): wrongly typed Citation or decision fields, or a token count too long for JSON, crashed the writer with an untyped error; the docstring promised a withdrawn Answer's titles never reach the client, which a Citation followed by an error breaks; the reader limited lines, not events; the reader accepted made-up Evidence and Document IDs; the reader accepted the same JSON spelled differently; token counts above 2**53 were accepted; and the same broken bytes gave different defects depending on the split. It confirmed that every refused writer call leaves the stream unchanged, that the reader accepts everything the writer sends with the same result, that nothing but `InvalidEventStream` escapes on hostile input, that no accepted stream keeps text on an abstention or error or loses the usage, and that a planted marker never reaches an error message, a chained error, or a repr.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **A fixed order, and the last event decides.** A client shows text as it arrives, but only the terminal event says what the text is worth. `completion` with `answer` keeps the text and Citations; `abstain` and `error` replace them with a fixed message. A stream that stops without a terminal event is `TRUNCATED`, so a dropped connection never looks like a finished Answer.
- **Citations only after the decision to answer.** A Citation shows a Document's title and link. If the Answer is then withdrawn because a cited Document became hidden, the client must not have seen that title. The reader refuses an abstention after a Citation, so a server that sends Citations before deciding fails its tests.
- **Fixed messages, not free text.** An abstention carries only the fixed message of its reason, and an error only the fixed message of its public code. Neither a server bug nor a proxy can slip Document text into what the client shows instead of an Answer.
- **One rule set, one spelling.** The writer and the reader share `_Rules`, so the server cannot send what a client refuses. The reader accepts only the writer's exact bytes, so one event has one spelling.
- **Left for M06-T09.** Sending these events over HTTP, cancelling when the client disconnects, deadlines, keep-alive comments, and resuming after a reconnect.
- **Deliberate break.** Deleting the check that refuses Citations before an abstention failed 1 of 81 tests, `test_citations_never_precede_an_abstention`. The fuzz test still passed, because the writer and the reader lost the rule together and stayed in step; only a test that names the rule catches its loss. Restored; 81 passed.
- **Falsified assumption.** I assumed the rule "an abstention after a Citation is refused" means a withdrawn Answer's titles never reach the client. The review shows a Citation followed by an `error` is accepted, and `feed` hands the Citation to the client as it arrives. So the contract can only expose a server that cites before deciding; the guarantee itself needs M06-T09 to call `decide` before it sends any Citation.
- **Second falsified assumption.** I assumed a strict model per event was enough to keep the reader as strict as the writer. The review found the reader accepted the same JSON with spaces or escaped letters, made-up Evidence IDs, and an event larger than the writer allows when each line fits. So the reader now compares each event with the writer's own bytes and counts the whole event.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What the writer hides from M06-T09.** The endpoint never numbers events, formats SSE lines, escapes JSON, tracks which event may come next, checks that an answer has text, a Citation, and usage, picks an abstention or error message, or counts limits. It calls one method per step and sends the bytes it gets back; a refused call tells it to end with `error` instead.
2. **Sending each Citation as the model names it.** It breaks "Citations only after the decision to answer". If the support check or `confirm_citations` then withdraws the Answer, the user has already seen the title and link of a Document that may now be hidden. The reader refuses a following abstention, so `test_citations_never_precede_an_abstention` shows the shape of the bug, but the title is already on screen.
3. **A dropped event.** The next event's ID is one too high, so the reader refuses the stream with `GAP`. Without the check, a lost `text_delta` would silently cut words out of the Answer, and a lost `citation` would drop a source, while the client still showed the result as complete.
4. **Why fixed messages.** A free-text message is a channel around every check: a server bug could put the answer text, a hidden Document's title, or an internal error with a path or SQL into it, and the client would show it as the system's own words. With fixed texts, the worst a broken server can do is pick the wrong reason.
5. **The next test.** A stream that sends `metadata` twice after some text, or a `citation` whose `excerpt` differs from the server's Evidence. I expect `OUT_OF_ORDER` for the first; the second would be accepted today, because the reader has no Evidence to compare with, which is a limit `read_citations` could close where the Evidence is known.
