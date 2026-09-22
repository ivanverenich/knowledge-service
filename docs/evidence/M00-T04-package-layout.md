# M00-T04 — Initial package layout evidence

Completed: 2026-09-22

## Invariant

Importing `knowledge_service` from the locked project environment must succeed
without printing, starting services, reading configuration, or contacting an
external system.

## Layout decision

```text
src/
└── knowledge_service/
    └── __init__.py
tests/
└── test_package.py
```

The `src` layout keeps the importable package separate from repository-level
files and makes tests exercise the installed project rather than an accidental
repository-root module. The test directory remains outside the package because
tests are development assets, not application runtime code.

No feature directories were added. Modules such as retrieval, ingestion, and
transport should be introduced only when a task supplies behavior and an
interface worth protecting. Empty folders would imply architecture without
invariants to enforce.

## Interface tests

- `test_package_is_importable` verifies the successful import case.
- `test_package_import_has_no_output` verifies the side-effect boundary.
- `test_unknown_package_module_fails_with_typed_import_error` verifies that an
  unsupported package module fails explicitly with `ModuleNotFoundError`,
  rather than silently resolving to speculative structure.

The third test is intentionally an import-boundary failure. This task does not
yet expose a behavior-bearing application function with domain validation, so
introducing a custom exception or fake domain input would expand the scope.

## Reflection

- The package hides repository layout details from callers: callers import the
  stable `knowledge_service` package name, while the source location remains
  an internal project choice.
- Adding future-facing feature folders now would make later boundaries harder
  to choose because empty structure would be mistaken for an established
  design.
- Without the negative import test, an accidental module or speculative package
  path could appear supported until a later integration exposed the mistake.

## Verification

Commands were run with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the
default user cache is outside the writable workspace.

```text
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_package.py
3 passed

UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff format --check .
285 files already formatted

UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff check .
All checks passed!

UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright
0 errors, 0 warnings, 0 informations

UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest
3 passed
```

The package and tests contain no provider-specific contract, secret, private
content, or speculative domain implementation.
