# M07-T11 — Eliminate existence leaks evidence

## Invariant

To a reader, a Document they may not see is indistinguishable from one that
does not exist: the answer stream's bytes and headers, every log record, and
the model's prompt are the same with or without another User's best-matching
Document, and a forbidden Chunk, Evidence, or Conversation ID gives the same
result and log as a missing one. Vector search settles the reader's Chunks
before it ranks any, so its work does not grow with hidden Chunks near the
question. Lexical search's timing gap is recorded, with numbers, as a known
limit until it serves a reader.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_existence_leaks_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_existence_leaks_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Whether alice's answer stream, the logs, or the prompt change when bob's exact-match Document is added: none of them changes; the status, headers, body bytes, every log record, and the model's prompt are equal once UUIDs and Evidence IDs are masked, whether alice has a Document of her own (answered) or not (abstains).
- What `confirm_citations` returns for a Citation of bob's real Chunk and for one of an invented Chunk: the same withdrawn `AnswerResult`, reason, defect, and usage included, with the same log records.
- How long a vector search takes for a reader who sees 20,000 Documents, with and without 5,000 hidden exact matches, before and after the change: before, about 24 ms without and 34 ms with them; after, about 50 ms in both cases, the difference within noise.
- Which tests fail if `MATERIALIZED` is removed from the vector query, and which keep passing: only `test_the_reader_s_chunks_are_settled_before_any_ranking` fails (1 failed, 34 passed); the security contract and the black-box tests keep passing (186), because the results do not change, only the work.

## Design

- **Black-box comparisons.** `tests/test_existence_leaks_integration.py` observes everything outside the server sees twice, without and with another User's Document: the HTTP status, headers, and body of `/v1/answer/stream`, the prompt the model is shown, and every log record from every logger with all its `extra` fields. Only values that differ on every request anyway are masked: UUIDs, opaque Evidence IDs, and `duration_ms`. Results are compared whole, so a different `reason`, `defect`, or `usage` is a difference too.
- **Forbidden versus missing IDs.** A Citation and an Evidence item pointing at bob's real Chunk give the same result and logs as ones pointing at a Chunk that never existed (`confirm_citations`, `evidence_still_visible`). bob's Conversation, alice's own deleted one, and an invented ID give `(None, False, [])` from `load_history` and `delete_conversation`, with no log record.
- **The materialized step.** `vector_search_statement` first selects the Chunks that are available, granted to the reader, and match every filter in a `visible` CTE marked `MATERIALIZED`, which PostgreSQL computes on its own and never merges into the ranking; the outer query then joins `chunk_embeddings` to `visible` and ranks only those by distance. The current-space check and the NaN check stay outside. `test_the_reader_s_chunks_are_settled_before_any_ranking` reads the compiled SQL and requires the grants inside the CTE and the distance operator outside it.
- **What still leaks.** Lexical search still applies the permission check after its word index, so its time grows with hidden matches; sparse search scores hidden Chunks in Python. Both are recorded in the threat model, not fixed.

## Verification

- Expected failing check: before the change, `uv run pytest -m integration tests/test_existence_leaks_integration.py -q` failed with `ERROR: file or directory not found: tests/test_existence_leaks_integration.py`.
- After Step 2, `tests/test_vector_search_integration.py`: 43 passed. After Step 3, `tests/test_vector_search.py`: 35 passed. After Step 4, `tests/test_existence_leaks_integration.py`: 5 passed.
- Timing, measured by the reviewer's harness (median vector search, control versus 5,000 hidden exact-match Documents): a reader who sees 20,000 Documents, before 24 ms versus 34 ms, after about 50 ms versus 50 to 55 ms; a reader who sees few Documents, before 2.3 ms versus 2.4 ms, after 3.3 ms versus 3.3 to 3.6 ms.
- Deliberate break: without `.prefix_with("MATERIALIZED")`, `tests/test_vector_search.py` gave 1 failed, 34 passed (`test_the_reader_s_chunks_are_settled_before_any_ranking`), while the security contract and the black-box tests gave 186 passed. Restored: 35 passed. The prediction was right.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2785 passed (+1); `make test-integration` 641 passed (+5); `make docs-check` passed.
- Known limits: lexical and sparse search time still depends on hidden matches; vector search is about twice as slow for a reader who sees very many Documents; `rate_message` and `load_message_feedback` need an owner check before any route exposes them.

## Review

An independent adversarial review compared stream bytes, prompts, logs, `truncated`, eligible counts, hybrid diagnostics, error paths, and forbidden, deleted, and missing IDs, and measured timing with up to 20,000 visible and 5,000 hidden Documents, with query plans. No byte, log, prompt, or ID leak was found. It found:

- The vector timing leak: hidden Chunks nearer than the reader's results cost a permission check each, about 10 ms per 5,000. Fixed with the materialized step and pinned by the structural test, which fails on the old statement.
- A first work-count test (table statistics and query plans) passed on the old code too, because with a small corpus PostgreSQL already started from the grants. Replaced with the structural SQL test.
- Four of six planted leaks survived the first tests: a count in another log field, a sleep, a different `reason`, and a title in another logger. The comparisons now cover every logger, every extra field, and whole results; the three that are not about timing are now caught, and the sleep is only caught as structure.
- The lexical timing leak, about 5 to 28 ms per 5,000 hidden matches, and that a hybrid branch deadline would turn it into a definite timed-out branch: recorded for M09.
- `rate_message` and `load_message_feedback` read by Message ID with no owner check: not reachable today, recorded as a known gap.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Compare everything.** A comparison of chosen fields only finds the leaks someone thought of; the planted leaks in another field and another logger proved it. Comparing every observable value, masking only what is random on every request, finds the ones nobody thought of.
- **Decide who may read before ranking.** A query's results can be exactly right while its work, and so its time, depends on rows the reader may not see. Settling the reader's Chunks first makes the work depend only on what the reader may see. SQL leaves the order to the planner, so `MATERIALIZED` is what forces it.
- **Accept the cost for large readers.** A reader who sees 20,000 Documents waits about 50 ms instead of 24 ms; a reader who sees few waits about the same. A reader who sees almost everything has little left to learn, and the corpus has no vector index yet, so the price is small now; it must be revisited when an index is added.
- **Lexical search waits.** The same fix would stop it from using its word index for every reader, and lexical search reaches no reader yet. The threat model records the numbers and the deadline: fixed or bounded before M09 serves it.

**Falsified assumption.** I assumed that forcing the permission check first would cost vector search almost nothing. The measurement shows that a reader who sees 20,000 Documents now waits about 50 ms instead of 24 ms, twice as long. So "filtering first is always cheaper" is wrong: it pays off only when the filter removes most rows, and the planner had chosen the other order because it was faster for large readers.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The time the answer takes, and the log records an operator or a leaked log could show; the error a forbidden ID gives compared with a missing one, and what the model is shown. This lesson checks the logs, the prompt, and forbidden versus missing IDs with black-box tests, and the time of vector search structurally.
2. A test of results compares which rows come back; removing `MATERIALIZED` changes only how much work the database does before returning the same rows. A test that checks the shape of the work catches it: here, the compiled SQL must decide the reader's Chunks before any distance is computed.
3. Readers who see very many Documents pay it; everyone who may not see some Documents benefits, because the time no longer tells them how many hidden ones match. With millions of Chunks, ranking every visible Chunk becomes too slow; I would add a vector index with the permission filter inside the index search, or partition the index by access, and measure the gap again.
4. That the Conversation exists and belongs to someone else. It should answer 404 Not Found for both, with the same body and log.
5. Alice would ask about the reorganisation and about an unrelated topic many times and compare the median times; the reorganisation questions would be slower because the word index finds bob's 5,000 Documents and each is checked and dropped. The fix would need the same order there: settle alice's Chunks first and match words only among them, or bound the work so that it does not grow with hidden matches.
