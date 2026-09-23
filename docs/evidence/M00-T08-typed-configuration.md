# M00-T08 — Typed configuration evidence

Completed: 2026-09-23

## Invariant

Configuration is validated once at the application boundary without weakening
the deterministic, offline, credential-free development loop. Local execution
has safe defaults, production refuses to start without its required credential,
and secret values do not appear in settings representations or repository
artifacts.

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | Added `pydantic-settings` as a runtime dependency |
| `uv.lock` | Locked Pydantic Settings and its transitive dependencies |
| `src/knowledge_service/settings.py` | Added typed environment, log-level, and model credential settings with production validation |
| `.env.example` | Documented safe local environment names with an empty secret placeholder |
| `.gitignore` | Continued ignoring `.env` variants while explicitly allowing `.env.example` |
| `tests/test_settings.py` | Added deterministic tests for defaults, environment overrides, missing production credentials, and secret redaction |

## Configuration contract

- `environment` is one of `local`, `test`, or `production` and defaults to
  `local`.
- `log_level` is restricted to `DEBUG`, `INFO`, `WARNING`, or `ERROR` and
  defaults to `INFO`.
- `model_api_key` is represented by `SecretStr` and defaults to absent for
  local and test use.
- Production configuration without `model_api_key` fails validation with a
  clear error.
- Environment variables use the `KNOWLEDGE_SERVICE_` prefix. Empty values in
  `.env` are ignored.

The test fixture removes inherited `KNOWLEDGE_SERVICE_*` variables and changes
to an empty temporary directory before each test. This prevents a developer's
process environment or repository `.env` file from changing test outcomes.

## Verification

Commands used `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the default user
cache is outside the writable workspace.

The narrow suite was deliberately run with hostile inherited settings to prove
test isolation:

```text
$ KNOWLEDGE_SERVICE_ENVIRONMENT=production \
  KNOWLEDGE_SERVICE_LOG_LEVEL=INVALID \
  KNOWLEDGE_SERVICE_MODEL_API_KEY=<REDACTED> \
  uv run pytest tests/test_settings.py
4 passed in 0.11s
```

The aggregate developer gate then passed:

```text
$ make check
5 files already formatted
All checks passed!
0 errors, 0 warnings, 0 informations
9 passed, 1 deselected in 0.10s
```

Coverage reported 18 statements, 2 branches, and 100% coverage for the current
package. The `live` guard test remained deselected.

## Reflection

- **What complexity did this task hide from its caller?** Callers receive one
  typed settings object instead of parsing strings, selecting defaults, loading
  `.env`, and remembering which deployment modes require credentials.
- **Which alternative would make the next change harder, and why?** Reading
  `os.environ` throughout adapters and workflows would duplicate names,
  conversion rules, and validation. Central validation keeps those decisions at
  one boundary.
- **What production failure would escape if the negative test were removed?**
  A production process could start without its model credential and fail later
  during a request. The negative test proves startup rejects that configuration
  immediately and clearly.

## Confirmations

- `.env.example` contains no credential; the secret field is blank.
- `.env` and `.env.*` remain ignored, while `.env.example` is available to
  commit.
- No provider-specific SDK or domain dependency was introduced.
- Secret redaction is asserted against the complete `Settings` representation.
- All existing package, pytest-loop, formatting, linting, typing, and test gates
  remain green.
