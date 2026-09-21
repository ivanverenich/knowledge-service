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
