# M09-T01 — Design native resource routes

Task: [M09-T01 — Design native resource routes](../curriculum/milestones/M09-product-interfaces/M09-T01-design-native-resource-routes.md)

## What you will do

This lesson opens milestone M09, the product interfaces. You design the service's own HTTP API, the **native API**, before later lessons add Source administration, synchronization administration, feedback, and an OpenAI-compatible adapter for Open WebUI. The design becomes code: one table of every route, with the rules each route must follow, and tests that hold the app and its OpenAPI document to that table.

A concrete example of what changes. Today an administrator who asks for the Jobs waiting for a decision gets up to 100 of them in one list called `jobs`, and nothing more. After this lesson the same request reads like every list the API will ever have:

```text
GET /v1/job-decisions?limit=2
-> {"items": [job A, job B], "next_cursor": "WzEsImpvYi1kZWNpc2lvbnMi..."}

GET /v1/job-decisions?limit=2&cursor=WzEsImpvYi1kZWNpc2lvbnMi...
-> {"items": [job C], "next_cursor": null}
```

The same lesson removes `POST /v1/answer`, an early route that sends a Question straight to the model without any search, and gives the real answering route its resource name, `POST /v1/questions`.

| What the task asks | Where it is done |
|---|---|
| Specify versioned routes for Questions, Conversations, Sources, synchronization, evaluation, feedback, and diagnostics, with role requirements | Block 1; Step 2 |
| OpenAPI shows consistent IDs | Blocks 1 and 4; Steps 6 and 8 |
| OpenAPI shows consistent pagination | Block 3; Steps 3 to 6 and 8 |
| OpenAPI shows consistent errors | Block 5; Steps 6 and 8 |
| No provider-shaped domain models | Block 6; Step 8 |
| Success, boundary, and typed failure or denial cases; real infrastructure where its behaviour is under test | Steps 8 and 9 (PostgreSQL for the paging order) |
| Replay or cancellation explicit, external calls bounded | Block 3; Step 11 |
| Explain what would falsify the design | Step 9 and the reflection questions |
| Update only affected documentation | Step 10 |

This lesson includes coding: Steps 2 to 8 change code and tests. Steps 1, 9, 10, and 11 need no code, although Step 10 edits two documents and Step 9 makes a temporary one-line change that you undo.

Terms used in this lesson:

- **Route:** one HTTP method on one path, such as `GET /v1/jobs/{job_id}`. The app answers it with one function.
- **Resource:** the thing a path names, written as a plural noun: `sources`, `conversations`, `jobs`. A path such as `/v1/sources/{source_id}` names one item of that list.
- **Versioned route:** a path that starts with `/v1`. A later incompatible design would live under `/v2`, so clients of `/v1` keep working.
- **Operation:** an action an Application Role may permit, such as `configure_sources` (M07-T05). Every `/v1` route needs exactly one.
- **OpenAPI document:** the machine-readable description FastAPI builds from the routes, served at `/openapi.json`. Client generators and the API reference (M09-T11) read it.
- **Pagination:** giving a long list in pages. `limit` says how many items a page holds; a **cursor** says where the next page starts.
- **Keyset pagination:** starting the next page after the last item's sort key, such as "after the Job that failed at 09:02 with ID 01a2...". It is the opposite of an offset such as "skip the first 100".
- **Provider-shaped model:** a request or response shaped like a model provider's protocol, for example OpenAI's `choices`, `messages`, and `role`. The native API keeps its own words: Question, Answer, Citation.

## Before you begin

This section explains every choice the lesson's code depends on. Each block shows a real situation in this project, what happens today, the fact underneath it, and the fix. Read it first; the steps assume it.

### 1. One table holds the design, and tests hold the app to it

Today each route is written in two places that nobody compares: once in `app.py`, and once by hand in a test dictionary that says which Operation it needs (`tests/test_app_authentication.py`, from M07-T05). The routes that do not exist yet, such as Source administration, are written nowhere. The next lessons each add routes, so here is what happens without one design.

| Lesson | Route it might add | What goes wrong |
|---|---|---|
| M09-T02 | `GET /v1/source/{id}` | singular `source`, and a bare `{id}` |
| M09-T03 | `GET /v1/sources/{source_id}/runs?page=2` | a different list style: page numbers |
| M09-T09 | `POST /v1/rate_answer` | a verb with `_` instead of a resource |
| M10 | `GET /v1/evaluation-runs` returning `{"runs": [...]}` | yet another list shape |

Each route works alone. Together they make an API a client cannot guess: every list has its own shape, and every ID its own name.

The hidden basic fact: FastAPI checks nothing about a route's name, its ID's name, or its response shape. Any path string is accepted. Consistency exists only if something checks for it.

The fix is a table in code, `src/knowledge_service/api_routes.py`. Each row is one route: method, path, Operation, whether it is served or planned, the task that builds it, a one-line summary, and whether it pages. A function, `route_problems`, checks every row against the rules:

| Rule | Good | Refused |
|---|---|---|
| Every path starts with `/v1/` | `/v1/sources` | `/sources`, `/v2/sources` |
| A path word is lowercase, joined by `-` | `evaluation-runs` | `Sources`, `source_list` |
| A word is a plural list, unless it is one of a few named words that end a path after an ID | `sources`, `/{message_id}/feedback`, `/{job_id}/retry` | `source`, `getsourcenow`, `/v1/feedback` |
| An ID is named after the list before it | `/v1/sources/{source_id}`, `/v1/entries/{entry_id}` | `/v1/sources/{id}`, `/v1/{source_id}` |
| An action such as `retry` is only a POST | `POST /v1/jobs/{job_id}/retry` | `GET /v1/jobs/{job_id}/retry` |
| An ID appears once | `/v1/sources/{source_id}` | `/v1/sources/{source_id}/sources/{source_id}` |
| Every GET of a list pages, and nothing else does | `GET /v1/sources` paginated | `GET /v1/sources` not paginated |
| A served route names the task that built it | `"M08-T07"` | `None` |

Then two tests tie the app to the table. One compares the routes the app really serves under `/v1` with the table's served rows. The other is the existing role test from M07-T05, which now reads its routes from the table instead of a hand-written dictionary. A new route in `app.py` that is missing from the table fails the first test; a row whose Operation is wrong fails the second.

The table has 24 rows: 6 served and 18 planned. The planned rows are the design the next lessons follow.

| Area | Served now | Planned |
|---|---|---|
| Questions | `POST /v1/questions` | |
| Conversations | `DELETE /v1/conversations/{conversation_id}` | list, read one, list its messages |
| Feedback | | `PUT` and `DELETE .../messages/{message_id}/feedback` (M09-T09) |
| Diagnostics | | `GET .../messages/{message_id}/diagnostics` (no task yet) |
| Sources | | list, register, read, change, enable, disable (M09-T02) |
| Synchronization | `GET /v1/jobs/{job_id}` | start and list `/v1/sources/{source_id}/synchronizations` (M09-T03) |
| Jobs needing a decision | `GET /v1/job-decisions`, `POST .../retry`, `POST .../cancel` | |
| Evaluation | | datasets (M10-T01), runs (M10-T11) |

An everyday analogy: a building's floor plan. Each builder adds a room, but the plan says where doors go and how rooms are numbered. An inspector compares the building with the plan.

### 2. The ungrounded route goes, and the answering route gets its resource name

The service has two routes that take a Question.

| Route | What it does |
|---|---|
| `POST /v1/answer` (M01) | sends the Question straight to the model: no search, no Evidence, no Citations, no output limit, and it does not ask the provider not to keep the text |
| `POST /v1/answer/stream` (M06) | the grounded path: searches as the reader, checks Citations, may abstain, and streams the Answer |

The threat model (M07-T01) lists the first one as a known gap that "the versioned Question route replaces (M09-T01)". This is that lesson.

The hidden basic fact: a route is reachable by anyone who has a token with the `user` role, whether or not any client uses it. Leaving the old route in place leaves the cost and privacy gap in place.

How bad it is today: small, because no client calls it. It would get worse: M09-T05 adapts OpenAI chat completions, and an adapter wired to the wrong workflow would answer without Evidence.

The fix:

| Was | Becomes |
|---|---|
| `POST /v1/answer` | removed; the `workflow` argument of `create_app` goes with it |
| `POST /v1/answer/stream` | `POST /v1/questions`, the same function, renamed `ask_question` |
| code `workflow_unavailable` when nothing can answer | `answering_unavailable` |

The name follows rule 1: the resource is a Question, so the client posts one to the list of Questions, and the response streams its Answer. `workflow.py`, `openai_model.py`, and `contracts.Answer` stay in the code for now, used only by their own tests; M09-T05 decides whether its adapter reuses them.

### 3. Pages start after the last item, not after a count

The decision list is ordered newest failure first. Suppose an administrator reads it two Jobs at a time with an offset, the common first idea:

| Moment | Administrator | The list in PostgreSQL, newest first | What the page shows |
|---|---|---|---|
| 10:00 | asks for page 1: skip 0, take 2 | C, B, A | C, B |
| 10:01 | | Job D fails: D, C, B, A | |
| 10:02 | asks for page 2: skip 2, take 2 | D, C, B, A | B, A |

B appears twice. If a Job had been retried instead, leaving the list, one Job would have been skipped and never seen.

The hidden basic fact: an offset counts rows at the moment of each request. Each page is a new query on a list that may have changed, so "the third row" is a different Job each time.

The fix is keyset pagination. The page remembers its last Job's sort key, `(finished_at, job_id)`, and the next query asks for Jobs strictly before that key:

```sql
-- page 2, after the last Job of page 1
SELECT ... FROM jobs
WHERE status = 'failed' AND retry_at IS NULL AND ...
  AND (finished_at, job_id) < ('2026-11-24 09:02:00+00', '01a2...')
ORDER BY finished_at DESC, job_id DESC
LIMIT 3;
```

PostgreSQL compares the two pairs left to right, the way words are sorted: first by `finished_at`, and only when two times are equal, by `job_id`. So Jobs that failed in the same microsecond still have one fixed order, and D, which failed later, sorts before page 1 and never shifts page 2.

| Moment | Administrator | The list | What the page shows |
|---|---|---|---|
| 10:00 | page 1: the first 2 | C, B, A | C, B; the cursor remembers B |
| 10:01 | | D fails: D, C, B, A | |
| 10:02 | page 2: after B | D, C, B, A | A; no cursor, the list ended |

`LIMIT 3` asks for one Job more than the page holds. If it comes back, another page follows, and `next_cursor` names the page's last Job; if not, `next_cursor` is `null`. Without that extra row, a page of exactly 2 Jobs could not tell "there are more" from "that was the end".

The cursor travels through the client, so it must be safe to give out and safe to take back:

| Choice | Why |
|---|---|
| base64url of a small JSON list: `[1, "job-decisions", {"finished_at": ..., "job_id": ...}]` | fits in a URL; clients treat it as opaque, so its contents can change later |
| `1` is a version | a future format can refuse old cursors cleanly |
| `"job-decisions"` names the list | a cursor of one list never works on another list with the same keys |
| decoding refuses anything `encode_cursor` would not write, including other spellings of the same content | there is exactly one valid cursor per position, so nothing can be smuggled through key order, spaces, or spare base64 bits |
| not signed | it only says where a list starts that the caller may read whole anyway; a forged cursor shows nothing new |
| at most 512 characters | decoding work and memory stay small whatever a client sends |

Replay and cancellation are simple here. Reading a page changes nothing, so a repeated or cancelled request has no effect; the same cursor sent twice gives the page that follows the same Job.

An everyday analogy: a bookmark in a book that gets new pages glued in at the front. "Page 37" moves; a bookmark after a sentence does not.

### 4. Who, then may they, then what: also for query and path

Every route since M07 checks in a fixed order: who the caller is (401), whether their role permits the operation (403), and only then their input (422). A caller without permission learns nothing about the input rules.

FastAPI breaks this order if you let it validate. Declare `limit: int = Query(ge=1, le=100)`, and FastAPI checks it before your function runs:

| Request | With a FastAPI `Query` | Required |
|---|---|---|
| no token, `?limit=0` | 422 | 401 |
| a `user` without `recover_jobs`, `?limit=0` | 422 | 403 |

The hidden basic fact: FastAPI validates declared parameters before calling the route function, and therefore before any code inside it, including the identity check.

So the routes read `limit` and `cursor` themselves, after `require`, through `page_request`. OpenAPI still needs to describe them, so they are written into the document by hand with `openapi_extra`. The same applies to path IDs. They stay plain text for FastAPI, so it never refuses them first; the route checks their spelling after the role. For the document, a small annotation marks them as UUIDs:

```python
# Was: a path ID documented as any string
async def read_synchronization_job(request: Request, job_id: str) -> ...

# Becomes: the same text to FastAPI, a UUID in OpenAPI
Identifier = Annotated[str, Path(json_schema_extra={"format": "uuid"})]
async def read_synchronization_job(request: Request, job_id: Identifier) -> ...
```

`json_schema_extra` only adds `"format": "uuid"` to the document. FastAPI does not enforce it for a `str`, which the prototype checked: `/v1/x/notauuid` still reaches the function.

`page_request` also refuses a parameter it does not know, and a parameter given twice. Without that, an administrator who types `?Limit=5` gets the default 50 Jobs and never learns the parameter was ignored.

### 5. One error shape for every answer under /v1

The project's errors have one shape since M01: `{"code": ..., "message": ..., "request_id": ...}`. The adversarial review of this lesson's prototype found answers under `/v1` that bypass it:

| Request | Answer before the fix |
|---|---|
| `POST /v1/answer` after its removal | 404 `{"detail": "Not Found"}` |
| `GET /v1/questions` | 405 `{"detail": "Method Not Allowed"}` |
| `GET /v1/job-decisions/` | 307 redirect to the path without `/`, with no token checked |

The hidden basic fact: these answers come from Starlette, the framework under FastAPI, before any route function runs. They raise Starlette's own `HTTPException`, which the app's handlers did not cover.

There is a second gap in the document. FastAPI adds a 422 response with its own `HTTPValidationError` schema to every route that has parameters, but this project never sends that shape: its 422 is an `ErrorResponse`. A client generated from the document would expect the wrong body.

The fix has three parts:

| Part | What it does |
|---|---|
| `http_error_handler` for Starlette's `HTTPException` | 404 becomes `not_found`, 405 becomes `method_not_allowed`, in the one shape; the `Allow` header of a 405 is kept |
| `FastAPI(redirect_slashes=False)` | `/v1/job-decisions/` is a 404, not a redirect |
| `error_responses(...)` on every route | documents 400, 401, 403, 422, 500, and 503, plus each route's own statuses, all with `ErrorResponse`; a route that documents its own 422 makes FastAPI leave out `HTTPValidationError` |

A 404 for an unknown path still comes before identity. That is normal for an HTTP API and reveals only the path list, which the OpenAPI document publishes anyway.

### 6. No model provider's words in the native API

ADR 0004 decides that the native API exposes this service's ideas, Questions, Evidence, Citations, feedback, diagnostics, and that an OpenAI-compatible adapter translates for Open WebUI (M09-T04 to M09-T07). If a native schema starts using `messages`, `choices`, or `role: assistant`, the adapter has nothing to translate, and every native client is tied to one provider's protocol.

A test lists the provider fields that must never appear in any schema of the document, including the Question body: `choices`, `messages`, `finish_reason`, `object`, `delta`, `logprobs`, `role`, `content`, `model`, `id`. It also refuses schema names containing `openai`, `chat`, or `completion`. `id` is in the list for a second reason: every identifier in this API is named after its resource, such as `job_id`, and is a UUID.

### Other findings of the adversarial review

An independent review attacked the prototype before this guide was written. Besides blocks 4 and 5, it found these; each is fixed and has a test that fails without the fix:

- **A full last page claimed another page.** With `len(jobs) >= limit`, 4 Jobs read 2 at a time gave a third, empty page. The tests had only used 5 Jobs, never an exact multiple. Now the check is `>` and a test reads 4 Jobs in pages of 2.
- **An ignored cursor made a test hang.** A paging loop with `while True` ran forever when the cursor was ignored. Now every paging loop stops after 10 pages and fails.
- **The OpenAPI tests missed real changes.** Dropping a route's 404 from the document, adding an `id: int` field, or adding `role` and `content` fields all passed. Now each route's error statuses are compared exactly, every `id` field must be a UUID, and the provider list is longer.
- **The route rules accepted bad routes.** `GET /v1/source/{source_id}`, a repeated `{source_id}`, `/v1/feedback` at the root, and a verb like `getsourcenow` all passed. `/v1/entries/{entry_id}` was refused because `entries` minus `s` is `entrie`. Now every word must be a list, a single resource, or an action, and `-ies` becomes `-y`.
- **One cursor had several spellings.** Reordered keys, spaces, a repeated key, or 4 different last base64 letters decoded to the same position. Now decoding re-encodes the position and requires the exact same text.
- **Nothing proved the role comes before the page parameters.** Moving the `limit` and `cursor` reading above the role check left every test green, although a caller without the role would then learn the input rules from a 422. Now a test sends `?limit=0`, `?cursor=%%`, and `?Limit=5` with no token (401) and with the `user` role (403), and checks the store was never asked. The same review made `limit` refuse a second spelling such as `007`.

### Known limits

- Irregular plurals, such as `analyses`, have no rule; name such a list differently or add the rule with a test.
- A 404 for an unknown path is answered before identity, as in any HTTP API.
- The Question route's streamed events are not described in OpenAPI yet; M09-T11 documents them with examples.
- `workflow.py`, `openai_model.py`, and `contracts.Answer` are no longer served; M09-T05 reuses or removes them.
- Only `GET /v1/job-decisions` pages today. Every planned list follows the same `page_request`, cursor, and `{items, next_cursor}` shape.

## Step 1 — Read, predict, and see the check fail

No coding in this step.

This step fixes what you expect before you change anything, so the evidence can compare prediction and result.

- Read the evidence of M08-T07 and M08-T08, which built `GET /v1/jobs/{job_id}` and the decision routes, and M07-T05, which built the role test.
- Write down, for the evidence file: the invariant ("every `/v1` route is a served row of the route table, needs that row's Operation, and documents its errors with `ErrorResponse`; every list pages by cursor"), the owner of authorization (the route's `require`, before any input), and the owner of retries (the client: reading a page changes nothing).
- Predict: with 4 failed Jobs read 2 at a time, how many requests does the administrator make, and what is the second page's `next_cursor`? (Two requests; `null`.)

Then run the check that must fail first:

```bash
uv run pytest tests/test_api_routes.py -q
```

Expected: `ERROR: file or directory not found: tests/test_api_routes.py` and `no tests ran`, exit code 4.

## Step 2 — Write the route table

Coding step.

In plain words: this file is the API's floor plan. It lists every route the service answers or will answer, who may call each one, and which lesson builds it. It also holds the checker that says which rule a badly named route breaks. Nothing calls the network or the database here.

Create `src/knowledge_service/api_routes.py`:

| Part | What it holds |
|---|---|
| `Method`, `RouteStatus` | the HTTP methods the API uses; served or planned |
| `Route` | one row: method, path, Operation, status, task, summary, `paginated` |
| `RouteProblem` | a route and the rule it breaks, as text a test can compare |
| `SINGLE_RESOURCES`, `ACTIONS` | the only words allowed that are not plural lists; each ends a path after an ID |
| `ROUTES` | the 24 rows, grouped by area |
| `served_routes` | the rows the app must serve |
| `route_problems` | every broken rule of every row; empty means the design holds |

```python
"""The native HTTP API's routes: one table, and the rules every row follows.

Every route the service serves or plans is a row here, with the Operation an
Application Role must permit and the task that builds it. `route_problems`
checks the naming, identifier, and pagination rules; the tests check that the
app serves exactly the served rows, each behind its Operation.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.roles import Operation

API_PREFIX = "/v1"
# Words that end a path after an ID: one thing of that item, such as its
# feedback, and something done to it, such as a retry. Every other word must
# name a list, in the plural.
SINGLE_RESOURCES = frozenset({"feedback", "diagnostics"})
ACTIONS = frozenset({"enable", "disable", "retry", "cancel"})

_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
_PARAMETER = re.compile(r"\{([a-z_]+)\}")
_TASK = re.compile(r"M\d{2}-T\d{2}")


class Method(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RouteStatus(StrEnum):
    SERVED = "served"
    PLANNED = "planned"


@dataclass(frozen=True, slots=True)
class Route:
    """One route: who may call it, whether it is served, and who builds it.

    `task` is the curriculum task that built or builds it; None marks a
    planned route no task schedules yet. A `paginated` route takes `limit`
    and `cursor` and answers `{"items": [...], "next_cursor": ...}`.
    """

    method: Method
    path: str
    operation: Operation
    status: RouteStatus
    task: str | None
    summary: str
    paginated: bool = False


@dataclass(frozen=True, slots=True)
class RouteProblem:
    """A rule one route breaks."""

    route: Route
    rule: str


_GET, _POST, _PUT, _PATCH, _DELETE = Method
_SERVED, _PLANNED = RouteStatus.SERVED, RouteStatus.PLANNED
_MESSAGE = "/v1/conversations/{conversation_id}/messages/{message_id}"

ROUTES: tuple[Route, ...] = (
    # Questions
    Route(
        _POST,
        "/v1/questions",
        Operation.ASK_QUESTIONS,
        _SERVED,
        "M09-T01",
        "Ask a Question; the response streams its Answer",
    ),
    # Conversations, each only its owner's
    Route(
        _GET,
        "/v1/conversations",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List the caller's Conversations",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "Read one of the caller's Conversations",
    ),
    Route(
        _DELETE,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _SERVED,
        "M07-T16",
        "Delete one of the caller's Conversations",
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}/messages",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List a Conversation's Questions and Answers",
        paginated=True,
    ),
    # Feedback on one of the caller's Answers
    Route(
        _PUT,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Rate an Answer up or down; the same rating again changes nothing",
    ),
    Route(
        _DELETE,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Remove the caller's rating of an Answer",
    ),
    # Diagnostics of one of the caller's Answers: counts, timings, and usage
    Route(
        _GET,
        f"{_MESSAGE}/diagnostics",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "How an Answer was made, without any Document text",
    ),
    # Sources
    Route(
        _GET,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "List Sources",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Register a Source",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Read a Source's configuration",
    ),
    Route(
        _PATCH,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Change a Source's configuration",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/enable",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Let a Source synchronize again",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/disable",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Stop a Source from synchronizing",
    ),
    # Synchronization: durable Jobs, never broker messages
    Route(
        _POST,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "Start a synchronization, or return the one already open",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "List a Source's synchronization Jobs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/jobs/{job_id}",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M08-T07",
        "Read a synchronization Job and its newest attempt",
    ),
    # Jobs that failed for good, for an administrator
    Route(
        _GET,
        "/v1/job-decisions",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "List Jobs that failed for good, newest first",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/retry",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Queue a Job that failed for good once more",
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/cancel",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Cancel a Job that has not finished",
    ),
    # Evaluation
    Route(
        _GET,
        "/v1/evaluation-datasets",
        Operation.MANAGE_EVALUATION_DATASETS,
        _PLANNED,
        "M10-T01",
        "List Evaluation Datasets",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Start a paid evaluation run",
    ),
    Route(
        _GET,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "List evaluation runs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/evaluation-runs/{evaluation_run_id}",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Read an evaluation run and its results",
    ),
)


def served_routes() -> tuple[Route, ...]:
    """The rows the app must serve, in table order."""
    return tuple(route for route in ROUTES if route.status is RouteStatus.SERVED)


def route_problems(routes: Iterable[Route]) -> tuple[RouteProblem, ...]:
    """Every rule each route breaks; an empty tuple means the design holds."""
    routes = tuple(routes)
    seen = Counter((route.method, route.path) for route in routes)
    problems: list[RouteProblem] = []
    for route in routes:
        problems += [RouteProblem(route, rule) for rule in _broken_rules(route)]
        if seen[route.method, route.path] > 1:
            problems.append(RouteProblem(route, "the same method and path twice"))
    return tuple(problems)


def _broken_rules(route: Route) -> list[str]:
    broken: list[str] = []
    if type(route.method) is not Method:
        broken.append("no Method")
    if type(route.operation) is not Operation:
        broken.append("no Operation")
    if type(route.status) is not RouteStatus:
        broken.append("no RouteStatus")
    elif route.status is RouteStatus.SERVED and route.task is None:
        broken.append("served without a task")
    if route.task is not None and not _TASK.fullmatch(route.task):
        broken.append("a task that is not a task ID")
    if not route.path.startswith(f"{API_PREFIX}/"):
        return [*broken, "not under /v1"]
    segments = route.path.removeprefix(f"{API_PREFIX}/").split("/")
    names: set[str] = set()
    previous: str | None = None
    for position, segment in enumerate(segments):
        parameter = _PARAMETER.fullmatch(segment)
        if parameter is not None:
            if previous is None or parameter[1] != _identifier_of(previous):
                broken.append(f"{segment} does not name the list before it")
            if parameter[1] in names:
                broken.append(f"{segment} appears twice")
            names.add(parameter[1])
        elif not _WORD.fullmatch(segment):
            broken.append(f"{segment!r} is not lowercase words joined by '-'")
        elif segment in SINGLE_RESOURCES or segment in ACTIONS:
            after_id = previous is not None and _PARAMETER.fullmatch(previous)
            if position != len(segments) - 1 or not after_id:
                broken.append(f"{segment!r} must end the path, after an ID")
            if segment in ACTIONS and route.method is not Method.POST:
                broken.append(f"{segment!r} is an action, so only POST")
        elif not _is_list(segment):
            broken.append(f"{segment!r} is not a list, a single resource, or an action")
        previous = segment
    lists = route.method is Method.GET and _is_list(segments[-1])
    if route.paginated and not lists:
        broken.append("paginated, but not a GET of a list")
    if lists and not route.paginated:
        broken.append("a GET of a list that is not paginated")
    return broken


def _is_list(segment: str) -> bool:
    """A plural word, such as `sources` or `job-decisions`."""
    return (
        _WORD.fullmatch(segment) is not None
        and segment.endswith("s")
        and segment not in SINGLE_RESOURCES
        and segment not in ACTIONS
    )


def _identifier_of(collection: str) -> str | None:
    """`sources` -> `source_id`, `entries` -> `entry_id`; None if not a list.

    An irregular plural, such as `analyses`, has no rule here: name such a
    list differently, or add the rule with a test.
    """
    if not _is_list(collection):
        return None
    singular = f"{collection[:-3]}y" if collection.endswith("ies") else collection[:-1]
    return f"{singular.replace('-', '_')}_id"
```

Two details matter. `_broken_rules` checks types with `type(...) is`, so a plain string `"served"` in place of `RouteStatus.SERVED` is a problem, not a silent pass. And it collects every problem instead of stopping at the first, so one test can show all mistakes in a row at once.

Check: `uv run python -c "from knowledge_service.api_routes import ROUTES, route_problems; print(len(ROUTES), route_problems(ROUTES))"` prints `24 ()`.

## Step 3 — Write the page size and cursor helpers

Coding step.

In plain words: this file turns the two page parameters into safe values. It reads `limit` as a whole number from 1 to 100. It writes a cursor that names where the next page starts, and when a cursor comes back, it accepts it only if it is exactly what it would have written for that list.

Create `src/knowledge_service/pagination.py`:

| Part | What it does |
|---|---|
| `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE`, `MAX_CURSOR_CHARACTERS` | 50, 100, 512 |
| `InvalidPage` | the one error for a bad `limit` or cursor; the route turns it into a 422 |
| `page_size` | `None` means 50; only 1 to 3 ASCII digits from 1 to 100 are accepted |
| `encode_cursor(listing, position)` | `[1, listing, position]` as compact, sorted JSON, then base64url without `=` padding |
| `decode_cursor(cursor, listing=, keys=)` | checks length and alphabet, decodes, checks the structure, then re-encodes and requires the same text |

```python
"""Pages of a list: how many items, and where the next page starts.

Every paginated route takes `limit` and `cursor` and answers
`{"items": [...], "next_cursor": ...}`. A cursor names the last item of the
page before, so the next page starts after it even when items are added in
between. Clients must treat it as opaque. It is not signed: it only says
where to start in a list its caller may read whole anyway, and the server
checks every field before using it.
"""

import base64
import json
import re
from collections.abc import Mapping
from typing import cast

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_CHARACTERS = 512
_CURSOR_VERSION = 1
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS = re.compile(r"[1-9][0-9]{0,2}")


class InvalidPage(ValueError):
    """A page size or cursor the API does not accept."""


def page_size(raw: str | None) -> int:
    """`limit` as a number of items: absent means the default."""
    if raw is None:
        return DEFAULT_PAGE_SIZE
    if not _DIGITS.fullmatch(raw) or not 1 <= int(raw) <= MAX_PAGE_SIZE:
        raise InvalidPage(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}")
    return int(raw)


def encode_cursor(listing: str, position: Mapping[str, str]) -> str:
    """The cursor that starts the next page of `listing` after `position`."""
    document = json.dumps(
        [_CURSOR_VERSION, listing, dict(position)],
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(document.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, listing: str, keys: frozenset[str]) -> dict[str, str]:
    """The position a cursor of `listing` names, with exactly `keys`, all text.

    Raises `InvalidPage` for anything `encode_cursor` would not have written
    for this list: a cursor too long, not base64url, not JSON, of another
    version or list, with other fields, or another spelling of the same ones.
    """
    if len(cursor) > MAX_CURSOR_CHARACTERS or not _BASE64URL.fullmatch(cursor):
        raise InvalidPage("the cursor is not one this API gave")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        document: object = json.loads(raw.decode())
    # Bad base64, bad UTF-8, and bad JSON are all a ValueError.
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    position = _position(document, listing, keys)
    # One spelling only: key order, spaces, or spare base64 bits differ.
    if position is None or encode_cursor(listing, position) != cursor:
        raise InvalidPage("the cursor is not one this API gave")
    return position


def _position(
    document: object, listing: str, keys: frozenset[str]
) -> dict[str, str] | None:
    """`[1, listing, {key: text, ...}]` with exactly `keys`, else None."""
    if type(document) is not list:
        return None
    parts = cast(list[object], document)
    # `type(...) is int`, because JSON `true` would equal 1.
    if len(parts) != 3 or type(parts[0]) is not int or parts[0] != _CURSOR_VERSION:
        return None
    if parts[1] != listing or type(parts[2]) is not dict:
        return None
    fields = cast(dict[object, object], parts[2])
    position = {
        key: value
        for key, value in fields.items()
        if type(key) is str and type(value) is str
    }
    if len(position) != len(fields) or frozenset(position) != keys:
        return None
    return position
```

Why `type(parts[0]) is not int`: in Python `True == 1`, so JSON `true` would pass as version 1 with a plain comparison. Why re-encode at the end: it is the shortest way to refuse every other spelling at once, whatever JSON or base64 allows.

Check: `uv run python -c "from knowledge_service.pagination import *; c = encode_cursor('x', {'a': 'b'}); print(c, decode_cursor(c, listing='x', keys=frozenset({'a'})))"` prints the cursor and `{'a': 'b'}`.

## Step 4 — Let the decision list start after a Job

Coding step.

In plain words: the database query for Jobs waiting for a decision learns to start after a given Job instead of at the top. Then the decision module learns to write the cursor for a page's last Job and to read it back into a time and an ID, refusing any other spelling.

First the query, in `src/knowledge_service/persistence/job_recovery.py`. Replace `load_failed_for_good`:

```python
async def load_failed_for_good(
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed with no retry planned, that no newer Job replaced.

    Newest failure first. A Job for the same work queued after it, such as
    the next scheduled synchronization, replaces it, so it is not listed.
    With `after`, the list starts after the Job that finished then with
    that ID, so pages never repeat or skip a Job, even one that finished at
    the same moment.
    """
    conditions = [
        jobs.c.status == JobStatus.FAILED.value,
        jobs.c.retry_at.is_(None),
        ~_newer_of_same_intent(),
    ]
    if after is not None:
        finished_at, job_id = after
        conditions.append(
            sa.tuple_(jobs.c.finished_at, jobs.c.job_id)
            < sa.tuple_(sa.literal(finished_at), sa.literal(job_id.value))
        )
    statement = (
        sa.select(jobs)
        .where(*conditions)
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

`sa.tuple_(...) < sa.tuple_(...)` writes the SQL row comparison from block 3. `finished_at` is never `NULL` for a failed Job: the `ck_jobs_finished_at` constraint guarantees it, so the comparison never meets an unknown value.

Then `src/knowledge_service/job_decisions.py`. Add the import:

```python
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
```

Add the list's name and keys under `MAX_DECISION_LIST`:

```python
MAX_DECISION_LIST: Final = 500
_DECISION_LIST = "job-decisions"
_DECISION_CURSOR_KEYS = frozenset({"finished_at", "job_id"})
```

Replace `load_jobs_needing_decision`, and add `decision_cursor` and `decision_position` after it:

```python
async def load_jobs_needing_decision(
    runtime: DatabaseRuntime,
    *,
    limit: int = 100,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed for good and that no newer Job replaced, newest first.

    With `after`, from a `decision_position`, the list starts after that Job.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_DECISION_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DECISION_LIST}")
    async with runtime.transaction() as connection:
        return await load_failed_for_good(connection, limit=limit, after=after)


def decision_cursor(job: Job) -> str:
    """The cursor of the page that starts after `job` in the decision list."""
    if job.finished_at is None:
        raise ValueError("only a finished Job ends a page of decisions")
    return encode_cursor(
        _DECISION_LIST,
        {"finished_at": job.finished_at.isoformat(), "job_id": str(job.job_id.value)},
    )


def decision_position(cursor: str) -> tuple[datetime, JobId]:
    """Where a `decision_cursor` says the next page starts.

    Only the exact text `decision_cursor` writes is accepted, so an offset
    other than UTC or another spelling of the same ID raises `InvalidPage`.
    """
    position = decode_cursor(cursor, listing=_DECISION_LIST, keys=_DECISION_CURSOR_KEYS)
    finished_text, job_text = position["finished_at"], position["job_id"]
    try:
        finished_at = datetime.fromisoformat(finished_text)
        job_id = UUID(job_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        finished_at.utcoffset() != timedelta(0)
        or finished_at.isoformat() != finished_text
        or str(job_id) != job_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return finished_at, JobId(job_id)
```

`decision_position` refuses a time with another offset even when it is the same moment, such as `10:30+01:00` for `09:30+00:00`, because `decision_cursor` always writes UTC. It also refuses an uppercase or braced ID. One cursor per Job keeps the rule from Step 3 true for the fields inside.

Finally, pass `after` through `DatabaseJobDecisions`:

```python
    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        return await load_jobs_needing_decision(self.runtime, limit=limit, after=after)
```

Check: `uv run pyright src/knowledge_service/job_decisions.py src/knowledge_service/persistence/job_recovery.py` reports 0 errors.

## Step 5 — Give the decision list the page shape

Coding step.

In plain words: the response model of the decision list changes from one list called `jobs` to the shape every list will have: the page's items, and the cursor of the next page or nothing.

In `src/knowledge_service/contracts.py`, replace `JobsNeedingDecision`:

```python
class JobsNeedingDecision(BaseModel):
    """A page of Jobs that failed for good and no newer Job replaced.

    Newest first. `next_cursor` starts the next page; None on the last one.
    """

    items: list[OperatorJob]
    next_cursor: str | None
```

`next_cursor` has no default, so OpenAPI lists it as required: a client can rely on the key being there, with `null` on the last page.

This renames a response field (`jobs` to `items`). No client uses it yet, which is why the change happens now and not after M09-T08 connects Open WebUI.

## Step 6 — Change the app

Coding step.

In plain words: the app drops the early answer route, serves the answering route as `POST /v1/questions`, answers every unknown path or method in the one error shape, pages the decision list, and describes every route's errors, IDs, and page parameters in its OpenAPI document. The order of checks inside each route stays: who, then may they, then what.

All changes are in `src/knowledge_service/app.py`, in file order.

**Imports.** `Annotated` and `Any` from `typing`; `Path` from `fastapi`; Starlette's `HTTPException`:

```python
from typing import Annotated, Any, Protocol
```

```python
from fastapi import FastAPI, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Receive, Scope, Send
```

Remove `Answer` from the `knowledge_service.contracts` import, and the line `from knowledge_service.workflow import AnswerWorkflow`. Import the cursor helpers and the page helpers:

```python
from knowledge_service.job_decisions import (
    JobIntentAlreadyOpen,
    JobNotFound,
    decision_cursor,
    decision_position,
)
```

```python
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    MAX_PAGE_SIZE,
    InvalidPage,
    page_size,
)
```

**The path ID type**, after `CANONICAL_ID` (block 4):

```python
# A path ID, documented as a UUID but read as text: the route checks its
# spelling itself, after the caller's role, so it never answers first.
Identifier = Annotated[str, Path(json_schema_extra={"format": "uuid"})]
```

**The store's protocol.** `JobDecisions.needing_decision` takes `after`:

```python
    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]: ...
```

Delete the constant `DECISION_LIST_LIMIT` and its comment; `page_size` replaces it.

**`create_app`.** Delete the parameter `workflow: AnswerWorkflow | None = None,`. Create the app without slash redirects (block 5):

```python
    # No redirect from `/v1/x/` to `/v1/x`: a redirect answers before identity.
    app = FastAPI(title="knowledge-service", redirect_slashes=False)
```

Add the handler for unknown paths and methods, just before the `RequestValidationError` handler:

```python
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # An unknown path or method: the same error shape as every other.
        code, message = HTTP_ERRORS.get(
            error.status_code, ("http_error", "The request cannot be served")
        )
        response = error_response(
            status_code=error.status_code,
            code=code,
            message=message,
            request_id=request_id_from(request),
        )
        response.headers.update(error.headers or {})
        return response
```

**The Question route.** Delete the whole `@app.post("/v1/answer", response_model=Answer)` function. Replace the decorator and name of the stream route:

```python
    @app.post(
        "/v1/questions",
        responses={
            200: {
                "description": "The Answer as Server-Sent Events",
                "content": {"text/event-stream": {}},
            },
            **error_responses(413, 429),
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": Question.model_json_schema()}
                },
            }
        },
    )
    async def ask_question(request: Request) -> Response:
```

`openapi_extra` adds the request body to the document, because the route reads its body itself, after the role, exactly like the page parameters. The 200 response is `text/event-stream`, the Server-Sent Events of M06.

Inside it, the error for a missing streamer gets its new code:

```python
        if streamer is None:
            return error_response(
                status_code=503,
                code="answering_unavailable",
                message="Answering is unavailable",
                request_id=request_id,
            )
```

**The other served routes** each get `error_responses(...)` with their own extra statuses, and `Identifier` for their path ID:

```python
    @app.delete(
        "/v1/conversations/{conversation_id}",
        status_code=204,
        responses=error_responses(),
    )
    async def delete_own_conversation(
        request: Request, conversation_id: Identifier
    ) -> Response:
```

```python
    @app.get(
        "/v1/jobs/{job_id}",
        response_model=SynchronizationJobReport,
        responses=error_responses(404),
    )
    async def read_synchronization_job(
        request: Request, job_id: Identifier
    ) -> SynchronizationJobReport | JSONResponse:
```

```python
    @app.post(
        "/v1/jobs/{job_id}/retry",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def retry_failed_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=True)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def cancel_unfinished_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=False)
```

**The decision list.** Replace the route:

```python
    @app.get(
        "/v1/job-decisions",
        response_model=JobsNeedingDecision,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_jobs_needing_decision(
        request: Request,
    ) -> JobsNeedingDecision | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else decision_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            jobs = await job_decisions.needing_decision(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job decision list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = jobs[:limit]
        now = datetime.now(UTC)
        return JobsNeedingDecision(
            items=[operator_job(job, now=now) for job in page],
            next_cursor=decision_cursor(page[-1]) if len(jobs) > limit else None,
        )
```

Read it as block 3 in code. The page parameters are read after `require`, so a caller without the role gets 403 whatever they sent. A bad `limit` or cursor is a 422 before the store is asked. The store is asked for `limit + 1` Jobs, and the extra one only decides `next_cursor`.

**Module helpers**, before `_jobs_unavailable`:

```python
# What each error status means; every `/v1` route can answer the first six.
ERROR_STATUSES: dict[int, str] = {
    400: "The Authorization header is malformed",
    401: "No valid access token",
    403: "No role of the caller permits this operation",
    422: "The request is not valid",
    500: "An internal error",
    503: "A service it needs is unavailable",
    404: "Nothing with this ID",
    409: "The request does not fit the resource's current state",
    413: "The request body is too large",
    429: "Too many requests, or the service is at capacity",
}
COMMON_ERRORS = (400, 401, 403, 422, 500, 503)
# The error code and message for a path or method the API does not have.
HTTP_ERRORS: dict[int, tuple[str, str]] = {
    404: ("not_found", "No such resource"),
    405: ("method_not_allowed", "This resource does not allow this method"),
}

# `limit` and `cursor`, described by hand: the route reads them itself, after
# the caller's role, so a bad one never answers 422 before the 401 or 403.
PAGE_PARAMETERS: list[dict[str, Any]] = [
    {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "How many items to return",
        "schema": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_PAGE_SIZE,
            "default": DEFAULT_PAGE_SIZE,
        },
    },
    {
        "name": "cursor",
        "in": "query",
        "required": False,
        "description": "`next_cursor` of the page before; absent for the first page",
        "schema": {"type": "string", "maxLength": MAX_CURSOR_CHARACTERS},
    },
]


def error_responses(*extra: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI responses for every error status a route may answer."""
    return {
        status: {"model": ErrorResponse, "description": ERROR_STATUSES[status]}
        for status in (*COMMON_ERRORS, *extra)
    }


def page_request(request: Request) -> tuple[int, str | None]:
    """`limit` and `cursor`, each at most once and nothing else, else `InvalidPage`.

    A misspelled parameter, such as `Limit`, is refused rather than ignored.
    """
    if set(request.query_params) - {"limit", "cursor"}:
        raise InvalidPage("only limit and cursor are accepted")
    for name in ("limit", "cursor"):
        if len(request.query_params.getlist(name)) > 1:
            raise InvalidPage(f"{name} is given more than once")
    return (
        page_size(request.query_params.get("limit")),
        request.query_params.get("cursor"),
    )
```

`error_responses(404, 409)` returns the common six plus 404 and 409. Because 422 is among the six, FastAPI leaves out its own `HTTPValidationError`.

Check: `uv run pyright src` reports 0 errors, and `uv run python -c "from knowledge_service.app import create_app; print(sorted(create_app().openapi()['paths']))"` lists `/v1/questions` and no `/v1/answer`.

## Step 7 — Update the existing tests

Coding step.

In plain words: the tests that called the old routes now call the new one, the tests of the removed route go, and the role test reads its routes from the table. After this step the existing suite passes again.

**Rename the stream path** in every test file. On macOS:

```bash
grep -rl "/v1/answer/stream" tests | xargs sed -i '' 's#/v1/answer/stream#/v1/questions#g'
```

On Linux, use `sed -i` without the empty `''`. This changes 11 files; check with `git diff --stat tests`.

**`tests/test_app.py`** keeps only the three health tests. Delete everything from `test_answer_endpoint_returns_answer` to the end, and keep these imports:

```python
"""Tests FastAPI application."""

import httpx
from fastapi import FastAPI

from identity_fakes import AUTHORIZATION, TRUSTED
from knowledge_service.app import create_app


def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTHORIZATION,
    )
```

**`tests/test_stream_failures.py`**: delete the class `FailingWorkflow` and the test `test_the_answer_endpoint_gives_the_same_retry_guidance`; the stream's own failure tests cover the same guidance. Then `uv run ruff check --fix tests/test_stream_failures.py` removes the imports that became unused.

**`tests/test_streaming.py`**: in the parametrized test that expects `workflow_unavailable`, write `answering_unavailable`.

**`tests/test_app_authentication.py`**, the role test from M07-T05:

1. Delete the classes `Workflow` and `Counting`, and the import of `AnswerWorkflow`. Add `from knowledge_service.api_routes import served_routes`.
2. In `test_identity_is_decided_before_the_body_is_parsed`, post only to `"/v1/questions"` instead of looping over two paths.
3. Replace `test_the_ungrounded_endpoint_needs_a_token_too` with a test that the old routes are gone:

```python
@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_the_old_answer_routes_are_gone(path: str) -> None:
    source = Source()

    async with client(app_with(source)) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 404
    assert source.readers == []
```

4. In `test_a_caller_without_the_user_role_gets_403_and_nothing_runs` and `test_an_app_without_an_authenticator_answers_nobody`, drop the `path` parametrization and the workflow; post to `"/v1/questions"` and assert `source.readers == []`.
5. Build `ROUTES` from the table:

```python
# Every route the app serves and the operation it needs: the served rows of
# the route table, and the public routes outside `/v1`, marked None. A new
# route fails the first test below until the table lists it.
ROUTES: dict[tuple[str, str], Operation | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health/ready"): None,
    ("GET", "/openapi.json"): None,
    ("GET", "/docs"): None,
    ("GET", "/docs/oauth2-redirect"): None,
    ("GET", "/redoc"): None,
    **{(route.method.value, route.path): route.operation for route in served_routes()},
}
```

6. Remove the workflow from `full_app` and from the role test:

```python
def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
    )
```

```python
    source, conversations = Source(), Deleting()
    jobs, decisions = Reading(), Deciding()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, {"token": token}, conversations, jobs, decisions)
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path.replace("{conversation_id}", str(uuid4())).replace(
                "{job_id}", str(uuid4())
            ),
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = source.readers or conversations.calls or jobs.calls or decisions.calls
    assert bool(reached) is allowed
```

**`tests/test_job_recovery_integration.py`**: in `test_the_decision_list_shows_only_failures_nothing_replaced`, read `response.json()["items"]` instead of `["jobs"]`.

Check: `uv run pytest tests/test_app.py tests/test_app_authentication.py tests/test_stream_failures.py tests/test_streaming.py -q` gives `244 passed`.

## Step 8 — Add the design tests

Coding step.

In plain words: three sets of tests prove the design. The first checks the table's rules one by one, that the app serves exactly the served rows, and that the OpenAPI document has one error shape, UUID IDs, one page shape, and no provider words. The second attacks the cursor with hostile input and walks the decision list page by page. The third walks real PostgreSQL rows, including Jobs that failed in the same microsecond.

Create `tests/test_api_routes.py`:

| Test | What it proves |
|---|---|
| `test_the_route_table_follows_every_rule` | the 24 rows break no rule |
| `test_the_table_covers_every_resource_the_task_names` | every area of the task has a route |
| `test_each_rule_names_the_route_that_breaks_it` | each of 20 bad routes breaks exactly the one expected rule |
| `test_a_route_listed_twice_is_a_problem_both_times` | duplicates are named |
| `test_a_single_resource_at_the_end_of_a_get_is_not_a_list`, `test_a_plural_in_ies_or_with_a_dash_names_its_identifier` | good routes are accepted |
| `test_the_app_serves_exactly_the_served_rows_under_v1` | app and table agree |
| `test_every_error_a_route_documents_has_the_one_error_shape` | each route documents exactly its statuses, all `ErrorResponse` |
| `test_no_route_documents_the_framework_validation_error` | no `HTTPValidationError` |
| `test_every_path_identifier_is_a_uuid_named_after_its_resource`, `test_every_id_field_of_every_schema_is_a_uuid` | consistent IDs |
| `test_every_served_list_takes_limit_and_cursor_and_answers_one_page_shape` | consistent pagination |
| `test_the_question_route_documents_its_body`, `test_no_schema_is_shaped_like_a_model_provider_protocol` | no provider-shaped models |
| `test_an_unknown_path_or_method_answers_with_the_one_error_shape` | 404, 405, and trailing slashes |

```python
"""The native API's route table, and the OpenAPI document of the app it describes.

The table is the design: every route served or planned, who may call it, and
whether it pages. These tests check that the table follows its own rules,
that the app serves exactly its served rows, and that the OpenAPI document
gives every route one error shape, one page shape, and IDs named after their
resource, with nothing shaped like a model provider's protocol.
"""

from dataclasses import replace
from typing import Any

import httpx
import pytest

from knowledge_service.api_routes import (
    ROUTES,
    Method,
    Route,
    RouteStatus,
    route_problems,
    served_routes,
)
from knowledge_service.app import COMMON_ERRORS, create_app
from knowledge_service.roles import Operation

SPEC: dict[str, Any] = create_app().openapi()
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]
V1_OPERATIONS = [
    (method.upper(), path, operation)
    for path, item in SPEC["paths"].items()
    if path.startswith("/v1/")
    for method, operation in item.items()
]
# Field names of the OpenAI Chat Completions protocol; the native API has its
# own words, and the compatible adapter (M09-T05) translates.
PROVIDER_FIELDS = frozenset(
    {
        "choices",
        "messages",
        "finish_reason",
        "object",
        "delta",
        "logprobs",
        "role",
        "content",
        "model",
        "id",
    }
)
# Every error status each served route documents, beyond the common six.
ROUTE_ERRORS = {
    ("POST", "/v1/questions"): {413, 429},
    ("DELETE", "/v1/conversations/{conversation_id}"): set[int](),
    ("GET", "/v1/jobs/{job_id}"): {404},
    ("GET", "/v1/job-decisions"): set[int](),
    ("POST", "/v1/jobs/{job_id}/retry"): {404, 409},
    ("POST", "/v1/jobs/{job_id}/cancel"): {404, 409},
}
GOOD = Route(
    Method.GET,
    "/v1/sources",
    Operation.CONFIGURE_SOURCES,
    RouteStatus.PLANNED,
    "M09-T02",
    "List Sources",
    paginated=True,
)


def test_the_route_table_follows_every_rule() -> None:
    assert route_problems(ROUTES) == ()


def test_the_table_covers_every_resource_the_task_names() -> None:
    paths = {route.path for route in ROUTES}

    for resource in (
        "/v1/questions",
        "/v1/conversations",
        "/feedback",
        "/v1/sources",
        "/synchronizations",
        "/v1/evaluation-runs",
        "/diagnostics",
    ):
        assert any(resource in path for path in paths), resource


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"path": "/sources"}, "not under /v1"),
        ({"path": "/v2/sources"}, "not under /v1"),
        (
            {"path": "/v1/Sources", "paginated": False},
            "'Sources' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/source_list", "paginated": False},
            "'source_list' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/", "paginated": False},
            "'' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/{id}", "paginated": False},
            "{id} does not name the list before it",
        ),
        (
            {"path": "/v1/{source_id}", "paginated": False},
            "{source_id} does not name the list before it",
        ),
        (
            {"path": "/v1/sources/{source_id}/{job_id}", "paginated": False},
            "{job_id} does not name the list before it",
        ),
        ({"paginated": False}, "a GET of a list that is not paginated"),
        ({"method": Method.POST}, "paginated, but not a GET of a list"),
        (
            {"path": "/v1/sources/{source_id}", "method": Method.GET},
            "paginated, but not a GET of a list",
        ),
        ({"status": RouteStatus.SERVED, "task": None}, "served without a task"),
        ({"task": "T02"}, "a task that is not a task ID"),
        ({"operation": "configure_sources"}, "no Operation"),
        ({"status": "served", "task": None}, "no RouteStatus"),
        (
            {"path": "/v1/source", "paginated": False},
            "'source' is not a list, a single resource, or an action",
        ),
        (
            {
                "path": "/v1/sources/{source_id}/getsourcenow",
                "method": Method.POST,
                "paginated": False,
            },
            "'getsourcenow' is not a list, a single resource, or an action",
        ),
        (
            {"path": "/v1/sources/{source_id}/sources/{source_id}", "paginated": False},
            "{source_id} appears twice",
        ),
        (
            {"path": "/v1/feedback", "paginated": False},
            "'feedback' must end the path, after an ID",
        ),
        (
            {"path": "/v1/sources/{source_id}/retry", "paginated": False},
            "'retry' is an action, so only POST",
        ),
    ],
)
def test_each_rule_names_the_route_that_breaks_it(
    change: dict[str, Any], rule: str
) -> None:
    bad = replace(GOOD, **change)

    assert [problem.rule for problem in route_problems([bad])] == [rule]


def test_a_route_listed_twice_is_a_problem_both_times() -> None:
    problems = route_problems([GOOD, replace(GOOD, summary="Again")])

    assert [problem.rule for problem in problems] == [
        "the same method and path twice"
    ] * 2


def test_a_single_resource_at_the_end_of_a_get_is_not_a_list() -> None:
    diagnostics = replace(
        GOOD,
        path="/v1/conversations/{conversation_id}/messages/{message_id}/diagnostics",
        paginated=False,
    )

    assert route_problems([diagnostics]) == ()


@pytest.mark.parametrize(
    "path", ["/v1/entries/{entry_id}", "/v1/job-decisions/{job_decision_id}"]
)
def test_a_plural_in_ies_or_with_a_dash_names_its_identifier(path: str) -> None:
    assert route_problems([replace(GOOD, path=path, paginated=False)]) == ()


def test_the_app_serves_exactly_the_served_rows_under_v1() -> None:
    served: set[tuple[str, str]] = set()
    for route in create_app().routes:
        path: str = getattr(route, "path", "")
        methods: set[str] = getattr(route, "methods", None) or set()
        if path.startswith("/v1/"):
            served |= {(method, path) for method in methods}

    assert served == {(route.method.value, route.path) for route in served_routes()}


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    V1_OPERATIONS,
    ids=[f"{method} {path}" for method, path, _ in V1_OPERATIONS],
)
def test_every_error_a_route_documents_has_the_one_error_shape(
    method: str, path: str, operation: dict[str, Any]
) -> None:
    errors = {
        status: response
        for status, response in operation["responses"].items()
        if status.startswith(("4", "5"))
    }

    expected = {*COMMON_ERRORS, *ROUTE_ERRORS[method, path]}
    assert set(errors) == {str(status) for status in expected}
    for response in errors.values():
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_no_route_documents_the_framework_validation_error() -> None:
    assert "HTTPValidationError" not in SCHEMAS


def test_every_path_identifier_is_a_uuid_named_after_its_resource() -> None:
    for _method, path, operation in V1_OPERATIONS:
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                assert parameter["name"].endswith("_id"), path
                assert parameter["schema"]["format"] == "uuid", path


def test_every_id_field_of_every_schema_is_a_uuid() -> None:
    for name, schema in SCHEMAS.items():
        for field, value in schema.get("properties", {}).items():
            if field == "id" or field.endswith("_id"):
                formats = [value, *value.get("anyOf", [])]
                assert any(f.get("format") == "uuid" for f in formats), (name, field)


@pytest.mark.parametrize(
    "route",
    [route for route in served_routes() if route.paginated],
    ids=lambda route: route.path,
)
def test_every_served_list_takes_limit_and_cursor_and_answers_one_page_shape(
    route: Route,
) -> None:
    operation = SPEC["paths"][route.path][route.method.value.lower()]
    query = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]
    page = SCHEMAS[reference["$ref"].rsplit("/", 1)[1]]

    assert set(query) == {"limit", "cursor"}
    assert (
        query["limit"]["schema"]["minimum"],
        query["limit"]["schema"]["maximum"],
    ) == (
        1,
        100,
    )
    assert set(page["properties"]) == {"items", "next_cursor"}
    assert set(page["required"]) == {"items", "next_cursor"}
    assert page["properties"]["items"]["type"] == "array"


def test_the_question_route_documents_its_body() -> None:
    body = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]

    schema = body["content"]["application/json"]["schema"]
    assert (body["required"], set(schema["properties"])) == (True, {"text"})


def test_no_schema_is_shaped_like_a_model_provider_protocol() -> None:
    question = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]
    schemas = {
        **SCHEMAS,
        "Question": question["content"]["application/json"]["schema"],
    }
    for name, schema in schemas.items():
        assert not PROVIDER_FIELDS & set(schema.get("properties", {})), name
        assert not any(
            word in name.lower() for word in ("openai", "chat", "completion")
        ), name


async def call(method: str, path: str) -> httpx.Response:
    """A request with no token to the app with nothing wired."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as http:
        return await http.request(method, path)


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("POST", "/v1/answer", 404, "not_found"),
        ("GET", "/v1/nothing-here", 404, "not_found"),
        ("GET", "/v1/questions", 405, "method_not_allowed"),
        ("PUT", "/v1/job-decisions", 405, "method_not_allowed"),
        ("GET", "/v1/job-decisions/", 404, "not_found"),
        ("POST", "/v1/questions/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_answers_with_the_one_error_shape(
    method: str, path: str, status: int, code: str
) -> None:
    response = await call(method, path)

    assert response.status_code == status
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["code"] == code
```

Create `tests/test_pagination.py`. Hypothesis, the property-testing library already in the project, generates thousands of strings and byte strings; whatever it sends, decoding must give a position or `InvalidPage`, never another exception. The HTTP tests use a fake store that pages the way the SQL does.

```python
"""Page sizes and cursors: only what the API wrote comes back in.

A cursor travels through the client, so it is untrusted input. Each test
hands the decoder something `encode_cursor` would never write and expects
`InvalidPage`, never another exception or a position.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import JobDecisions, create_app
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import decision_cursor, decision_position
from knowledge_service.jobs import Job, JobKind
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    InvalidPage,
    decode_cursor,
    encode_cursor,
    page_size,
)
from knowledge_service.roles import ApplicationRole

KEYS = frozenset({"finished_at", "job_id"})
LIST = "job-decisions"
FINISHED_AT = datetime(2026, 11, 24, 9, 30, 15, 123456, tzinfo=UTC)


def raw_cursor(document: Any) -> str:
    """A cursor holding any JSON, encoded the way `encode_cursor` encodes."""
    text = json.dumps(document)
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def raw_text(text: str) -> str:
    """A cursor holding exactly `text`."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def failed_job(finished_at: datetime = FINISHED_AT) -> Job:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=finished_at - timedelta(minutes=2),
    )
    started = queued.start(started_at=finished_at - timedelta(minutes=1))
    return started.fail(error_code="database_rejected", finished_at=finished_at)


@pytest.mark.parametrize(
    ("raw", "size"), [(None, DEFAULT_PAGE_SIZE), ("1", 1), ("100", 100)]
)
def test_a_page_size_is_a_whole_number_from_1_to_100(
    raw: str | None, size: int
) -> None:
    assert page_size(raw) == size


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "00",
        "007",
        "101",
        "-1",
        "+5",
        " 5",
        "5 ",
        "5.0",
        "1e2",
        "0x10",
        "\u0665",
        "1000",
    ],
)
def test_any_other_page_size_is_refused(raw: str) -> None:
    with pytest.raises(InvalidPage):
        page_size(raw)


def test_a_cursor_gives_back_the_position_it_was_made_from() -> None:
    position = {"finished_at": "2026-11-24T09:30:15+00:00", "job_id": str(uuid4())}

    assert (
        decode_cursor(encode_cursor(LIST, position), listing=LIST, keys=KEYS)
        == position
    )


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "a" * (MAX_CURSOR_CHARACTERS + 1),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]) + "=",
        "not+base64/",
        "%%%",
        "A",
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        raw_cursor("just text"),
        raw_cursor([2, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([True, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1.0, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y", "more": "z"}]),
        raw_cursor([1, LIST, {"finished_at": 5, "job_id": "y"}]),
        raw_cursor([1, LIST, ["finished_at", "job_id"]]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}, "extra"]),
        raw_cursor([[[[[[[[[[1]]]]]]]]]]),
        raw_cursor([1, "sources", {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"job_id": "y", "finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]),
        '[1,"job-decisions",{"finished_at":"x","finished_at":"x","job_id":"y"}]',
    ],
)
def test_a_cursor_the_api_did_not_write_is_refused(cursor: str) -> None:
    if cursor.startswith("["):
        cursor = raw_text(cursor)
    with pytest.raises(InvalidPage):
        decode_cursor(cursor, listing=LIST, keys=KEYS)


@given(st.text(max_size=600))
def test_any_text_as_a_cursor_is_a_position_or_invalid_page(cursor: str) -> None:
    try:
        position = decode_cursor(cursor, listing=LIST, keys=KEYS)
    except InvalidPage:
        return
    assert set(position) == KEYS


@given(st.binary(max_size=400))
def test_any_bytes_behind_a_cursor_are_a_position_or_invalid_page(raw: bytes) -> None:
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    try:
        decision_position(cursor)
    except InvalidPage:
        return


def test_spare_base64_bits_do_not_make_a_second_spelling() -> None:
    cursor = encode_cursor(LIST, {"finished_at": "x", "job_id": "y"})
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    others = [cursor[:-1] + letter for letter in alphabet if letter != cursor[-1]]

    accepted: list[str] = []
    for other in others:
        try:
            decode_cursor(other, listing=LIST, keys=KEYS)
        except InvalidPage:
            continue
        accepted.append(other)
    assert accepted == []


def test_a_decision_cursor_starts_after_its_job() -> None:
    job = failed_job()

    assert decision_position(decision_cursor(job)) == (FINISHED_AT, job.job_id)


@pytest.mark.parametrize(
    "position",
    [
        {"finished_at": "2026-11-24T10:30:15.123456+01:00"},
        {"finished_at": "2026-11-24T09:30:15.123456"},
        {"finished_at": "2026-11-24T09:30:15.123456Z"},
        {"finished_at": "2026-11-24 09:30:15.123456+00:00"},
        {"finished_at": "yesterday"},
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "000000000000000000000000000000001"},
        {"job_id": "not an id"},
    ],
)
def test_a_decision_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    valid = {
        "finished_at": FINISHED_AT.isoformat(),
        "job_id": "00000000-0000-0000-0000-00000000000a",
    }

    with pytest.raises(InvalidPage):
        decision_position(encode_cursor("job-decisions", {**valid, **position}))


# The decision list over HTTP, with a fake store.


class Decisions:
    """Holds failed Jobs newest first, and records what each list asked for."""

    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs
        self.calls: list[dict[str, Any]] = []

    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        self.calls.append({"limit": limit, "after": after})
        start = 0
        if after is not None:
            keys = [(job.finished_at, job.job_id.value) for job in self.jobs]
            start = next(
                (i for i, key in enumerate(keys) if key < (after[0], after[1].value)),
                len(keys),
            )
        return tuple(self.jobs[start : start + limit])


class Administrator:
    """Gives every request an administrator's caller."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=READER, roles=frozenset({ApplicationRole.ADMINISTRATOR}))


async def list_decisions(decisions: Decisions, query: str) -> httpx.Response:
    app = create_app(
        authenticator=Administrator(), job_decisions=cast(JobDecisions, decisions)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/job-decisions{query}", headers={"Authorization": "Bearer t"}
        )


async def test_pages_of_decisions_follow_each_other_to_the_end() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n // 2)) for n in range(5)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (await list_decisions(decisions, query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [call["limit"] for call in decisions.calls] == [3, 3, 3]


async def test_a_full_last_page_says_no_page_follows() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n)) for n in range(4)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)

    first = (await list_decisions(decisions, "?limit=2")).json()
    cursor = first["next_cursor"]
    second = (await list_decisions(decisions, f"?limit=2&cursor={cursor}")).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


async def test_an_empty_list_has_no_next_page() -> None:
    page = (await list_decisions(Decisions([]), "")).json()

    assert page == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=101",
        "?limit=x",
        "?limit=1&limit=2",
        "?cursor=%%",
        "?cursor=a&cursor=b",
        "?Limit=5",
        "?offset=10",
    ],
)
async def test_a_bad_limit_or_cursor_is_refused_before_the_store_is_asked(
    query: str,
) -> None:
    decisions = Decisions([failed_job()])

    response = await list_decisions(decisions, query)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert decisions.calls == []


@pytest.mark.parametrize("query", ["?limit=0", "?cursor=%%", "?Limit=5"])
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_limit_or_cursor_is_read(
    query: str, headers: dict[str, str], status: int
) -> None:
    decisions = Decisions([failed_job()])
    app = create_app(authenticator=TRUSTED, job_decisions=cast(JobDecisions, decisions))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.get(f"/v1/job-decisions{query}", headers=headers)

    assert response.status_code == status
    assert decisions.calls == []


async def test_a_cursor_for_one_job_id_is_read_as_that_id() -> None:
    job = failed_job()
    decisions = Decisions([job])

    await list_decisions(decisions, f"?cursor={decision_cursor(job)}")

    assert decisions.calls[0]["after"] == (FINISHED_AT, job.job_id)
    assert isinstance(decisions.calls[0]["after"][1].value, UUID)
```

Append the PostgreSQL test to `tests/test_job_recovery_integration.py`, before the comment `# The relay and the migration.`:

```python
async def failed_purge_at(runtime: DatabaseRuntime, finished_at: datetime) -> Job:
    """A purge of a Document of its own, failed for good at `finished_at`."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            ),
        )
        failed = queued.job.start(started_at=STARTED_AT).fail(
            error_code="database_rejected", finished_at=finished_at
        )
        await save_job(connection, job=failed)
    return failed


async def test_pages_of_decisions_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    # Three Jobs fail in the same microsecond, so only their IDs order them.
    for minutes in (1, 2, 2, 2, 3):
        await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=minutes))
    expected = [
        str(job.job_id.value) for job in await load_jobs_needing_decision(runtime)
    ]
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        response = await over_http(runtime, "GET", f"/v1/job-decisions{query}")
        page = response.json()
        if not seen:
            # A Job that fails while the operator reads goes before the first
            # page, so it moves none of the pages after it.
            await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=10))
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == expected
    assert len(set(seen)) == 5
```

Each purge gets its own target, so no Job replaces another. Three Jobs fail at the same minute, so only `job_id` orders them. After the first page a newer Job fails, and the pages after it must not move.

Check:

```bash
uv run pytest tests/test_api_routes.py tests/test_pagination.py -q
uv run pytest tests/test_job_recovery_integration.py -q -m integration
```

Expected: `45 passed` and `70 passed` for the first two files, and `31 passed` for the integration file.

## Step 9 — Break it on purpose

No coding in this step; you make one temporary change and undo it.

A test you have never seen fail proves little. This step shows the full-last-page test catching the off-by-one of the review.

In `list_jobs_needing_decision`, change `if len(jobs) > limit else None` to `if len(jobs) >= limit else None`. Run with bytecode caching off, because a same-size edit within one second can reuse the old compiled file:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_pagination.py -q
```

Expected: `1 failed, 69 passed`, and the failure is `test_a_full_last_page_says_no_page_follows`: its second page of 2 Jobs still has a `next_cursor`. Undo the change and run again: `70 passed`.

What would falsify the design: a page that repeats or skips a Job while the list changes; a cursor accepted for another list or in another spelling; a route under `/v1` that is not in the table, or answers before its role check, or with another error shape; a native schema with a provider's field.

## Step 10 — Update the documents

No coding in this step.

Only the two documents that describe routes change.

In `docs/architecture.md`:

- in the `answering` row of the code layout table, `behind POST /v1/answer/stream` becomes `behind POST /v1/questions`;
- under "Answering", "The streamed path, `POST /v1/answer/stream`" becomes `POST /v1/questions`;
- "Both answer endpoints `require` ..." becomes: "`POST /v1/questions`, the one Question route, requires the `ask_questions` operation before it reads the body, and refuses with 403 otherwise.";
- in the decision routes paragraph, "before the ID is parsed. They list the Jobs that failed for good and that no newer Job replaced," becomes "before the ID, `limit`, or `cursor` is read. They list the Jobs that failed for good and that no newer Job replaced, a page at a time,";
- before the paragraph that starts with `` `job_dispatch` ``, add:

```markdown
`api_routes` is the native API's design. `ROUTES` lists every route the
service serves or plans under `/v1`, each with the Operation an
Application Role must permit, the task that builds it, and whether it
pages; the app serves exactly its served rows. `route_problems` checks the
rules: path words are lowercase and joined by `-`, each `{x_id}` follows
the list it names (`/v1/sources/{source_id}`), and every GET of a list
pages. A paginated route takes `limit` (1 to 100, 50 by default) and
`cursor`, both read after the role check, refuses any other query
parameter, and answers
`{"items": [...], "next_cursor": ...}`; `pagination` writes the cursor,
which names the last item of the page, so a page never repeats or skips
an item added in between, and checks every field of one that comes back.
Every `/v1` route documents its errors in OpenAPI with the one
`ErrorResponse` shape. A provider's protocol is an adapter's business: no
native route or schema uses its field names (ADR 0004).
```

In `docs/threat-model.md`:

- in the entry points table, `POST /v1/answer/stream` becomes `POST /v1/questions`, and the row for `POST /v1/answer` is deleted;
- the row "The provider keeps Questions and Evidence" becomes: "The one Question route, `POST /v1/questions`, asks it not to (`store=False`); the ungrounded M01 route was removed in M09-T01 | None";
- the row "A Question floods the model to run up cost" keeps only "Context and token budgets and one deadline per stream";
- in the entry points row for `GET /v1/job-decisions`, "failed for good, with their kind" becomes "failed for good, a page at a time, with their kind", and "both checked before the ID is parsed" becomes "both checked before the ID, `limit`, or `cursor` is read";
- in the known gaps, replace "`POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it." with:

```markdown
- A page cursor is not signed. A client can write its own, but it only says where a list starts that the caller may read whole anyway, and the server refuses any cursor it would not have written.
```

Check: `grep -n "/v1/answer" docs/architecture.md docs/threat-model.md` prints nothing, and `make docs-check` passes.

## Step 11 — Run the gates and write the evidence

No coding in this step.

Run the gates one after another, on one test database:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --check
uv run pre-commit run --all-files
```

On the prototype these gave: 363 files formatted, all checks passed, 0 errors, docs-check exit 0, `3377 passed` unit, and `910 passed` integration with Keycloak's secrets and Redis available.

Then write `docs/evidence/M09-T01-native-resource-routes.md` in your own words: the invariant and owners from Step 1, your prediction and what happened, the expected failing check, the route table by area, the paging design and why not an offset, the deliberate break, the gate results, and what would falsify the design. No external call is made by this lesson's code, and no paid call.

## Completion checklist

- [ ] `src/knowledge_service/api_routes.py` lists 24 routes and `route_problems(ROUTES)` is empty.
- [ ] `POST /v1/answer` is gone; the answering route is `POST /v1/questions`.
- [ ] `GET /v1/job-decisions` takes `limit` and `cursor`, answers `{items, next_cursor}`, and a full last page has `next_cursor: null`.
- [ ] A bad or unknown query parameter is a 422 `validation_error`, but only after the role check.
- [ ] An unknown path or method under `/v1` answers in the `ErrorResponse` shape, with no redirect.
- [ ] Every `/v1` route documents its errors with `ErrorResponse`, its IDs as UUIDs, and no provider fields.
- [ ] `tests/test_api_routes.py` (45), `tests/test_pagination.py` (70), and the new integration test pass.
- [ ] You saw the deliberate break fail and pass again.
- [ ] `docs/architecture.md` and `docs/threat-model.md` no longer mention `/v1/answer`.
- [ ] Every gate passes, and the evidence file is written.

## Reflection questions

1. Suppose an administrator reads page 1 of the decision list, then retries a Job on it, then reads page 2. Is the retried Job on page 2, and could a Job be skipped? Explain with the cursor's `(finished_at, job_id)`.
2. The cursor is not signed. What exactly can a client do by writing its own cursor, and why does that show them nothing they could not already read?
3. If `limit` were declared as a FastAPI `Query(ge=1, le=100)`, what would a caller without the `recover_jobs` role learn from sending `?limit=0`? Why does the route read it by hand instead?
4. Which test fails first if M09-T02 adds `GET /v1/sources` to `app.py` but forgets to add the row to the table? Which fails if the row has the wrong Operation?
5. Why do the native schemas avoid `messages`, `role`, and `choices`, when Open WebUI speaks exactly that protocol? Where will those words live instead?
6. After this lesson, which failure can still happen after a client believes its request succeeded? Think about a page read while Jobs fail, and about the old `/v1/answer` route in a client that was never updated.
