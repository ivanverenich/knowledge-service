# M04-T03 — Embedding batcher evidence

Completed: 2026-10-23

## Invariant

For any texts, the plan holds every text exactly once, in order, in the
fewest batches that fit the model's limits, and the same input always gives
the same plan and keys. A run never has more than `concurrency` requests in
flight, ends every batch with exactly one outcome in plan order, attaches each
vector to the position of its own text, and never sends a batch whose key is
in `completed`. Outcomes carry error class names, never text.

## Expected failing check

Written before `embedding_batches.py` exists.

**What fails first.** Once `tests/test_embedding_batches.py` exists but
`src/knowledge_service/embedding_batches.py` does not, pytest stops while
collecting, before any test runs: `ModuleNotFoundError: No module named
'knowledge_service.embedding_batches'`, in that one test file, reported as
"1 error during collection". Nothing else imports the module, so no other test
file is affected. A trial run against the prototype with the module removed
showed exactly this.

**Predictions,** with limits of 3 inputs, 4 tokens per input, and 6 tokens
per batch, and one token per word:

- **`a b c d "e e e e" "f f f" g`** becomes three batches:
  - `(a, b, c)` with 3 tokens, cut because it holds 3 texts;
  - `(d, "e e e e")` with 5 tokens, cut because `"f f f"` would make 8;
  - `("f f f", g)` with 4 tokens.

  Their starts are 0, 3, and 5.
- **A text of 5 words** is over the 4-token limit for one input, so no batch
  can hold it. `plan_batches` raises `EmbeddingInputRejected` with reason
  `input_too_long` and the text's position in the whole list, and nothing is
  sent.
- **Concurrency 1, second batch rate limited** (ten one-word texts, so four
  batches). The outcomes are `SUCCEEDED`, `FAILED` (`EmbeddingRateLimited`),
  `DEFERRED`, `DEFERRED`. Only two requests are sent: the rate limit stops
  new batches from starting.
- **Concurrency 1, second batch times out** (seven texts, three batches).
  The outcomes are `SUCCEEDED`, `FAILED` (`EmbeddingTimeout`), `SUCCEEDED`.
  A timeout fails only its own batch, so the run goes on, and the texts of
  the first and third batches get vectors.
- **A second run given the first run's succeeded keys.** The first batch is
  `SKIPPED` and not sent. The failed batch and the deferred ones are sent and
  succeed, so the two runs together give a vector to every text.

## Design

Changed files:

- New `src/knowledge_service/embedding_batches.py`: `PlannedBatch`,
  `BatchStatus`, `BatchOutcome`, `plan_batches`, `embed_in_batches`, and
  `vectors_by_position`.
- New `tests/test_embedding_batches.py`: 24 tests, counting parametrized
  cases, two of them Hypothesis property tests.

How it works:

| Part | Behaviour |
|---|---|
| `plan_batches(texts, model)` | Counts each text once with the model's `count_tokens`, refusing an empty, unencodable, uncountable, or over-long text by its position in the whole list. Then it fills each batch greedily until the next text would pass `max_inputs` or `max_batch_tokens`. |
| Batch key | SHA-256 of the JSON of the model identity, the batch's start, and its texts. |
| `embed_in_batches` | Plans, marks batches whose key is in `completed` as `SKIPPED`, and starts `min(concurrency, waiting)` workers. Each worker takes the next batch from one shared iterator, embeds it, records the outcome, and repeats until nothing is left or the run is stopped. |
| Stopping | A rate limit, a refused request, or an unexpected error stops new batches from starting. Batches in flight finish, and the rest are `DEFERRED`. A timeout, an unavailable provider, or a malformed response fails only its own batch. |
| `vectors_by_position` | Maps each text's position in the input to its vector, for succeeded batches only, zipping texts and vectors with `strict=True`. |

Decisions:

- **Workers pull from one iterator.** Only `concurrency` tasks exist, and a
  worker starts a batch only after finishing the previous one, so no more
  than `concurrency` batches are ever in flight: that is the backpressure. In
  `asyncio`, `next(queue, None)` cannot be interrupted, so no batch is taken
  twice.
- **Every batch has exactly one outcome,** returned in plan order whatever
  order the requests finished in.
- **A failure is an outcome, not an exception.** `except Exception` records
  any error by its class name, so one bug cannot throw away the vectors of
  finished, paid-for batches. `CancelledError` is not an `Exception`, so
  cancellation still stops every batch in flight.
- **The model is not trusted.** A result with the wrong number of vectors,
  or from another model identity, fails its batch with
  `EmbeddingMalformedResponse`, instead of moving vectors onto other texts.
- **Keys include the start position,** so two identical batches at
  different positions never share a key, and resuming one never skips the
  other.
- **No text in `repr`.** `PlannedBatch.texts` is a `field(repr=False)`, so
  logging a batch or an outcome never shows document content.
- **One string is refused** as `texts` or as `completed` with `TypeError`,
  because a `str` is itself a sequence of strings.

**Adversarial review and fixes.** An independent review ran its own two
Hypothesis properties, not part of the committed tests, with 700 examples in
total, using a model that slept a random 0–2 ms per call. The examples covered concurrency 1–6, random failures of every
port error class, and random sets of `completed` keys. Across all of them:

- the number of calls in flight never passed `concurrency`;
- every outcome was in plan order;
- each vector matched its own text;
- no batch was sent twice, and no skipped batch was sent at all.

It also confirmed:

- **cancellation** leaves no task running;
- **keys** are the same in another process with another hash seed;
- **planning 100,000 texts** takes about 0.1 seconds.

It found four breaks, all fixed with a test that fails without the fix:

- **A bug in the model lost the whole run.** A `ValueError` from `embed`
  escaped as an `ExceptionGroup`, discarding the vectors of every finished
  batch, so a resume had nothing to skip and paid for them again.
- **A vector could land on the wrong text.** A model returning one vector
  too many filled the position of a text whose batch had failed; one too few
  left a silent gap; vectors from another model identity were accepted.
- **`completed="some-key"`** was read as a set of single characters, so
  nothing was skipped and the batch was paid for again.
- **The default `repr` of a batch printed its texts,** so logging a batch
  or an outcome would have logged document content.

**The closing spec review found one more gap, in the tests.** The fake
model first yielded exactly three times per call, so batches always finished
in the order they started. A batcher that handed results to positions in
finishing order therefore passed the no-reorder property. The fake now yields
1 to 8 times, from a hash of the batch's texts. That mutant fails the
property test, and a separate one-off check with every batch succeeding,
while the real module passes.

Before the review, a deliberate break showed a gap in the property test:
cutting a batch while it still had room (`<` instead of `<=` for the token
budget) passed every test. The property now also checks that each batch,
except the last, is cut only when the next text cannot join it.

When the learner's code was finished, seven deliberate breaks were run against
it, one at a time, and each was caught:

- starting a worker for every batch failed 5 tests;
- reversing the offsets in `vectors_by_position` failed 1;
- a strict token budget failed 1;
- not stopping on a rate limit failed 4;
- leaving `start` out of the key failed 1;
- catching only `EmbeddingError` failed 1;
- handing successful results out in finishing order failed 5, the
  no-reorder property among them; a variant that also handed out failures in
  finishing order failed only the property.

Each file was restored afterwards.

**Notes:**

- **Keys depend on the plan.** Changing the limits, or inserting a text near
  the start, changes every following key, so a resume sends everything
  again. That costs money but never skips a text.
- **A caller must record a key only after storing its vectors.** A key of a
  failed batch passed as `completed` would make its texts silently go
  without vectors. M04-T05 owns storing.
- **The whole plan is built up front,** but only `concurrency` tasks ever
  exist.
- **A model whose token count changes between planning and sending** can
  refuse a planned batch at send time. It ends `FAILED` with
  `EmbeddingInputRejected`, and retrying the same plan fails the same way.
- **The deadline applies per batch,** not to the whole run; only NaN is
  refused up front.

No logging was added. Outcomes record errors by class name only, and the
seeded text `the salary of Olena is high` is asserted absent from the `repr`
and `str` of a batch and of its outcome. No credential or provider name
appears in the module.

## Verification

Final run on 2026-10-23 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embedding_batches.py -q` | 24 passed |
| `make format-check lint typecheck` | 118 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 656 passed, 54 deselected |
| `make check` (without the database) | 657 passed, 50 skipped, 3 deselected; `embedding_batches.py` 100% covered |
| `make check` (with the database) | 707 passed, 3 deselected; total coverage 94% |
| `make docs-check` | passed |
| `git diff --check` | clean |

The expected failing check held: with `embedding_batches.py` removed, pytest
reported 1 error during collection, `ModuleNotFoundError` in
`tests/test_embedding_batches.py`, and nothing else. Each prediction held.
Along the way:

- The local variable in `_key` was named `playload`; it was renamed to
  `payload`. Behaviour was unchanged.
- `make format` reformatted `embedding_batches.py` (blank lines, a missing
  final newline). Trailing commas the learner added keep some calls split
  across lines, which Ruff accepts.

## Reflection

1. **What the batcher hides from its caller.** The caller passes texts, a
   model, and a concurrency. It never counts model tokens, decides where one
   request ends and the next begins, tracks which requests are in flight,
   reacts to a rate limit, remembers which batches already finished, matches
   vectors back to texts, or handles a model that returns the wrong number
   of vectors.
2. **The promise easiest to break in a speed-up.** Matching vectors to
   texts. Writing results into one shared list by "the next free slot" as
   they arrive would interleave batches in completion order, which is random
   with concurrency above 1: every vector would still be valid, just on the
   wrong Chunk, and search would quietly return wrong passages. Each batch
   must write only to its own positions, `start` to `start + len(texts)`.
3. **Why a rate limit stops new batches but a timeout does not.** A rate
   limit says the provider is overloaded by us: sending more only earns more
   refusals and can extend the penalty. A timeout is usually one slow
   request; the next one may be fine, so stopping everything would waste the
   rest of the run.
4. **Why a key includes the position.** A document can repeat the same
   paragraphs, so two batches can hold identical texts. With a key of texts
   only, finishing the first would mark the second as done too, and its
   Chunks would never get vectors.
5. **What could show the concurrency is wrong.** Watching, while raising it
   from 2 to 8: total run time, the share of batches that end
   `EmbeddingRateLimited`, and the latency of each request. If time stops
   falling while rate limits and latency rise, the setting is past what the
   provider allows for this account.
6. **A test whose removal would let a real bug through.**
   `test_no_text_is_lost_or_reordered_and_concurrency_stays_bounded`.
   Without it, a change that handed results to positions in the order the
   requests finished would pass every other test: the closing review showed
   that a variant doing this for successes and failures alike fails only this
   property. In production it would attach valid vectors to the wrong Chunks,
   and search would quietly return wrong passages.

The answers in this evidence were drafted by Claude at the learner's request
and have not yet been rewritten in the learner's own words.
