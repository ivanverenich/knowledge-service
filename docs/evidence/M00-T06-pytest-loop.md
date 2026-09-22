# M00-T06 — pytest loop evidence

Completed: 2026-09-22

## Invariant

The default test command stays deterministic, offline, and credential-free:
`uv run pytest` must not call an external provider, read a secret, or depend on
machine state. It must also preserve the M00-T04 import side-effect boundary and
the M00-T05 gates. The environment change adds a development-only dependency,
so the runtime `dependencies` list stays empty.

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | `pytest-timeout` added to the `dev` group; `[tool.pytest.ini_options]`, `[tool.coverage.run]`, and `[tool.coverage.report]` added |
| `uv.lock` | Re-resolved: 15 packages, up from 14 |
| `tests/test_pytest_loop.py` | New. Proves the loop configuration is active rather than merely present |

Two lint findings raised by the M00-T05 gates against the new test file were
fixed mechanically, with `ruff check --fix` and `ruff format`:

- `I001` — missing blank line between the standard-library and third-party
  import blocks.
- `W292` — no newline at end of file.

Both were fixed before this record was written; the gates refuse a file that
violates either rule, which is the intended behavior.

## Configured behavior

| Setting | Value | Reason |
|---|---|---|
| `testpaths` | `tests` | A bare `pytest` cannot wander into `docs` or `.venv` |
| `addopts` | `--strict-markers --strict-config -m "not live"` | Undeclared markers and undocumented options fail instead of warn; `live` is off unless requested |
| `markers` | `integration`, `live` | Declared before first use, because strict markers rejects undeclared ones |
| `asyncio_mode` | `auto` | Any `async def test_` runs without a per-test marker |
| `asyncio_default_fixture_loop_scope` | `function` | One loop per test |
| `asyncio_default_test_loop_scope` | `function` | Stated rather than inherited |
| `timeout` | `30` | A hung test fails in 30 seconds instead of hanging the suite |
| `coverage.source` | `knowledge_service` | Measures the package, not the tests |
| `coverage.branch` | `true` | Branch coverage is recorded from the start |

The run header confirms the configuration is loaded, not merely present:

```text
platform darwin -- Python 3.14.2, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/ivanverenich/Documents/searching_job/rag_project
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0, timeout-2.4.0, asyncio-1.4.0
timeout: 30.0s
timeout method: signal
timeout func_only: False
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
```

## Deliberate exclusions

1. **No coverage threshold.** `fail_under` is not set. The package contains one
   docstring, so any threshold passes vacuously and would create false
   confidence. Coverage flags also stay out of `addopts`, so a narrow test run
   is not slowed by measurement; the `all checks` command in M00-T07 will own
   that.
2. **`live` is deselected by default.** Declaring the marker is not enough: on a
   machine where provider credentials exist, a bare `pytest` would make real
   calls. `-m "not live"` in `addopts` makes the opt-in explicit. The guard test
   fails loudly if that default is ever removed.

## Negative tests

The acceptance criterion is that a deliberately failing test goes red, then the
corrected suite passes with the expected markers. Three probes were run, each
planted and then removed.

### 1. A deliberately failing test goes red

`assert True` was changed to `assert False` in the integration test:

```text
$ uv run pytest -m integration
E       assert False

tests/test_pytest_loop.py:16: AssertionError
=========================== short test summary info ============================
FAILED tests/test_pytest_loop.py::test_integration_marker_is_registered - ass...
=========================== 1 failed, 5 deselected in 0.02s ============================
exit=1
```

### 2. An undeclared marker is rejected

The reason two markers are declared up front. With
`@pytest.mark.not_declared` present:

```text
$ uv run pytest
collected 3 items / 1 error
==================================== ERRORS ====================================
ERROR tests/test_pytest_loop.py - Failed: 'not_declared' not found in `marker...
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
exit=2
```

Note that `--strict-markers` does **not** reject an unknown name used only in a
`-m` expression: `uv run pytest -m not_a_real_marker` exits 0 with
`6 deselected`. Only a marker applied in source is validated. A typo in a
selection expression therefore fails silently by selecting nothing, which is
worth knowing before CI relies on `-m` for gating.

### 3. The timeout watchdog is enforced

A test containing `time.sleep(5)` was run with a one-second override:

```text
$ uv run pytest --timeout=1 -m integration
E       Failed: Timeout (>1.0s) from pytest-timeout.
================== 1 failed, 1 passed, 5 deselected in 1.03s ===================
exit=1
```

Both probes were removed afterwards. The final file is exactly the three tests
below, and the gates are green.

## Final test module

```python
"""Tests that prove the pytest loop configuration is active."""

import asyncio

import pytest


async def test_async_test_runs() -> None:
    await asyncio.sleep(0)

    assert asyncio.get_running_loop().is_running()


@pytest.mark.integration
def test_integration_marker_is_registered() -> None:
    assert True


@pytest.mark.live
async def test_live_marker_is_not_enabled_by_default() -> None:
    raise AssertionError("live tests must not run without an explicit opt-in")
```

The third test fails **by design** when selected with `-m live`. It exists to
detect a regression in the default deselection, not to assert product behavior.
A reader who sees `1 failed` from `uv run pytest -m live` is seeing the guard
fire, not a broken suite.

## Verification

Commands were run with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the
default user cache is outside the writable workspace.

```text
$ uv sync --locked
Resolved 15 packages in 11ms
Checked 14 packages in 0.51ms

$ uv run pytest tests/test_pytest_loop.py
2 passed, 1 deselected in 0.01s

$ uv run ruff format --check .
3 files already formatted

$ uv run ruff check .
All checks passed!

$ uv run pyright
0 errors, 0 warnings, 0 informations

$ uv run pytest
5 passed, 1 deselected in 0.01s

$ uv run pytest -m integration
1 passed, 5 deselected in 0.01s

$ uv run pytest -m live
1 failed, 5 deselected in 0.02s   # the opt-in guard firing, as designed

$ uv run pytest --cov=knowledge_service --cov-report=term-missing
Name    Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------
TOTAL       0      0      0      0   100%

1 file skipped due to complete coverage.
5 passed, 1 deselected in 0.03s
```

The coverage line reports 0 statements because `knowledge_service` contains only
a docstring. Reporting is wired; the number becomes meaningful when M01 adds
executable code.

## Reflection

- **What complexity did this task hide from its caller?** A caller writes
  `async def test_*` and never sees the event-loop lifecycle, the loop-scope
  defaults, or the deselection expression. A caller runs `pytest` and never sees
  that undeclared markers abort collection or that a hang is killed at 30
  seconds. That is the point of a configured loop: the harness owns those
  decisions, and one file states them.
- **Which alternative would make the next change harder, and why?** Annotating
  every async test with `pytest.mark.asyncio` under `asyncio_mode = "strict"`
  would be more explicit, but the annotation would appear on every async test in
  the project while carrying no information. Marking the opt-in suites instead —
  where the marker changes *whether a real external system is called* — puts the
  ceremony where the risk is. The weaker alternative would be declaring the
  `live` marker without deselecting it, which converts a safety property into a
  convention that a bare `pytest` run silently violates.
- **What production failure would escape if the negative test were removed?**
  Without the timeout probe, a test that awaits a response which never arrives
  would hang the suite until CI's own job limit killed it, with no indication of
  which test was waiting. Without the `live` guard test, a future edit that
  drops `-m "not live"` from `addopts` would let the default suite make real
  provider calls with real credentials — a cost and data-exposure incident
  rather than a test failure.

## Confirmations

- `dependencies` remains empty; `pytest-timeout` is a development-only addition.
- No secret, credential, private document, or provider payload entered the
  configuration, the tests, or this record.
- The `live` marker is not attached to any test that performs real I/O; the only
  test carrying it deliberately raises.
- The M00-T04 import-side-effect tests and the M00-T05 gates still pass.
