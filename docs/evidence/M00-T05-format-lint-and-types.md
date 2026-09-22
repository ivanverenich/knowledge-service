# M00-T05 — Formatting, linting, and type-checking evidence

Completed: 2026-09-22

## Invariant

Configuring the checks must not change what the package does. Importing
`knowledge_service` from the locked project environment continues to succeed
without printing, starting services, reading configuration, or contacting an
external system. The change adds no runtime dependency and no import-time
side effect.

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | 45 added lines: `[tool.ruff]`, `[tool.ruff.format]`, `[tool.ruff.lint]`, `[tool.ruff.lint.per-file-ignores]`, `[tool.pyright]` |
| `tests/test_package.py` | 2 changed lines: annotated the `capsys` fixture; escaped the `pytest.raises` match pattern |

No command script, Makefile, or developer entrypoint was created. M00-T07 owns
that deliverable, so this task leaves the checks reachable only through the
tools themselves.

## Scope discovery: what the old check actually scanned

The M00-T04 record reported `285 files already formatted`. The repository holds
two Python files. Splitting the scan by path explained the difference:

```text
docs             281 files already formatted
src              1 file already formatted
tests            1 file already formatted
.venv            warning: No Python files found under the given path(s)
.git             warning: No Python files found under the given path(s)
```

Ruff 0.16 formats Python code blocks inside Markdown files. The `285` was 283
planning documents plus 2 Python files, so the recorded check was asserting
something almost entirely about curriculum prose and almost nothing about the
seed package. A probe confirmed the behavior:

```text
$ uv run ruff format --check /tmp/ruff-md-probe/probe.md
1 file would be reformatted

$ uv run ruff format --diff /tmp/ruff-md-probe/probe.md
- x = {  "a":1,   "b":2 }
+ x = {"a": 1, "b": 2}
```

`ruff check` never linted Markdown; this was a formatter-only over-reach.
Setting `extend-exclude = ["**/*.md"]` makes the format scope match the seed
package: `2 files already formatted`. Without this task the discrepancy would
have stayed invisible, and a later Markdown edit would have silently rewritten
reference documentation.

## Ruff formatting configuration

| Setting | Value | Reason |
|---|---|---|
| `target-version` | `py314` | Binds the formatter to the runtime pinned in M00-T03 instead of leaving it inferred |
| `line-length` | `88` | One width shared by the formatter and `E501` |
| `src` | `["src", "tests"]` | Declares where first-party code lives so import sorting classifies `knowledge_service` correctly |
| `extend-exclude` | `["**/*.md"]` | Keeps the formatter off planning documents (see above) |
| `quote-style` | `double` | Stated rather than implied |
| `indent-style` | `space` | Stated rather than implied |
| `line-ending` | `lf` | Removes line-ending churn between contributors |
| `skip-magic-trailing-comma` | `false` | A trailing comma still forces one-item-per-line |
| `docstring-code-format` | `true` | Example code inside docstrings stays as formatted as module code |

## Ruff linting configuration

`select` is explicit because Ruff's default rule set is only `E4`, `E7`, `E9`,
and `F`. The M00-T04 `All checks passed!` result was that default subset, not a
project policy.

| Selected | Purpose |
|---|---|
| `E`, `W`, `F` | Baseline pycodestyle and pyflakes correctness |
| `I` | Import order |
| `UP` | Modern syntax for the targeted runtime |
| `B`, `SIM`, `C4` | Real-bug patterns and avoidable complexity |
| `A` | Builtin shadowing |
| `PTH` | Path handling |
| `RET` | Return-path simplification |
| `T20` | No stray `print` in library code |
| `RUF` | Ruff-specific rules, including dropped `asyncio` task references |
| `ASYNC` | Async correctness (see the negative test below) |
| `PT` | Pytest-style rules, so M00-T06 starts from a consistent test shape |
| `S` | Security patterns |

## Deliberate exclusions

Exclusions are recorded in configuration, where a reviewer sees them, in three
kinds:

1. **Path scope.** `extend-exclude = ["**/*.md"]` for planning documents, plus
   Ruff's built-in excludes for `.venv`, `.git`, and tool caches. Reason: this
   task formats and checks the seed package. Making one formatter authoritative
   over 283 reference documents is a larger decision, and M00-T13 owns
   documentation consistency.
2. **Rule families not selected.** `D` (docstring prose style) is not selected
   because this task protects behavior and contracts, not written style.
   `ANN` (annotation presence) is not selected because Pyright strict already
   fails on a missing or unknown annotation in `src` and `tests`; enabling both
   would report one defect twice. `PL` (the pylint subset) is not selected
   because it overlaps rules already chosen. `COM` and `ISC` are not selected
   because Ruff documents `COM812` and `ISC001` as incompatible with the
   formatter.
3. **One per-file ignore.** `"tests/**" = ["S101"]` permits bare `assert`, which
   is how pytest expresses expectations. `S101` remains active for `src`.

The third exclusion was verified to be scoped rather than global, by linting the
same source text under both filenames without writing a file:

```text
$ printf 'def _probe(value: object) -> None:\n    assert value is not None\n' \
    | uv run ruff check --stdin-filename src/knowledge_service/__init__.py -
    assert value is not None
    ^^^^^^
Found 1 error.

$ printf 'def _probe(value: object) -> None:\n    assert value is not None\n' \
    | uv run ruff check --stdin-filename tests/test_package.py -
All checks passed!
```

Deliberately *not* excluded: `E501`. Pre-emptively weakening a check before it
has failed once would hide the decision behind a rule name instead of behind
evidence.

## Pyright configuration

| Setting | Value |
|---|---|
| `typeCheckingMode` | `strict` |
| `pythonVersion` | `3.14` |
| `venvPath` / `venv` | `.` / `.venv` (the locked environment) |
| `include` | `src`, `tests` |

`typeCheckingMode` defaults to `standard`, which is why M00-T04's
`0 errors` said little about a docstring-only module. Strict mode treats
implicit `Any` as an error, so silence is no longer acceptance.

Strict mode immediately found real gaps in the existing test file — 6 errors,
all rooted in one missing annotation:

```text
tests/test_package.py:12:39 - error: Type of parameter "capsys" is unknown (reportUnknownParameterType)
tests/test_package.py:12:39 - error: Type annotation is missing for parameter "capsys" (reportMissingParameterType)
tests/test_package.py:15:5  - error: Type of "captured" is unknown (reportUnknownVariableType)
tests/test_package.py:15:16 - error: Type of "readouterr" is unknown (reportUnknownMemberType)
tests/test_package.py:17:12 - error: Type of "out" is unknown (reportUnknownMemberType)
tests/test_package.py:18:12 - error: Type of "err" is unknown (reportUnknownMemberType)
6 errors, 0 warnings, 0 informations
```

One annotation resolved all six:

```python
def test_package_import_has_no_output(capsys: pytest.CaptureFixture[str]) -> None:
```

Tests are held to the same strict setting as `src`. A fixture annotation is
cheap; a fixture silently typed as `Unknown` is not.

## One lint finding that was a behavior bug

Enabling `RUF` produced a finding that no reviewer would have flagged by eye:

```text
RUF043 Pattern passed to `match=` contains metacharacters but is neither escaped nor raw
  --> tests/test_package.py:23:36
   |
23 |         ModuleNotFoundError, match="knowledge_service.not_a_real_module"
   |                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
```

The unescaped `.` characters are regex wildcards, so the pattern also matched
`knowledge_serviceXnot_a_real_module`. The assertion was weaker than it read.
It is now `match=r"knowledge_service\.not_a_real_module"`.

## Negative test: planted violation

A deliberate violation was planted in `src/knowledge_service/__init__.py` to
prove the configuration rejects bad code rather than merely existing:

```python
"""Permission-aware organizational knowledge service."""

import os
import time


async def _planted_violation( ) -> None:
    time.sleep(1)
    count: int = "not an int"
```

The deterministic suite still passed. This is the central point of the task:

```text
$ uv run pytest
3 passed in 0.02s
```

The single gate command failed:

```text
$ uv run ruff format --check . && uv run ruff check . && uv run pyright
unformatted: File would be reformatted
 --> src/knowledge_service/__init__.py:7:30
  |
6 |
  - async def _planted_violation( ) -> None:
7 + async def _planted_violation() -> None:
8 |     time.sleep(1)
  |

1 file would be reformatted, 1 file already formatted
exit=1
```

Run separately, lint and type checking reported the following.

```text
$ uv run ruff check .
help: Remove unused import: `os`

ASYNC251 Async functions should not call `time.sleep`
 --> src/knowledge_service/__init__.py:8:5

F841 Local variable `count` is assigned to but never used
 --> src/knowledge_service/__init__.py:9:5

Found 3 errors.

$ uv run pyright
  src/knowledge_service/__init__.py:3:8 - error: Import "os" is not accessed (reportUnusedImport)
  src/knowledge_service/__init__.py:7:11 - error: Function "_planted_violation" is not accessed (reportUnusedFunction)
  src/knowledge_service/__init__.py:9:5 - error: Variable "count" is not accessed (reportUnusedVariable)
  src/knowledge_service/__init__.py:9:18 - error: Type "Literal['not an int']" is not assignable to declared type "int"
    "Literal['not an int']" is not assignable to "int" (reportAssignmentType)
4 errors, 0 warnings, 0 informations
```

The async rule fired as intended: `ASYNC251` catches a blocking sleep inside an
`async` function. The violation was then removed, and `git diff -- src` is
empty — the seed package is byte-identical to its M00-T04 state.

## Deviation from the task's step 3

The task asks for deterministic tests at the module interface covering the
success case, the most important edge case, and one typed failure. The package
exposes no behavior-bearing interface yet — it contains a docstring — so an
"edge case" would have to be invented, which would add speculative domain
surface and pre-empt M02. M00-T06 owns the pytest loop. The typed failure is
therefore recorded as the planted violation above, which is what the
acceptance criterion asks for.

## Verification

Commands were run with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the
default user cache is outside the writable workspace. The narrow test ran
first, then the gates, then the full suite.

```text
$ uv sync --locked
Resolved 14 packages in 10ms
Checked 13 packages in 0.48ms

$ uv run pytest tests/test_package.py
3 passed in 0.01s

$ uv run ruff format --check .
2 files already formatted

$ uv run ruff check .
All checks passed!

$ uv run pyright
0 errors, 0 warnings, 0 informations

$ uv run pytest
3 passed in 0.02s

$ uv run ruff --version ; uv run pyright --version ; uv run pytest --version
ruff 0.16.8
pyright 1.1.414
pytest 9.1.1
```

The dependency resolution is unchanged, `uv.lock` was not touched, and the
three tests added in M00-T04 still pass, including
`test_package_import_has_no_output`, which guards the invariant above.

## Reflection

- **What complexity did this task hide from its caller?** A caller now runs one
  gate command and learns nothing about rule families, severity, path scope, or
  the split of labor between formatter and linter. That is the intended trade:
  the policy is reviewable in one file instead of re-derived from flags at each
  call site. The cost is that a rule silently widened by a later edit would not
  be noticed by anyone running the command.
- **Which alternative would make the next change harder, and why?** Passing
  rules as command-line flags, or leaving them unset and configuring only CI,
  would let the editor and the gate disagree; developers then learn to ignore
  squiggles. The opposite extreme — selecting every family and suppressing what
  hurts — would push the real policy into `# noqa` comments spread across files,
  which is harder to review than one list in `pyproject.toml`.
- **What production failure would escape if the negative test were removed?**
  A blocking call inside an `async` request handler. Pytest passed on the
  planted violation, so the suite alone would not notice; in production the
  event loop would stall and every concurrent request would queue behind it,
  presenting as intermittent latency rather than an error. A dropped
  `asyncio.create_task` reference (`RUF006`) is the same shape: correct in a
  short test, garbage-collected under load.

## Confirmations

- The public package contract gained no provider-specific, vendor, or
  framework-specific leakage; `knowledge_service` still exports only its
  module docstring.
- No secret, credential, private document, or production content entered the
  configuration, the test, or this record.
- The only executable code touched is `tests/test_package.py`, and its behavior
  is unchanged except that one assertion is now strictly narrower.
