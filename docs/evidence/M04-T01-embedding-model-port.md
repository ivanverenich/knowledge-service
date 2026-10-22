# M04-T01 — EmbeddingModel port evidence

Completed: 2026-10-22

## Invariant

For any batch, `embed` either returns exactly one finite vector of
`identity.dimensions` numbers per input, in input order, or raises an
`EmbeddingError` and returns nothing. A batch that breaks the limits is
refused before anything is sent, naming the index of the bad input, never its
text. The fake gives the same vector for the same text in any batch, order,
or process.

## Expected failing check

Written before `embeddings.py` exists.

**What fails first.** Once the tests, the fake, and the `errors.py` change
exist but `src/knowledge_service/embeddings.py` does not, pytest stops while
collecting, before any test runs: `ModuleNotFoundError: No module named
'knowledge_service.embeddings'`. The failure is wider than the new tests:

- `tests/test_embeddings.py` imports the module directly;
- `tests/fakes.py` imports it for `FakeEmbeddingModel`, so every test file
  that imports `fakes` fails to collect too;
- `errors.py` imports it, so anything that imports `classify_failure`, such
  as the app and `tests/test_errors.py`, fails as well.

A trial run against the prototype with `embeddings.py` removed showed exactly
this: collection errors in `tests/test_errors.py`, `tests/test_app.py`,
`tests/test_chat_model_contract.py`, and the other importers.

**Predictions for `embed`,** using the fake with `max_inputs=3`,
`max_input_tokens=4`, `max_batch_tokens=6`, and one token per word:

- **Two texts in either order.** Two vectors, each in the position of its
  text. Swapping the order swaps the vectors, and each text gets exactly the
  vector it gets alone in another batch.
- **Exactly `max_inputs` texts.** Accepted: three vectors.
- **One more input.** `EmbeddingInputRejected` with reason
  `too_many_inputs` and no index, because no single input is at fault.
  Nothing is sent.
- **A text of exactly `max_input_tokens` tokens.** Accepted.
- **One token more.** `EmbeddingInputRejected` with reason
  `input_too_long` and the index of that text. Nothing is sent.
- **A batch whose total passes `max_batch_tokens` at its third text**
  (3 + 2 + 2 tokens). `EmbeddingInputRejected` with reason
  `batch_too_long` and index 2, where the running total first passes 6.
  Nothing is sent.
- **An empty or blank text.** `EmbeddingInputRejected` with reason
  `empty_text` and that text's index. Nothing is sent.
- **An empty batch.** No vectors, zero input tokens, and the model is never
  called, so it cannot fail.
- **A provider failure.** The batch counts as sent, and the typed error, for
  example `EmbeddingRateLimited`, reaches the caller unchanged with no
  vectors. `classify_failure` calls it `CAPACITY`. Other provider errors are
  `DEPENDENCY`, and a rejected input is `VALIDATION`.

No error message, `repr`, argument, note, or chained exception contains the
input text.

## Design

Changed files:

- New `src/knowledge_service/embeddings.py`:
  - the value types `EmbeddingModelIdentity`, `EmbeddingLimits`,
    `EmbeddingUsage`, `EmbeddingBatch`, and `Vector`;
  - the `EmbeddingModel` port;
  - the errors `InvalidEmbedding` (impossible settings) and `EmbeddingError`
    with five subclasses, `EmbeddingInputRejected` among them, which carries
    a `RejectionReason` and an index;
  - the shared checks `check_inputs` and `checked_batch`.
- `src/knowledge_service/errors.py`: `classify_failure` maps
  `EmbeddingInputRejected` to `VALIDATION`, `EmbeddingRateLimited` to
  `CAPACITY`, and any other `EmbeddingError` to `DEPENDENCY`.
- `tests/fakes.py`: `FakeEmbeddingModel`.
- New `tests/test_embeddings.py` (43 tests, counting parametrized cases) and
  four new rows in `tests/test_errors.py`.

The port:

| Member | Promise |
|---|---|
| `identity` | Provider, model, and dimensions. Vectors of different identities are never compared. |
| `limits` | `max_inputs`, `max_input_tokens`, `max_batch_tokens` for one call, all positive. |
| `count_tokens(text)` | The text's length in this model's tokens, for M04-T03's batcher. |
| `async embed(texts, *, request_id, timeout_seconds)` | One finite, non-zero vector of `dimensions` numbers per text, in input order, or an `EmbeddingError` and nothing. An empty batch returns no vectors without calling the model. |

Decisions:

- **All or nothing per call.** Providers answer a batch as a whole. If the
  port allowed some vectors plus an error, every caller would have to track
  which Chunks got a vector. A failed call returns nothing, and the caller
  retries or splits that batch.
- **The rules live in the port module.** Every adapter calls `check_inputs`
  before sending and `checked_batch` on the response, so the fake and the
  OpenAI adapter in M04-T02 keep the same promise. `EmbeddingBatch` also
  refuses a vector of the wrong length, so even an adapter that skipped
  `checked_batch` cannot hand back such a batch.
- **Errors name an index and a reason, never the text.** Errors reach logs,
  and Chunk text is document content (ADR 0010). `check_inputs` raises
  outside any `except` block, so no chained exception carries the text
  either.
- **Nothing in a response is trusted.** `checked_batch` takes `vectors:
  object`. Only `int` and `float` count as numbers: `bool` and numeric
  strings do not. Infinite, NaN, overflowing, and all-zero vectors are
  refused, because distance to a zero vector is undefined.
- **The fake is deterministic across processes.** It derives each vector from
  a SHAKE-256 hash of the text, not from Python's `hash()`, which changes
  between processes. One token is one word.

**Adversarial review and fixes.** An independent review attacked the first
version with hand-picked cases, a 3,000-example Hypothesis fuzz of
`checked_batch`, and subprocesses with different `PYTHONHASHSEED` values. It
found six breaks, all fixed with a test that fails without the fix:

- **Raw crashes on bad responses.** `checked_batch` let a raw `TypeError`
  (for `None`, a dictionary, or a generator) or `OverflowError` (for
  `10**400`) escape. The fuzz found 1,058 such escapes.
- **Non-vectors accepted as vectors.** A string, bytes, numeric strings,
  booleans, and an all-zero vector all passed.
- **One string embedded letter by letter.** `embed("abc", ...)` became three
  one-letter inputs, because a `str` is a `Sequence[str]` and Pyright cannot
  tell. It now raises `TypeError`.
- **A bad token count bypassed the budget.** A negative, zero, boolean, or
  NaN count from `count_tokens` was trusted. Now it gives
  `uncountable_text`.
- **Text leaked or crashed.** A lone surrogate character crashed with a raw
  `UnicodeEncodeError`, whose `object` attribute holds the whole text, and a
  tokenizer error that quotes its input passed through. Both are now refused
  by index (`unencodable_text`, `uncountable_text`) with no chained
  exception.
- **The fake ignored its deadline.** It now raises `EmbeddingTimeout` after
  `timeout_seconds`.

A Hypothesis property in the tests now shows that any response is either a
valid batch or an `EmbeddingMalformedResponse`. The review confirmed the fake
is identical across processes and hash seeds, unit length for 1 to 10,000
dimensions, and that importing `embeddings` into `errors.py` creates no
import cycle.

When the learner's code was finished, five deliberate breaks were run against
it, one at a time, and each was caught: `>=` for `max_inputs` failed 2 tests,
recording the batch before checking it failed 9, raising a rejection from an
error that holds the text failed 1, accepting booleans as numbers failed 1,
and removing the zero-vector check failed 1. Each file was restored
afterwards.

**Notes:**

- **Rejected input is `VALIDATION` (HTTP 422, retried by the caller).** That
  is right for an over-long question at search time. During ingestion, an
  over-long Chunk is a bug in batching or chunking, not something a caller
  can fix; M04-T03 should treat it that way.
- **Unicode forms are not normalized here.** `café` written with a combined
  or a separate accent gives different vectors. M03's normalization already
  produces NFC text, so Chunks arrive in one form; a question will need the
  same.
- **A text of only invisible characters,** such as a zero-width space, passes
  the empty check. Normalization removes such characters before chunking.
- **Identity strings are not trimmed:** `" openai"` and `"openai"` would be
  different identities. Adapters set them from configuration.
- **A numpy `float64` would be refused** as not a plain `float`. Providers
  return JSON numbers, and the OpenAI SDK gives Python floats.
- **The error message** reads `embedding input rejected empty_text at input
  0`, without the colon in the guide's version. No test depends on it.

No credential, private content, or provider name is in the public types; the
provider is a string in `EmbeddingModelIdentity`. The tests use only
synthetic text, and the seeded text `the salary of Olena is 9000` is asserted
absent from every rejection.

## Verification

Final run on 2026-10-22 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embeddings.py tests/test_errors.py -q` | 61 passed |
| `make format-check lint typecheck` | 112 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 580 passed, 53 deselected |
| `make check` (without the database) | 581 passed, 50 skipped, 2 deselected; `embeddings.py` fully covered |
| `make check` (with the database) | 631 passed, 2 deselected; total coverage 93% |
| `make docs-check` | passed |
| `git diff --check` | clean |

The expected failing check held: in a trial run with `embeddings.py`
removed, pytest stopped at collection with `ModuleNotFoundError` in
`tests/test_errors.py`, `tests/test_app.py`, and the other importers of
`fakes` and `errors`. Each `embed` prediction held. Along the way:

- `classify_failure` first checked `EmbeddingError` before
  `EmbeddingInputRejected`, so a rejected input was classified as
  `DEPENDENCY`; the new row in `tests/test_errors.py` failed. Moving the
  subclass check first fixed it.
- `tests/fakes.py` had no final newline; `make format` added it.

## Reflection

1. **What the port hides from its callers.** A caller passes texts and a
   deadline. It never counts model tokens, checks the three limits, finds
   which input is at fault, validates the provider's response, checks vector
   lengths, numbers, or zero vectors, maps provider errors, or removes text
   from errors before logging.
2. **The promise easiest to break in a later speed-up.** Order. If M04-T03 ran
   batches in parallel and wrote vectors back by position in a shared list,
   a batch that finished early or was retried could put Chunk 7's vector on
   Chunk 3, and nothing would fail: every vector is still valid, only
   attached to the wrong text. Each batch must keep its own inputs and map
   its vectors back to exactly those.
3. **Why a refusal names an index and not the text.** Errors go to logs,
   traces, and error reports, and Chunk text is document content, possibly
   confidential. ADR 0010 says telemetry carries identifiers and counts, not
   content. The index is enough to find the input in the caller's own batch.
4. **Why all or nothing.** With partial results, every caller would have to
   track which inputs got a vector and which failed, and could easily store
   a half-embedded document. A failed call gives nothing, so the batcher has
   one decision to make: retry or split the whole batch.
5. **What could prove the design wrong.** Running the real tokenizer over
   Ukrainian Chunks. A 256-token Chunk by our own count may be many more
   model tokens in Cyrillic, so a batch filled by `Chunk.token_count` would
   overrun `max_batch_tokens`. Measuring the ratio of model tokens to our
   tokens for both languages would show whether the limits need language
   headroom.
6. **A test whose removal would let a real bug through.**
   `test_a_rejection_never_carries_the_input_text`. Without it, a change
   that chained the original error, or added the text to the message for
   easier debugging, would pass every other test and copy confidential
   document text into production logs.

The answers in this evidence were drafted by Claude at the learner's request
and have not yet been rewritten in the learner's own words.
