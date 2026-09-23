# M00-T12 — Reference-project lessons evidence

Completed: 2026-09-23

## Invariant

Reference-project practices are adopted only when they strengthen this
service's interfaces, test surface, reproducibility, or operations. The
comparison must name concrete reference paths and record complexity that is
deliberately not being copied.

## What changed

| File | Change |
|---|---|
| `docs/reference-project-lessons.md` | Added a concrete M00-T12 comparison mapping reference paths to local translations and rejected complexity |
| `tests/test_reference_lessons.py` | Added deterministic checks for required reference paths and rejected-complexity decisions |

The comparison uses these existing reference paths:

- `pyproject.toml`
- `browser_use/llm/base.py`
- `browser_use/llm/views.py`
- `browser_use/tokens/views.py`
- `tests/ci/infrastructure/test_config.py`
- `.github/workflows/lint.yml`
- `.github/workflows/test.yaml`
- `tests/ci/`

The local translation keeps uv, typed boundary models, explicit provider ports,
structured usage results, isolated configuration tests, focused CI jobs, and
separate test categories. It rejects browser automation breadth, a large
agent-oriented tree, lazy global configuration, a dynamic CI matrix, and blind
directory copying.

## Verification

Every claimed reference path was checked in the local
`browser-use-main` checkout.

```text
$ uv run pytest tests/test_reference_lessons.py
2 passed in 0.02s

$ make check
6 files already formatted
All checks passed!
0 errors, 0 warnings, 0 informations
11 passed, 1 deselected in 0.15s

$ make hooks
check python ast.........................................................Passed
check toml...............................................................Passed
check yaml...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
Detect hardcoded secrets.................................................Passed
Check Ruff formatting....................................................Passed
Run Ruff linting..........................................................Passed
Run Pyright..............................................................Passed
Run deterministic unit tests.............................................Passed
```

No provider payload, secret, private document, or copied reference-project
implementation entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** A contributor can
  reuse a proven practice while seeing exactly which boundary it strengthens
  and which reference-project scale assumptions do not apply here.
- **Which alternative would make the next change harder, and why?** Copying
  the reference package tree would create names and dependencies before this
  domain has contracts. A path-based comparison keeps the useful principle and
  rejects accidental structure.
- **What production failure would escape if the negative test were removed?**
  A future edit could silently delete the rejected-complexity decisions and
  reintroduce a large provider or agent surface without a documented reason.

## Confirmations

- Every borrowed practice in the M00-T12 section points to a concrete path.
- The comparison is project-specific rather than a generic reference summary.
- The deterministic test checks the comparison document itself.
- Existing application gates remain green.
