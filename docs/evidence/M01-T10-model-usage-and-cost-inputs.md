# M01-T10 — Model usage and cost inputs evidence

Completed: 2026-09-30

## Invariant

The `ChatModel` boundary continues to return the provider-neutral `Answer` with
its request ID and token counts. Usage now also identifies the provider, model,
latency, and optional price ID. Cost estimation consumes injected pricing data;
no current provider rates are hard-coded.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/contracts.py` | Extended `Usage` with provider, model, optional price ID, and non-negative latency. |
| `src/knowledge_service/openai_model.py` | Added configurable price ID and measures elapsed adapter time; maps safe usage metadata into `Answer`. |
| `src/knowledge_service/pricing.py` | Added validated `ModelPrice` and Decimal cost estimation using an injected price mapping. |
| `tests/fakes.py` | Updated deterministic model fakes to satisfy the expanded `Usage` contract. |
| `tests/test_contracts.py` | Added validation coverage for usage metadata and invalid values. |
| `tests/test_openai_model.py` | Verifies provider/model/price ID/token metadata and deterministic 25 ms latency. |
| `tests/test_pricing.py` | Verifies the cost calculation and returns `None` for missing or unknown price IDs. |

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_openai_model.py tests/test_pricing.py tests/test_contracts.py
29 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
Ruff format: 25 files already formatted
Ruff lint: all checks passed
Pyright: 0 errors, 0 warnings, 0 informations
83 passed, 1 deselected
Total coverage: 93%

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed, including deterministic unit tests

$ git diff --cached --check && git diff --check
passed
```

No secrets, current price claims, raw provider payloads, or private documents
entered the evidence or test fixtures. The prices in unit tests are synthetic
inputs used only to prove the arithmetic.

## Reflection

- **What complexity did this design hide from its caller?** The design hides provider-specific usage fields, token accounting, latency measurement, and price lookup behind one stable usage contract.
- **Which alternative would make future pricing changes harder, and why?** Hard-coding model prices in the adapter would make future pricing changes harder because code changes and redeployments would be required whenever rates change.
- **What production failure could escape if the negative tests were removed?** Missing prices could cause incorrect failures or silent cost omissions, and calculation bugs could report wrong costs in production.
