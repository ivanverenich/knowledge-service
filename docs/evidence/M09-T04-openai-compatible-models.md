# M09-T04 — OpenAI-compatible models evidence

## Invariant

`GET /openai/v1/models` lists exactly one model, the configured virtual
knowledge model (`knowledge-base`, "Knowledge base" by default), with a fixed
`created` (1795564800) and `owned_by` (`knowledge-service`), so the body is
the same byte for byte across restarts. Only a caller with a valid bearer
token and the `ask_questions` operation (the `user` role) sees it. Every
refusal under `/openai/v1`, unknown paths and methods included, has OpenAI's
error shape `{"error": {...}}` with the native code and message, the right
challenge or `Retry-After`, and the request ID as `X-Request-ID`. The native
API, its error shape, and its OpenAPI document are unchanged.

## Owners

- The virtual model, its rules, and OpenAI's shapes:
  `src/knowledge_service/compatibility/models.py` (`CompatibleModel`,
  `model_list`, `compatible_error`).
- Configuration and the start-up check: `Settings.compatible_model_id`,
  `Settings.compatible_model_name`, `Settings.check_compatible_model`.
- The route and the refusals: `src/knowledge_service/app.py`
  (`list_compatible_models`, `Refusal`, `not_authenticated_refusal`,
  `failure_refusal`, `FORBIDDEN`, `native_error`, `compatible_refusal`,
  `is_compatible`).
- Authentication and roles: the same `authenticate` and `require` as every
  native route (M07).

## Expected failing check

Before the change, `uv run pytest tests/test_compatible_models.py -q`
reported `no tests ran` with exit code 4, because the file did not exist.
Observed on 2026-11-27 at `35fca20`: `no tests ran in 0.13s`, exit 4.

## Predictions and what happened

| Prediction | Result |
|---|---|
| At the native `/v1/models`, the M09-T01 tests for provider field names, `limit`/`cursor` pagination, and the one native error shape would fail | Not tried: the adapter went to `/openai/v1`; the native route-table tests stay green and the role test only needed the new route added |
| Open WebUI would see no `data` and no `error` in the native 401 body, so it would show no models and no error; OpenAI's shape is read as an error | OpenAI's client raises `AuthenticationError` / `PermissionDeniedError` with the native code |
| With `time.time()`, `created` would change on every restart | It is a constant; two app instances give byte-identical bodies |
| Adding the route breaks the role-table test until the route is listed | Exactly one failure: `test_every_route_the_app_serves_is_listed_with_its_operation` |

## Why `/openai/v1`

An OpenAI client joins its base URL with `/models`, so the path only has to
end in the base URL it is given (`http://api:8000/openai/v1`). Inside the
native `/v1` the route would break the M09-T01 rules (no provider field
names, cursor pages, one error shape) and ADR 0004. The route is
`include_in_schema=False`, so no `/openai` path or `ModelList` schema appears
in the native OpenAPI document.

## Refusals

| Situation | Status | `error.code` | `error.type` | Header |
|---|---|---|---|---|
| no token, unknown token, other scheme | 401 | `unauthenticated` | `authentication_error` | `WWW-Authenticate: Bearer` or `Bearer error="invalid_token"` |
| malformed `Authorization` | 400 | `invalid_request` | `invalid_request_error` | `WWW-Authenticate: Bearer error="invalid_request"` |
| no `user` role | 403 | `forbidden` | `permission_error` | none |
| tokens cannot be checked | 503 | `dependency_unavailable` | `server_error` | `Retry-After: 5` |
| unknown path / method under `/openai/v1` | 404 / 405 | `not_found` / `method_not_allowed` | `invalid_request_error` | `Allow` for 405 |

Every refusal also carries `X-Request-ID`. Messages are fixed, so no token
text reaches the body, headers, or log (tested with a valid token from an
untrusted client).

## Deliberate break

Removed `require(caller.roles, Operation.ASK_QUESTIONS)` from
`list_compatible_models` and ran with bytecode off:

- `tests/test_compatible_models.py`: `2 failed, 35 passed` —
  `test_each_refusal_is_an_openai_error_with_the_native_code[no user role]`
  and
  `test_openais_client_raises_its_own_error_for_each_refusal[staff-token-PermissionDeniedError]`.
- `tests/test_app_authentication.py`: `8 failed, 279 passed` — every role set
  without `user` now got 200.

Restored: both files pass (`324 passed`).

## Gates

| Gate | Result |
|---|---|
| `uv run ruff format --check .` | 375 files already formatted |
| `uv run ruff check .` | All checks passed |
| `uv run pyright` | 0 errors |
| `make docs-check` | exit 0 |
| `make test-unit` | 3734 passed |
| `make test-integration` | 952 passed (Keycloak and Redis up) |
| `tests/test_compatible_models.py` | 37 passed |
| `tests/test_compatible_models_integration.py` | 4 passed with real Keycloak tokens for alice, bob, carol, and a tampered token refused |
| `git diff --cached --check` | clean |
| `uv run pre-commit run` | every hook passed on the staged files |

## Bounds and replay

The route makes no external call of its own; the only one is the identity
provider's key fetch, already bounded by M07. Nothing is stored, so there is
nothing to replay.

## Review

At completion two independent reviewers checked the staged change against
`35fca20`: one for repository standards, one for the task and lesson. Neither
found a blocking issue. The native refusals were compared handler by handler
(401/400 challenges, 403, `Retry-After`, `Allow` on 405) and are unchanged.
Non-blocking observations, recorded as known limits below.

## Known limits

- Only the list is served; `GET /openai/v1/models/{id}` is an OpenAI-shaped 404.
- No production entry point builds the app yet, so a configured
  `COMPATIBLE_MODEL_ID`/`COMPATIBLE_MODEL_NAME` is validated at start-up but
  reaches the route only in tests; the composition root will pass
  `settings.compatible_model()`.
- Only the 404/405 handler picks the shape by prefix; 413 and 422 under
  `/openai/v1` would still be native. Harmless for a GET-only adapter, to be
  handled when M09-T05 adds `POST /chat/completions`.
- `settings.py` now imports `compatibility.models`, which imports FastAPI, so
  any process loading `Settings` (the worker too) loads FastAPI.
- `FORBIDDEN` is a frozen dataclass with a mutable `headers` dict; no code
  mutates it.
- The prefix is case-sensitive; `/openai` without `/v1` gets the native 404.

## What would falsify the design

An OpenAI client that cannot read the list; a model whose ID or `created`
changes between restarts; a caller without the `user` role who sees the
model; a refusal under `/openai/v1` in the native shape or quoting token
text; an OpenAI-shaped path or schema in the native OpenAPI document.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

The adapter is a translator at the door, not a new feature: the same
authentication and role check run, and only the shape of the answer changes.
The tricky part was that FastAPI's exception handlers belong to the whole
app, so the route had to catch its own refusals, and sharing `Refusal` values
kept one source of truth for status, code, message, and headers between both
shapes. The model's stability is the real contract with Open WebUI: a saved
chat remembers the model ID, so the ID and `created` must never move, and the
ID names "the knowledge base", not the language model behind it.
