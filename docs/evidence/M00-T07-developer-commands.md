# M00-T07 — Developer command entrypoints evidence

Completed: 2026-09-23

## Invariant

The developer commands remain thin wrappers over the configuration established
in `pyproject.toml`. The default and aggregate test paths stay deterministic,
offline, and credential-free: neither selects tests marked `live`.

## What changed

| File | Change |
|---|---|
| `Makefile` | Added setup, formatting, linting, type-checking, unit-test, integration-test, and aggregate-check entrypoints |
| `README.md` | Documented each developer command and the required cache override for this environment |

The Makefile does not duplicate Ruff, Pyright, pytest, or coverage policy. Each
target invokes the tool that owns the corresponding configuration.

## Command contract

| Target | Command behavior |
|---|---|
| `setup` | Synchronizes the locked development environment |
| `format` | Applies Ruff formatting |
| `format-check` | Checks formatting without modifying files |
| `lint` | Runs Ruff lint rules |
| `typecheck` | Runs Pyright strict checking |
| `test-unit` | Selects tests that are neither `integration` nor `live` |
| `test-integration` | Selects `integration` tests while still excluding `live` |
| `check` | Runs formatting, linting, type checking, and all default non-live tests with coverage |

The explicit `and not live` clause on the integration target matters because a
future test may legitimately carry both `integration` and `live`. Such a test
must not make a real provider call through the normal integration entrypoint.

## Verification

Commands were run with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the
default user cache is outside the writable workspace.

```text
$ make setup
Resolved 15 packages in 22ms
Checked 14 packages in 5ms

$ make format
3 files left unchanged

$ make format-check
3 files already formatted

$ make lint
All checks passed!

$ make typecheck
0 errors, 0 warnings, 0 informations

$ make test-unit
4 passed, 2 deselected in 0.01s

$ make test-integration
1 passed, 5 deselected in 0.01s

$ make check
5 passed, 1 deselected in 0.02s
```

The aggregate check also reported clean Ruff formatting and linting, no Pyright
findings, and coverage configured for `knowledge_service`. Coverage currently
contains zero executable statements because the package still contains only its
module docstring.

An unknown target was used as a deterministic failure probe:

```text
$ make not-a-real-target
make: *** No rule to make target `not-a-real-target'. Stop.
exit=2
```

## Reflection

- **What complexity did this task hide from its caller?** A developer no longer
  needs to remember selection expressions, coverage flags, or the exact tool
  commands. The Makefile exposes stable names while the tools retain ownership
  of their detailed policy.
- **Which alternative would make the next change harder, and why?** Copying
  configuration into shell scripts or Make variables would create two policy
  sources. Thin commands allow later configuration changes to stay localized in
  `pyproject.toml`.
- **What production failure would escape if the negative test were removed?**
  Without checking a rejected target, a misspelled developer command could be
  mistaken for a successful no-op in automation. More importantly, explicitly
  excluding `live` from both test entrypoints prevents accidental provider calls
  with real credentials.

## Confirmations

- Commands are discoverable in the root `Makefile` and summarized in `README.md`.
- No runtime dependency was added.
- No provider-specific contract, secret, credential, or private content entered
  the implementation or evidence.
- The existing import boundary and all M00-T05/M00-T06 gates remain green.
