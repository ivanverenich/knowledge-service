# M06-T14 — Answering gate evidence

## Invariant

A streamed Answer uses only Evidence the reader may see, under opaque IDs
of this request; a Citation the model writes is kept only when it is one of
those IDs, and is rebuilt from its Evidence on the server. A reply with a
fake Citation is never shown: the client gets an abstention, no `citation`
event, and the stream's record says `answer_failed`, `invalid`, and
`invalid_citation`. The writer writes protocol version `2`, with at most
one `checking` event once the model's whole reply has arrived and before
any Citation, usage, or terminal event. The reader reads versions `1` and
`2`, and a client rebuilds the same Answer from a stream with or without
`checking`. No record holds Answer text or a Citation value.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.answering.events import EventName; EventName('checking')"`
fails with `ValueError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What the client sees, event by event, when the model cites an Evidence ID that is not in its prompt: `metadata`, the answer's text as `text_delta` events while the model writes it, then `usage` and a `completion` with an abstention `answer_failed`; no `citation` event, and the client replaces the streamed text with the fixed message.
- What the stream's record says about that stream before this change, and what an operator could not tell from it: `audit_outcome` `abstained` and `partial_text` `replaced`, with no reason or defect, so a fake Citation looks the same as an unsupported claim or a model refusal.
- What a version `1` reader does with a stream that holds a `checking` event, and why that calls for a new version: it refuses the whole stream as malformed at the unknown event, after showing part of the text, without saying the client is too old; a new version makes it refuse at the first event, clearly.
- How many gate tests fail if the reader accepts a second `checking`: two, the `twice` and `twice-around-text` cases of `test_checking_out_of_place_is_refused`.

## Evidence flow

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Retrieval with the permission check.** `AnswerStreamer.open` asks its `EvidenceSource` for Candidates; the hybrid search puts `eligible_chunks_predicate` (the reader's Authorization Context) into the same SQL as the ranking, so a Chunk the reader may not see never leaves PostgreSQL.
- **Evidence under opaque IDs.** `build_evidence` gives each eligible Candidate a random Evidence ID that lives for this request only. The model never sees a Chunk, Document, or tenant ID, so it cannot cite one that means anything outside this request.
- **Judging the Evidence before any model call.** `assess_evidence` reads the ranking signals; with no Evidence, or Evidence too weak, the stream abstains without calling the model, which saves cost and stops an Answer built on nothing.
- **The context budget and the prompt.** `allocate_context` keeps the best Evidence that fits the token budget, whole; `render_prompt` builds the instructions and numbered Evidence and the list of IDs the reply may cite. If nothing fits, the stream abstains.
- **The model's stream.** The model writes structured JSON; `AnswerFieldReader` reads only the `answer` field's characters as they come and sends them as `text_delta`; at the first surprise it stops, and the checked text is sent at the end instead. Nothing else the model writes is streamed live.
- **Checking the reply and its Citations.** Once the whole reply is in, the stream sends `checking`. `checked_answer` parses the reply and `resolve_citations` keeps a Citation only if it is one of this request's Evidence IDs; anything else makes the reply `invalid` with a defect such as `invalid_citation`.
- **The support check and the decision.** `check_support` judges every claim against the cited Evidence; the `AbstentionPolicy` decides `answer` or `abstain` with a reason. The streamed text must equal the checked Answer, or the policy abstains.
- **What the client receives.** For an Answer: any text not yet streamed, one `citation` event per Citation rebuilt on the server from its Evidence, `usage`, and `completion`. For an abstention: `usage` and a `completion` with the reason, which replaces the streamed text. A failure before the headers is an HTTP error; after them, one `error` event.

## Diagram

The answering sequence diagram in `docs/architecture.md` was redrawn to follow `AnswerStreamer`: retrieval with ACL predicates, `build_evidence` and `assess_evidence`, `allocate_context` and `render_prompt`, `200` and `metadata`, the early abstention with no model call, the model loop with `text_delta`, `checking`, `resolve_citations`, `check_support`, and the decision, the two endings, and the note on failures before and after the headers. The `answering` row of the package table mentions `checking` and the record's `reason`, `reply`, and `defect`.

## Fake Citation

Drafted by Claude at the learner's request from the Step 3 run; the learner should rewrite it in their own words.

Before the change:

```text
events:  ['metadata', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'usage', 'completion']
client:  abstained answer_failed ''
record:  {'audit_outcome': 'abstained', 'partial_text': 'replaced', 'citations': 0, 'reason': '(missing)', 'reply': '(missing)', 'defect': '(missing)'}
```

After the change:

```text
events:  ['metadata', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'text_delta', 'checking', 'usage', 'completion']
client:  abstained answer_failed ''
record:  {'audit_outcome': 'abstained', 'partial_text': 'replaced', 'citations': 0, 'reason': 'answer_failed', 'reply': 'invalid', 'defect': 'invalid_citation'}
```

The chain:

1. The model wrote a well-formed Evidence ID that was not in its prompt.
2. `resolve_citations`, called by `checked_answer`, found it among none of this request's Evidence and raised the defect `invalid_citation`.
3. `checked_answer` turned the defect into a reply with outcome `invalid`, keeping the usage.
4. The `AbstentionPolicy` abstains with `answer_failed` for any reply that is not `answered`.
5. Citation events are written only after a decision to answer, from Citations rebuilt on the server, so no `citation` event was sent.
6. The client got the abstention, which replaces the seven streamed text pieces with the fixed message.
7. The record held only `abstained` and `replaced`: an unsupported claim, a refusal, and changed text end the same way, so an operator could not tell them apart.

Repair: the record now holds the abstention `reason`, the reply's outcome (`reply`), and its `defect`, by name only.

## New event

Drafted by Claude at the learner's request; the learner should rewrite their own design from before they saw the reference, and how it differed.

The reference: `checking` with empty, strict data; allowed after `metadata` or text, at most once, refused after a Citation, usage, or a terminal event, and text may follow it; `AnswerEventWriter.checking()`; the same `_Rules` for writer and reader; protocol version `2`, with the reader still reading `1` without `checking` and with the M01 retry owners. A first design that keeps version `1` and only adds the event would make every older reader refuse new streams mid-text; that is the first review finding below.

## Design

- **The event.** `EventName.CHECKING` with `CheckingData`, a strict model with no fields, so `{}` is the only valid data. `_AFTER[CHECKING]` allows it after `metadata` or `text_delta` and lets text follow it. `_Rules.checking` refuses a second one (`OUT_OF_ORDER`); anything with data is `MALFORMED`.
- **Version `2`.** `EVENT_PROTOCOL_VERSION` is `"2"`; `MetadataData.version` accepts `"1"` and `"2"`; `_Rules.version` remembers which. In a version `1` stream `checking` is `MALFORMED`, and an `error` event is checked against `_FIRST_ERRORS`, the retry owners from M01's `policy_for`; in version `2`, against the M06-T13 after-headers guidance. A version `1` reader refuses a version `2` stream at its first event.
- **Where the streamer sends it.** In `AnswerStreamer._events`, right after the model's whole reply and its usage arrive, before `_finished` runs the citation, support, and policy checks. It is never sent when no reply arrived: no Evidence, weak Evidence, no room, or a model failure.
- **The record.** "answer stream ended" gains `reason` (the abstention reason, recorded only with its completion, before it is handed over), `reply` (`answered`, `refused`, `invalid`), and `defect` (such as `invalid_citation`). All are names; never text or a Citation value.
- **One sentence in `stream_failures.py`** now says version `2` events carry the after-headers guidance and the reader still reads a version `1` error with the M01 retry owner.

## Verification

- `EventName('checking')`: `ValueError` before, accepted now.
- `uv run pytest tests/test_answering_gate.py -q`: 23 passed. The updated event, streaming, and failure tests pass.
- Step 3 run: before and after as under `## Fake Citation`.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2320 passed (+23); `make test-integration` 378 passed (unchanged); `make docs-check` passed.
- Deliberate break: without the "only once" rule in `_Rules._take`, `tests/test_answering_gate.py` gave 2 failed, 21 passed (`test_checking_out_of_place_is_refused[twice]` and `[twice-around-text]`); restored, 23 passed.
- Known limits: the reader accepts a few orders the server never writes, such as `checking` right before an abstention with no usage, which change nothing a client rebuilds; `confirm_citations` is not yet in the streamed path (M07); the record names the defect, not which Citation was fake, on purpose; no live OpenAI run.

## Review

An independent adversarial review found `checking` sent exactly when a reply arrived, revealing nothing, and every fake Citation stopped with no marker in a record (invented, other-request, malformed, a real Chunk ID, an upper-cased ID). It found four problems, all fixed with tests that failed before the fix:

- A version `1` reader refused new streams while they still said version `1`. Now the writer says version `2`, and the reader reads both (`test_the_writer_says_version_2_and_the_reader_still_reads_version_1`, `test_a_version_1_error_keeps_the_retry_owner_it_had_then`, `test_a_version_1_stream_with_checking_is_refused`).
- The gate's property test failed on invisible text, because the writer refused that Answer before the comparison. Now it compares what the writer or reader made of each stream (`@example(deltas=[" "], ...)` on `test_checking_changes_nothing_a_client_rebuilds`).
- A refused reply and changed text looked the same in the record. Now the record says how the reply was judged (`test_the_record_tells_apart_why_the_answer_failed`).
- A failed stream kept a stale reason from a refused decision. Now a reason is recorded only with its completion (`test_a_stream_that_fails_keeps_no_reason_of_a_refused_decision`).

Documented, not fixed: the reader accepts a few orders the server never writes.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **A fake Citation never reaches the client.** Citation events are built only from this request's Evidence after the policy decides to answer, and any reference outside that Evidence makes the reply `invalid`, so there is nothing to send. The record names the defect but never the value, because the value came from the model and may carry text or planted content.
- **`checking` carries no data.** Anything it carried, such as the number of claims, would describe the reply before it was checked, and would differ between a good reply and a fake one.
- **Before the end of the text, never after a Citation.** Text the model did not stream live is sent only after the decision, so text may follow `checking`; a Citation, usage, or terminal event comes only after the checks, so `checking` after them would be a lie.
- **Version `2`.** An older reader cannot read the new event, and refusing mid-text is worse than refusing at the first event. The reader keeps version `1`: no `checking`, and the M01 retry owners for its errors.
- **Left for later.** `confirm_citations` in the streamed path with the real reader identity (M07).

**Falsified assumption.** I assumed a new event a client can ignore keeps the protocol compatible, so the version could stay `1`. The review shows the reader refuses any event it does not know, so every version `1` reader would refuse every new stream as malformed. So "an ignorable event is compatible" was wrong for this reader; the version is now `2`.

**Break run.** Without the "only once" rule: 2 failed, 21 passed. Restored: all pass.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The prompt lists only this request's IDs; the model's JSON is parsed by `checked_answer`; `resolve_citations` maps each reference to Evidence; the policy abstains for an `invalid` reply; and Citation events are written only from rebuilt Citations after a decision to answer. Removing `resolve_citations` alone would let it through, since nothing after it looks at the raw reference.
2. Counts by defect over time and by model version, the prompt's size and Evidence count, and whether the Evidence IDs in the prompt changed format; then replay the same Question offline against the evaluation set, where text may be inspected.
3. It would reveal how many claims the reply makes before it is checked, to anyone reading the stream, and it would differ between a good and a fake reply, so the event would no longer be the same for both.
4. Batching or merging events, such as sending `checking` with the first Citation, or dropping text that follows `checking`. `test_checking_changes_nothing_a_client_rebuilds` and `test_checking_comes_once_after_the_live_text_and_before_the_decision` catch it.
5. Version `3`, because a version `2` reader refuses an unknown event. The reader would keep reading `1` and `2` with their own rules: no `checking` and the M01 retry owners for `1`, `checking` and the after-headers guidance for `2`.
