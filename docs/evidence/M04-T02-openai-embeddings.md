# M04-T02 — OpenAI embeddings evidence

Completed: 2026-10-22

## Invariant

The OpenAI adapter keeps every promise of the `EmbeddingModel` port: one
vector per input, matched to its input by the response's index, or one port
error and nothing. A failure never takes longer than the deadline, never
retries on its own, and never carries input text in its message, arguments,
or chained exceptions. Every call that is refused, sent, or answered leaves
exactly one log record with counts and the outcome, and no text. Two calls
leave none: a cancelled one, whose `CancelledError` passes straight through,
and one with a NaN deadline, a caller bug refused before anything else.

## Expected failing check

Written before `openai_embeddings.py` exists.

**What fails first.** Once the tests and `tests/openai_stubs.py` exist but
`src/knowledge_service/openai_embeddings.py` does not, pytest stops while
collecting, before any test runs:

- `ModuleNotFoundError: No module named 'knowledge_service.openai_embeddings'`
  in `tests/test_embedding_model_contract.py` and
  `tests/test_openai_embeddings.py`, both of which import the stub module,
  which imports the adapter;
- pytest reports "2 errors during collection" and runs nothing.

Unlike M04-T01, the rest of the suite is not involved: `tests/fakes.py` and
`errors.py` do not import the adapter. A trial run against the prototype with
the module removed showed exactly these two collection errors.

**Predictions for `embed`:**

- **Items in reverse order.** The vectors come back in input order, because
  each item is placed by its `index`, not by its position in the response.
- **429.** `EmbeddingRateLimited`: the caller retries after slowing down.
- **408.** `EmbeddingTimeout`: a passing failure, so the service retries.
- **409.** `EmbeddingUnavailable`: also passing, so the service retries.
- **500.** `EmbeddingUnavailable`: the service retries.
- **401 and 400.** `EmbeddingRequestRefused`: a wrong key or a bad request
  fails the same way every time, so nobody retries. It is classified
  `INTERNAL`.
- **200 with a body that is not JSON.** `EmbeddingMalformedResponse`, raised
  without a chained exception.
- **Vectors of the wrong length.** `EmbeddingMalformedResponse` from
  `checked_batch`.
- **Slower than the deadline.** `EmbeddingTimeout` at the deadline, the
  shorter of the caller's and the configured timeout, with no hidden retry.
- **An input over the token limit.** `EmbeddingInputRejected` with reason
  `input_too_long` and its index. Nothing is sent, and one log record says
  the call was refused.
- **A deadline of NaN.** `ValueError` before anything is sent, instead of
  crashing the event loop.

In every failure, the port error has no `__cause__` or `__context__`, so the
SDK error, whose request body holds the input text, never travels with it.

## Design

Changed files:

- `src/knowledge_service/embeddings.py`: new `EmbeddingRequestRefused`, for
  a request the provider will refuse every time.
- `src/knowledge_service/errors.py`: `classify_failure` maps it to `INTERNAL`
  (no retry), checked before the general `EmbeddingError` branch.
- New `src/knowledge_service/openai_embeddings.py`:
  `OpenAIEmbeddingSettings` and `OpenAIEmbeddingModel`.
- `migrations/env.py`: `fileConfig(..., disable_existing_loggers=False)`.
- `tests/fakes.py`: `FakeEmbeddingModel` gains `returned_dimensions` and
  refuses a NaN deadline.
- New `tests/openai_stubs.py`, `tests/test_embedding_model_contract.py` (12
  tests and 1 opt-in live test), `tests/test_openai_embeddings.py` (39 tests),
  and one new row in `tests/test_errors.py`.

How one `embed` call works:

1. Refuse a NaN deadline (`ValueError`); `asyncio.timeout(nan)` would crash
   the event loop.
2. `check_inputs`; a refusal is logged, and nothing is sent.
3. An empty batch returns no vectors.
4. Send one request with the configured model and dimensions,
   `encoding_format="float"`, the request ID header, and the deadline: the
   shorter of the caller's and the configured timeout, enforced around the
   whole call with `asyncio.timeout`.
5. Translate any failure to a port error and raise it outside the `except`
   block.
6. Read the response without trusting it (`_batch_from`), place each vector
   by its `index` (`_ordered_vectors`), and check it with `checked_batch`.
7. Log one record with counts, timing, and the outcome.

Retry classification:

| SDK failure | Port error | Retry owner |
|---|---|---|
| Deadline, `APITimeoutError`, 408 | `EmbeddingTimeout` | service |
| `APIConnectionError`, 5xx, 409 | `EmbeddingUnavailable` | service |
| 429 | `EmbeddingRateLimited` | caller |
| 400, 401, 403, 404, 413, 422, other statuses | `EmbeddingRequestRefused` | nobody |
| Unreadable or non-JSON body, malformed data | `EmbeddingMalformedResponse` | service |

Decisions:

- **The SDK's retries are off.** `client.with_options(max_retries=0)` returns
  a configured copy, and the adapter keeps the copy. Hidden retries would
  stretch the deadline to three times its length and hide rate limits from
  the batcher that must slow down (M04-T03).
- **No port error chains the SDK error.** The SDK error keeps the HTTP
  request, and its body holds every input text. The adapter records the
  failure and raises after the `except` block, so `__cause__` and
  `__context__` stay empty.
- **Tokens are counted as UTF-8 bytes.** OpenAI's tokenizer turns at least
  one byte into each token, so bytes are a safe upper bound that needs no
  tokenizer download. The adapter can never send a request that is too
  large.
- **The response is untrusted.** The SDK does not validate a successful body,
  so `response` is typed `object` and read with `getattr` and `isinstance`.
- **The limits are settings.** They default to the endpoint's documented
  maximums (2048 inputs, 8192 tokens each, 300,000 tokens per request), as
  stated in the installed SDK's docstring.
- **One log record per call,** sent or refused, under
  `knowledge_service.openai_embeddings`, with `extra={"embedding": {...}}`:
  request ID, provider, model, dimensions, input count, latency, input
  tokens, and outcome. Never text.

**A logging bug found on the way.** `migrations/env.py` called
`fileConfig(config.config_file_name)`, whose default
`disable_existing_loggers=True` silences every logger that already exists. The
new logging test passed alone but failed in the full suite, because
`tests/test_migrations.py` runs `env.py` in the same process. Any application
that runs migrations in-process would have lost its own logs the same way.

**Adversarial review and fixes.** An independent review drove the first
version through the real SDK with a mocked network transport and no real
requests. It checked:

- a 3,000-example Hypothesis fuzz of responses: shuffled, duplicate,
  missing, out-of-range, boolean and `None` indexes, and malformed vectors;
- deadlines and cancellation against a slow mock server;
- log safety with the root logger at `DEBUG` and a 400 body that echoed the
  seeded text.

No vector was ever attached to the wrong input. The deadline held with SDK
retries set to 5 on the original client, outer cancellation stayed
`CancelledError`, and nothing leaked. It found four breaks, all fixed with a
test that fails without the fix:

- **A NaN deadline crashed the event loop** inside `asyncio.timeout`, not
  just the call. The fake had the same latent hazard.
- **Malformed 200 bodies escaped raw.** Plain text, a list, `{}`, missing
  usage, or a non-JSON body raised `AttributeError`, `TypeError`, or
  `JSONDecodeError`, unlogged. The existing "unreadable" test used an
  `APIResponseValidationError` that the default client never raises.
- **408 and 409 were "never retry".** Both are passing failures, and the SDK
  itself retries them.
- **A test gap.** The stub's `with_options` returned the stub itself, so
  dropping the configured client, which turns the SDK's retries back on,
  passed every test. The stub now returns a copy, and the original refuses
  to be used.

An extra check also caught a weak test: returning vectors in response order,
ignoring `index`, passed because the ordering test compared a batch with its
reverse, which is symmetric. The test now compares each vector with its text
embedded alone.

When the learner's code was finished, five deliberate breaks were run against
it, one at a time, and each was caught:

- dropping the configured client failed 37 tests;
- ignoring `index` failed 1;
- chaining the SDK error failed 2;
- removing the `isinstance(data, list)` check failed 1;
- removing the NaN guard failed 1.

Each file was restored afterwards.

**Notes:**

- **The token bound is conservative.** Ukrainian letters are 2 bytes and CJK
  characters 3, so a request holds fewer texts than the provider would
  accept. M04-T03 must size batches with `count_tokens`, not with
  `Chunk.token_count`.
- **The request ID header is `X-Request-ID`,** as in the chat adapter. The
  review suggested OpenAI's documented client header may be
  `X-Client-Request-Id`; this is unverified. The provider's own response
  request ID is not recorded yet.
- **The `openai` SDK logger** in this version logs only the method and
  retry count at `DEBUG`, not the request body; the review checked it.
  Logging setup for production is M10.
- **`fileConfig` still replaces the root handlers** when migrations run
  in-process; that was already true, and only adds noise in tests.
- **The live test** (`-m live`, needs `OPENAI_API_KEY`) was not run; no key
  was used in this lesson.

No credential appears in any file; the live test reads the key from the
environment only. Public types name no provider: `"openai"` is a string in
`EmbeddingModelIdentity`. The seeded text `the salary of Olena is 9000` is
asserted absent from error chains and from every log record.

## Verification

Final run on 2026-10-22 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_embedding_model_contract.py tests/test_openai_embeddings.py tests/test_errors.py -q` | 70 passed, 1 deselected (the live test) |
| `make format-check lint typecheck` | 116 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 632 passed, 54 deselected |
| `make check` (without the database) | 633 passed, 50 skipped, 3 deselected; `openai_embeddings.py` 100% covered |
| `make check` (with the database) | 683 passed, 3 deselected; total coverage 93% |
| `make docs-check` | passed |
| `git diff --check` | clean |

The expected failing check held: with `openai_embeddings.py` removed, pytest
reported 2 errors during collection, `ModuleNotFoundError` in the contract
and adapter test files, and nothing else. Each prediction held. Along the way:

- `classify_failure` first checked `EmbeddingRequestRefused` after the
  general `EmbeddingError` branch, so a refused request was classified
  `DEPENDENCY`; the new row in `tests/test_errors.py` failed. Moving the check
  up fixed it. The same import also needed sorting for Ruff.
- `make format` fixed one extra blank line and one comment indented by a
  single space in `openai_embeddings.py`.

## Reflection

1. **What the adapter hides from its callers.** A caller never sees the SDK,
   its error classes, HTTP status codes, its hidden retries, the request
   format, the response's item order or indexes, how tokens are counted, or
   that a successful response can still be malformed. It gets vectors in
   input order, or one port error whose class says who retries.
2. **The promise easiest to break in a speed-up.** The deadline and the
   retry owner. Turning the SDK's retries back on "for reliability" would
   pass every functional test, but a 10-second deadline could take 30, and a
   rate limit would be retried blindly instead of reaching the batcher that
   must slow down. `StubClient` now fails any adapter that keeps the
   unconfigured client.
3. **Why a 401 is not retried but a 409 is.** A 401 means the key is wrong;
   the next request fails the same way, so retrying wastes time and, at
   scale, floods the provider with bad requests. A 409 means the provider
   was busy with a conflicting operation; a moment later the same request
   can succeed.
4. **Why the port error is not raised `from` the SDK error.** The SDK error
   keeps `.request`, the HTTP request, whose body is the JSON list of every
   input text. Chaining it would put document content into any log that
   prints the exception with its cause.
5. **What could prove the byte bound too wasteful.** Running OpenAI's real
   tokenizer over a sample of Ukrainian and English Chunks and comparing its
   counts with byte counts. If Ukrainian batches use only half their real
   token budget, the next step is an exact tokenizer, or a measured
   per-language factor with a safety margin.
6. **A test whose removal would let a real bug through.**
   `test_a_provider_error_never_carries_the_request_text`. Without it, a
   change to `raise ... from error`, which looks like better debugging,
   would pass every other test and copy confidential document text into
   production logs whenever the provider refused a request.

The answers in this evidence were drafted by Claude at the learner's request
and have not yet been rewritten in the learner's own words.
