# M01-T10 — Capture model usage and cost inputs

This guide turns the task specification into an ordered coding checklist. It is
an implementation guide; keep the reusable task spec as the acceptance source.

## Outcome

The service returns safe model-usage metadata: provider, model, token counts,
latency, and a configurable price identifier. Cost estimates use injected price
data, so the code does not claim that a hard-coded rate is current.

## Starting point and invariant

M01-T09 added provider-neutral `GeneratedAnswer` parsing in
`src/knowledge_service/openai_model.py`. The existing `Usage` contract in
`src/knowledge_service/contracts.py` has token counts only, and the adapter
already has the provider response's usage data and its configured model name.

Preserve this invariant while adding metadata: `ChatModel.answer()` returns the
same provider-neutral `Answer`, including its request ID and token counts. No
OpenAI SDK response object or secret enters `Answer`.

## Checklist

### 1. Extend the usage contract

- [ ] **Purpose:** Give the application enough safe metadata to understand which
  model produced an answer and how many tokens and milliseconds it consumed.
  Token counts are already available; provider, model, and latency make those
  counts interpretable.
- [ ] **Decision:** Extend `Usage` rather than creating a second competing usage
  object. Keep the fields provider-neutral strings and integers; represent a
  missing price identifier as `None` rather than inventing an identifier.
- [ ] **Action:** In `src/knowledge_service/contracts.py`, extend `Usage` along
  these lines:

  ```python
  class Usage(BaseModel):
      """Safe model usage metadata for one answer."""

      provider: str = Field(min_length=1)
      model: str = Field(min_length=1)
      price_id: str | None = None
      latency_ms: int = Field(ge=0)
      input_tokens: int = Field(ge=0)
      output_tokens: int = Field(ge=0)
      total_tokens: int = Field(ge=0)
  ```

  Update every construction of `Usage` (search with
  `rg -n 'Usage\\(' src tests`). The fake model in `tests/fakes.py` must provide
  values too, so application and workflow tests continue to exercise the same
  contract.
- [ ] **Check:** Add or update contract tests proving negative tokens/latency
  are rejected and normal metadata is accepted. Search results should show no
  old `Usage(...)` constructors missing the new required fields.

### 2. Configure a price identifier and measure adapter latency

- [ ] **Purpose:** Token counts alone do not identify the relevant price table,
  and the service should measure elapsed time instead of asking the provider to
  supply local request latency.
- [ ] **Decision:** Add an optional `model_price_id` to `OpenAISettings`; do not
  hard-code dollar rates. Measure elapsed time around the awaited provider call
  with `time.perf_counter()`, which is a monotonic clock intended for measuring
  durations.
- [ ] **Action:** In `src/knowledge_service/openai_model.py`, add `time` and
  start timing just before the request:

  ```python
  started_at = time.perf_counter()
  response = await self._client.responses.parse(...)
  latency_ms = round((time.perf_counter() - started_at) * 1_000)
  ```

  Add the configurable identifier to `OpenAISettings`:

  ```python
  model_price_id: str | None = None
  ```

  When building `Usage`, provide `provider="openai"`,
  `model=self._settings.model_name`,
  `price_id=self._settings.model_price_id`, and the measured `latency_ms`, in
  addition to the three provider token counts. A price ID names a version of
  pricing data; it is not itself a price.
- [ ] **Check:** In `tests/test_openai_model.py`, configure a test price ID and
  assert all metadata values. Make latency deterministic by monkeypatching
  `knowledge_service.openai_model.time.perf_counter` to return two known values
  (for example `10.0` and `10.025`); the expected latency is `25` ms. Never
  assert a real wall-clock duration in a unit test.

### 3. Add injected pricing data and a cost calculation

- [ ] **Purpose:** Cost changes over time and differs by model and token type.
  The calculation should use an explicit price record supplied by the caller,
  not a rate embedded in application code.
- [ ] **Decision:** Put the calculation behind a small provider-neutral module,
  `src/knowledge_service/pricing.py`. Store input/output rates per million
  tokens as `Decimal` values to avoid binary floating-point rounding in money
  calculations. Return `None` when the usage has no matching configured price;
  that means “unknown,” not free.
- [ ] **Action:** Add a price record and estimator. For example:

  ```python
  from collections.abc import Mapping
  from decimal import Decimal

  from pydantic import BaseModel, Field

  from knowledge_service.contracts import Usage


  class ModelPrice(BaseModel):
      price_id: str = Field(min_length=1)
      input_usd_per_million_tokens: Decimal = Field(ge=0)
      output_usd_per_million_tokens: Decimal = Field(ge=0)


  def estimate_cost(
      usage: Usage,
      prices: Mapping[str, ModelPrice],
  ) -> Decimal | None:
      if usage.price_id is None:
          return None

      price = prices.get(usage.price_id)
      if price is None:
          return None

      million = Decimal("1000000")
      input_cost = (
          Decimal(usage.input_tokens) * price.input_usd_per_million_tokens / million
      )
      output_cost = (
          Decimal(usage.output_tokens) * price.output_usd_per_million_tokens / million
      )
      return input_cost + output_cost
  ```

  Inject `prices` into the estimator rather than loading live prices or putting
  actual dollar rates in `OpenAISettings`. Keep this a calculation, not a new
  external API or price-refresh service.
- [ ] **Check:** In `tests/test_pricing.py`, construct a `Usage` record and a
  `ModelPrice` with deliberately simple test rates. For 1,000 input tokens at
  `$2 per million` and 500 output tokens at `$8 per million`, assert the exact
  estimate is `Decimal("0.006")`. Also assert an unknown/missing `price_id`
  returns `None` rather than zero.

### 4. Verify at the interfaces

- [ ] **Purpose:** Prove the OpenAI adapter fills metadata, the cost estimator
  uses injected data, and existing callers still receive an `Answer` rather
  than provider objects.
- [ ] **Decision:** Use deterministic fakes and the existing module interfaces;
  no live model calls or real prices are needed.
- [ ] **Action:** Run the narrow tests first, then repository checks:

  ```bash
  uv run pytest tests/test_openai_model.py tests/test_pricing.py tests/test_contracts.py
  make check
  make docs-check
  make hooks
  git diff --check
  ```

  If the targeted test filenames differ because you chose a different module
  boundary, adjust the command to name the tests you added.
- [ ] **Check:** The focused tests pass; `make check` passes formatting, lint,
  strict typing, and deterministic tests; docs and hooks pass. Verify no rates,
  credentials, or provider payloads were copied into the evidence.

### 5. Record evidence and explain the design

- [ ] **Purpose:** Leave a reproducible record of what changed and why, so the
  next task can rely on actual behavior rather than memory.
- [ ] **Decision:** Record only measured command results and injected test data;
  do not present test prices as current provider prices.
- [ ] **Action:** Add a short evidence file under `docs/evidence/` with the
  invariant, changed files, behaviors proved, exact commands/results, and your
  own answers to the task's three reflection questions.
- [ ] **Check:** The task is ready to finish only when every acceptance case is
  observed, the evidence is accurate, and you can explain the design in your
  own words.

## Completion checklist

- [ ] Answer usage contains safe provider, model, token, latency, and price-ID
  metadata.
- [ ] Price identifiers are configurable; no current dollar rates are
  hard-coded.
- [ ] A deterministic unit test calculates a cost from injected price data and
  covers missing pricing data.
- [ ] Focused tests, `make check`, `make docs-check`, hooks, and diff checks pass.
- [ ] Evidence and learner reflection are recorded.

Implement the steps yourself. If a test fails, paste the failure and the small
relevant code section; we’ll work through it one step at a time.
