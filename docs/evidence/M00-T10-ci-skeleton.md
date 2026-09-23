# M00-T10 — CI skeleton evidence

Completed: 2026-09-23

## Invariant

CI must execute the same deterministic project commands as local development,
with no provider calls or credentials, while limiting permissions, cancelling
obsolete runs, caching locked dependencies, and bounding execution time.

## What changed

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | Added separate style, types, and deterministic-test jobs |

The workflow runs on pushes and pull requests targeting `main`, supports manual
dispatch, uses `contents: read`, cancels stale runs for the same ref, and gives
each job a ten-minute timeout. Each job checks out the repository, installs
Python 3.14 and uv, uses uv caching keyed by `pyproject.toml` and `uv.lock`,
synchronizes with `uv sync --locked`, and invokes an existing Makefile target.

## Job contract

| Job | Local command parity |
|---|---|
| `style` | `make format-check`, then `make lint` |
| `types` | `make typecheck` |
| `tests` | `make test-unit` |

The current pytest configuration excludes `live` tests by default, so the CI
test job remains offline and credential-free.

## Verification

```text
$ uv run pre-commit run check-yaml --files .github/workflows/ci.yml
check yaml...............................................................Passed

$ actionlint .github/workflows/ci.yml
exit=0

$ make format-check
5 files already formatted

$ make lint
All checks passed!

$ make typecheck
0 errors, 0 warnings, 0 informations

$ make test-unit
8 passed, 2 deselected in 0.23s
```

No secret, provider payload, or private content entered the workflow or this
record. The workflow contains no deployment, external-service, or live-test
job; those concerns remain later curriculum work.

## Reflection

- **What complexity did this task hide from its caller?** A pull request now
  receives independent style, type, and deterministic-test feedback without the
  contributor needing to provision Python, uv, or the project environment on a
  CI runner.
- **Which alternative would make the next change harder, and why?** One large
  opaque CI script would hide which quality dimension failed and make later
  caching or parallelization changes risky. Separate jobs keep feedback and
  ownership visible.
- **What production failure would escape if the negative test were removed?**
  Without validating the workflow itself, a YAML or expression error could stop
  CI before any quality gate runs. `check-yaml` and `actionlint` cover that
  configuration boundary.

## Confirmations

- Workflow syntax passed both YAML validation and `actionlint`.
- CI commands match existing Makefile entrypoints.
- Permissions are restricted to repository read access.
- Cancellation and explicit job timeouts are configured.
- Locked dependency installation and uv caching are explicit.
