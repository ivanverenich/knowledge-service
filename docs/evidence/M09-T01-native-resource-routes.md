# M09-T01 — Native resource routes evidence

## Invariant

The native API has one design, written down once and checked. Every route
the service serves or plans under `/v1` is a row of `api_routes.ROUTES`, with
the Operation an Application Role must permit, the task that builds it, and
whether it pages; `route_problems` finds no rule broken, and the app serves
exactly the served rows. Every route checks identity, then role, then input.
Every list pages by cursor with one shape, every error has the one
`ErrorResponse` shape, every path ID is a UUID named after its list, and no
native route or schema uses a model provider's field names (ADR 0004).

## Owners

- The design: `src/knowledge_service/api_routes.py` (`ROUTES`,
  `route_problems`).
- Holding the app to it: `tests/test_api_routes.py` (table versus app,
  OpenAPI) and the role matrix in `tests/test_app_authentication.py`, which
  now takes its routes from `served_routes()`.
- Authorization: each route's own `require(...)` call, before any input is
  read.
- Page sizes and cursors: `src/knowledge_service/pagination.py`; the decision
  list's position and keyset query: `job_decisions` and
  `persistence/job_recovery.py`.
- Retries: none added; a page request is a read.

## Expected failing check

Before the change, `uv run pytest tests/test_api_routes.py -q` reports
`no tests ran`, because the file does not exist.
Observed on 2026-11-25 at `e9d7bb5`: `no tests ran in 0.70s`.

## Predictions

Claude wrote these while preparing the lesson, from the prototype; they are
not blind predictions by the learner.

- If `next_cursor` is set when `len(jobs) >= limit` instead of `>`, which test
  fails: only `test_a_full_last_page_says_no_page_follows`, since 4 Jobs read
  2 at a time then claim a third, empty page (observed, see Verification).
- Which status does a caller without the `administrator` role get for
  `GET /v1/job-decisions?limit=0`: 403, not 422, because the role is checked
  before `limit` is read.
- What does `POST /v1/answer` answer after the change: 404 `not_found` in the
  `ErrorResponse` shape, not FastAPI's `{"detail": ...}`.

## Route table

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

24 rows: 6 served, 18 planned.

| Area | Routes | Status | Task |
|---|---|---|---|
| Questions | `POST /v1/questions` | served | M09-T01 (renamed from `POST /v1/answer/stream`) |
| Conversations | `GET /v1/conversations`, `GET` and `DELETE /v1/conversations/{conversation_id}`, `GET .../messages` | `DELETE` served (M07-T16), the rest planned | none yet |
| Feedback | `PUT` and `DELETE .../messages/{message_id}/feedback` | planned | M09-T09 |
| Diagnostics | `GET .../messages/{message_id}/diagnostics` | planned | none yet |
| Sources | `GET` and `POST /v1/sources`, `GET` and `PATCH /v1/sources/{source_id}`, `POST .../enable`, `POST .../disable` | planned | M09-T02 |
| Synchronization | `POST` and `GET /v1/sources/{source_id}/synchronizations`; `GET /v1/jobs/{job_id}`, `GET /v1/job-decisions`, `POST /v1/jobs/{job_id}/retry`, `POST /v1/jobs/{job_id}/cancel` | the first two planned, the Job routes served | M09-T03; M08-T07, M08-T08 |
| Evaluation | `GET /v1/evaluation-datasets`, `POST` and `GET /v1/evaluation-runs`, `GET /v1/evaluation-runs/{evaluation_run_id}` | planned | M10-T01, M10-T11 |

`POST /v1/answer`, the ungrounded M01 route, was removed: it sent the
Question straight to the model with no retrieval, Citations, or output
limit. Its error code `workflow_unavailable` became `answering_unavailable`.

## Paging design

A page is `{"items": [...], "next_cursor": ...}`, with `limit` from 1 to 100
(50 by default). The decision list is ordered by `(finished_at, job_id)`
newest first, and the next page asks for rows with
`(finished_at, job_id) < (a, b)` of the page's last Job, fetching `limit + 1`
rows to learn whether another page follows.

Why not an offset: the list changes while an operator reads it. A Job that
fails between two requests shifts every row by one, so `?page=2` repeats a
Job; a Job retried between two requests disappears, so `?page=2` skips one.
A position after the last Job seen does not move.
`test_pages_of_decisions_never_repeat_or_skip_a_job` (integration) adds a Job
between pages and includes Jobs that finished in the same microsecond.

The cursor is base64url of `[1, listing, position]`, at most 512 characters,
and unsigned: it only says where a list starts that the caller may read whole
anyway. The server accepts only the exact text it would have written:
another version, list, key, key order, spacing, or base64 spelling is a 422.

## Design

Changed files:

- `src/knowledge_service/api_routes.py` (new): `Route`, `ROUTES`,
  `served_routes`, `route_problems`.
- `src/knowledge_service/pagination.py` (new): `page_size`, `encode_cursor`,
  `decode_cursor`, `InvalidPage`.
- `src/knowledge_service/app.py`: `/v1/answer` removed, `/v1/questions`,
  `page_request`, the paged decision list, one handler for 404 and 405,
  `redirect_slashes=False`, `error_responses` documenting every error status
  with `ErrorResponse`, UUID path IDs, the Question body in OpenAPI.
- `src/knowledge_service/contracts.py`: `JobsNeedingDecision` becomes a page,
  `items` and `next_cursor`.
- `src/knowledge_service/job_decisions.py`: `decision_cursor`,
  `decision_position`.
- `src/knowledge_service/persistence/job_recovery.py`: the keyset query.
- Tests: `tests/test_api_routes.py` (45), `tests/test_pagination.py` (70), one
  integration test in `tests/test_job_recovery_integration.py`; every test
  that called `/v1/answer/stream` now calls `/v1/questions`; `tests/test_app.py`
  keeps only the health tests; `tests/test_app_authentication.py` reads its
  routes from the table and checks the old answer routes are gone.
- `docs/architecture.md`, `docs/threat-model.md`: the Question route, the
  route table and page rules, and the unsigned cursor as a known gap.

Replay and cancellation: a page request is a read, so repeating it is safe;
a cursor names a position, not a snapshot, so a repeated request sees the
list as it is now. This change adds no network, model, or paid call.

## Verification

Narrow checks (2026-11-25):
`uv run pytest tests/test_api_routes.py tests/test_pagination.py -q`:
115 passed.

Deliberate break (guide Step 9): with `if len(jobs) > limit` changed to
`>=` in `app.py`, run with `PYTHONDONTWRITEBYTECODE=1`,
`tests/test_pagination.py` gave `1 failed, 69 passed`
(`test_a_full_last_page_says_no_page_follows`). Restored: 70 passed.
Moving the `limit` and `cursor` reading above the role check failed 6 tests
(`test_the_role_is_checked_before_limit_or_cursor_is_read`); the old
`limit` rule `[0-9]{1,3}` failed `test_any_other_page_size_is_refused[007]`.

Gates (2026-11-25), one after another on one test database:
`uv run ruff format --check .` (363 files already formatted),
`uv run ruff check .` (all checks passed), `uv run pyright` (0 errors),
`make docs-check` (exit 0), `make test-unit` (3385 passed),
`make test-integration` (910 passed), `git diff --check` (clean),
`uv run pre-commit run --all-files` (all passed).
One earlier unit run failed the timing test
`test_many_links_parse_in_linear_time` once under load; it passed on its own
twice and in the full rerun, and this change touches neither the parser nor
that test.

Security and privacy: a bad `limit`, `cursor`, or unknown query parameter is
answered only after identity and role, and only with the code
`validation_error`, never the reason. Unknown paths, methods, and trailing
slashes answer 404 or 405 in the `ErrorResponse` shape, with no redirect.
No new log line.

## Review

While preparing the lesson, an adversarial review of the prototype found
these, each fixed with a test that fails without the fix: 404, 405, and 307
answers bypassed `ErrorResponse`; a full last page claimed another page; a
paging loop could hang on an ignored cursor; the OpenAPI tests missed a
dropped 404, an integer `id`, and provider fields; the route rules accepted
singular lists, verbs, repeated IDs, and `/v1/feedback` at the root, and
refused `entries`; one cursor had several spellings and no list tag; unknown
query parameters were ignored.

At completion, a Standards review and a Spec review ran independently. Both
found that nothing proved the role comes before `limit` and `cursor`; the
Spec reviewer showed it by moving the reading above the role check, with 248
tests still passing. Fixed with
`test_the_role_is_checked_before_limit_or_cursor_is_read` (no token: 401;
`user` role: 403; the store never asked). Also fixed: `limit` accepted `007`;
a comment gave a false reason for reading the page parameters by hand; one
`except` clause named three spellings of `ValueError`; `docs/architecture.md`
did not say paginated routes refuse other query parameters;
`docs/threat-model.md` still said the decision list's checks came "before the
ID is parsed". Both lesson guides were updated to match.

## Known limits

- Irregular plurals such as `analyses` have no rule.
- An unknown path answers 404 before the identity check.
- The SSE events of `POST /v1/questions` are not in OpenAPI (M09-T11).
- `workflow.py`, `openai_model.py`, and `contracts.Answer` are now unused;
  M09-T05 decides their fate.
- Only `job-decisions` pages today; the planned lists must page when built.
- Only paginated routes refuse unknown query parameters.

## What would falsify the design

- A client that must call two lists in two shapes, or guess an ID's name.
- A served route that no row describes, or a row whose Operation differs
  from the route's `require` call, without a test failing.
- A page that repeats or skips a Job while Jobs are added or retried between
  requests.
- A caller without the role who learns the input rules from a 422.
- The compatible adapter (M09-T05) needing a native schema to change shape.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

The table is plain Python on purpose: the design changes only together with
code, so the row and the route arrive in one diff and one review, and the
check runs in milliseconds without a database. Industry tools do the same job
on the OpenAPI document (style guides with a linter such as Spectral,
contract tests such as Schemathesis); the table also knows what OpenAPI does
not, the Operation and the building task. The tests do not judge whether a
name is well chosen, and someone can change a rule and its test together;
then the change is at least visible in the diff.
