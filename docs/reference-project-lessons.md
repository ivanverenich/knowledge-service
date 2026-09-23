# Lessons from `browser-use-main`

The reference repository is `/Users/ivanverenich/Documents/searching_job/browser-use-main`; the originally supplied `broser-use-main` path was a typo.

## Practices to borrow

- `uv`, one `pyproject.toml`, a lockfile, and focused command wrappers.
- Pydantic v2 request/result contracts with validation and field descriptions.
- Runtime-checkable provider protocols and async operations at true I/O seams.
- Provider-specific adapters grouped behind a stable interface.
- Behavior-focused regression tests plus distinct unit, integration, slow, and provider-dependent suites.
- Ruff, type checking, pre-commit checks, secret detection, and CI timeouts/caching.
- Dependency caching and a non-root runtime user in Docker images.
- Structured usage/token results that make cost and latency observable.

## Patterns to reject here

- Browser automation packages and their dependency breadth.
- A large agent-oriented package tree before the domain is understood.
- Legacy and current configuration models living together.
- A giant always-loaded agent instruction file.
- A dynamic CI matrix optimized for a much larger repository.
- Blindly pinning every dependency without an update policy.

## Adaptation rule

Borrow a practice only when it strengthens a module interface, test surface, reproducibility, or operations. Do not copy directory names merely because they exist in the reference repository.

## M00-T12 concrete comparison

| Reference path | Practice | Local translation |
|---|---|---|
| `pyproject.toml` | One Python project with uv metadata, tool configuration, and dependency groups | Keep one `pyproject.toml`, `uv.lock`, and Makefile-backed command surface |
| `browser_use/llm/base.py` | Runtime-checkable async provider protocol | Introduce a `ChatModelPort` only when M01 reaches the model boundary |
| `browser_use/llm/views.py` | Pydantic request/result models at an external boundary | Use Pydantic contracts for API/provider boundaries, not domain entities |
| `browser_use/tokens/views.py` | Structured token, usage, and cost results | Preserve usage and cost as explicit application data when generation is added |
| `tests/ci/infrastructure/test_config.py` | Configuration behavior tested through environment changes | Keep settings tests deterministic and isolated from the real `.env` and process environment |
| `.github/workflows/lint.yml` | Dedicated style/type feedback in CI | Keep separate style, type, and test jobs with uv caching and timeouts |
| `.github/workflows/test.yaml` | Automated test execution with environment setup | Reuse the repository's locked setup and Makefile test commands |
| `tests/ci/` | Behavior-focused regression suites separated from provider/browser concerns | Keep unit, integration, and live/provider-dependent tests explicitly separated |

### Complexity deliberately rejected

- `browser_use/llm/` provider breadth: this project starts with one model port and adds adapters only when a contract requires them.
- Browser automation dependencies and browser-specific test infrastructure: they do not serve organizational knowledge retrieval.
- The reference project's large agent-oriented package tree: this repository keeps the initial package boundary minimal.
- Lazy global configuration behavior from the reference configuration tests: this project uses typed settings with explicit validation at the application boundary.
- A large CI matrix and many specialized workflows: the current repository needs three focused jobs before matrix expansion is justified.
- Blindly copying every reference directory: only practices that strengthen an interface, test surface, reproducibility, or operations are adopted.
