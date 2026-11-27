# M09-T04 — Serve OpenAI-compatible models

Task: [M09-T04 — Serve OpenAI-compatible models](../curriculum/milestones/M09-product-interfaces/M09-T04-serve-openai-compatible-models.md)

## What you will do

This lesson opens the second half of M09: the OpenAI-compatible adapter that Open WebUI will talk to. Open WebUI, like every OpenAI client, first asks which models it may use. After this lesson the service answers with one virtual model, the knowledge base, and does so in OpenAI's own format, so OpenAI's own Python client can read it.

A concrete example. With a user's access token:

```text
GET /openai/v1/models
Authorization: Bearer eyJhbGciOi...
-> 200 {"object": "list",
        "data": [{"id": "knowledge-base", "object": "model", "created": 1795564800,
                  "owned_by": "knowledge-service", "name": "Knowledge base"}]}

GET /openai/v1/models            (no token)
-> 401 {"error": {"message": "A valid access token is required",
                  "type": "authentication_error", "param": null, "code": "unauthenticated"}}
```

The next lessons build on this route: M09-T05 answers chat completions with this model, M09-T06 streams them, M09-T07 keeps Citations, and M09-T08 connects Open WebUI.

| What the task asks | Where it is done |
|---|---|
| `/v1/models` with one virtual knowledge model | Blocks 1 and 2; Steps 2 and 4 |
| Stable ownership and metadata expected by Open WebUI | Block 2; Step 2 |
| Protocol test: shape | Step 6 (OpenAI's own client) |
| Protocol test: bearer authentication | Block 3; Step 6 (with real Keycloak tokens too) |
| Protocol test: configured model visibility | Block 4; Steps 3 and 6 |
| Success, boundary, typed failure or denial | Step 6 |
| Artifacts versioned and redacted, external calls bounded | Block 3; Step 9 |
| Explain what would falsify the design | Step 7 and the reflection questions |

Terms this lesson needs:

- **OpenAI-compatible API:** an HTTP API that speaks the same request and response shapes as OpenAI's, so any OpenAI client can use it by changing only its base URL and key.
- **Base URL:** the address an OpenAI client puts in front of every path. With base URL `http://api/openai/v1`, the client's "list models" call is `GET http://api/openai/v1/models`.
- **Virtual model:** a model ID that names no real language model. Here, `knowledge-base` means "answer from the knowledge base", whichever provider model does the writing behind it.
- **Adapter:** code that translates one protocol into another. ADR 0004 keeps the native API in its own words and lets a thin adapter speak OpenAI's.

## Before you begin

This section explains each decision the lesson's code depends on. Each block shows a real situation in this project, what happens today, the fact underneath it, and the fix. Read it first: the steps rely on it.

### 1. The adapter lives at `/openai/v1`, not inside the native `/v1`

The task says `/v1/models`, the path OpenAI uses. The obvious place would be this service's own `/v1/models`. Here is what happens if you put it there:

| What M09-T01 enforces for every `/v1` route | What OpenAI's model list needs |
|---|---|
| No provider field names in any schema: `object`, `id`, `model`, ... | `{"object": "list", "data": [{"id": ..., "object": "model"}]}` |
| Every GET of a list takes `limit` and `cursor` and answers `{items, next_cursor}` | One page, `{"object", "data"}`, no cursor |
| One error shape: `{"code", "message", "request_id"}` | OpenAI's error shape: `{"error": {"message", "type", "param", "code"}}` |

Every one of those M09-T01 tests would fail. And they would be right to: ADR 0004 says the native API keeps its own words, and only an adapter speaks a provider's.

The hidden fact: an OpenAI client never sees the path `/v1/models` as such. It joins its configured **base URL** with `/models`. So the adapter can live anywhere, as long as its base URL ends there. This lesson puts it at `/openai/v1`:

| Client | Base URL it is given | Model list it calls |
|---|---|---|
| OpenAI's Python client | `http://api:8000/openai/v1` | `GET /openai/v1/models` |
| Open WebUI (M09-T08) | `http://api:8000/openai/v1` | `GET /openai/v1/models` |

The adapter's route also stays out of the native OpenAPI document (`include_in_schema=False`). That document describes the native API; OpenAI documents its own protocol.

Think of a hotel with a separate entrance for tour groups: the same building and staff, but the group's own sign and language at that door.

### 2. One virtual model whose details never change

Open WebUI asks for the model list on every page load. The adapter's server code (verified in `open_webui/routers/openai.py`) does this with the answer:

| What Open WebUI does | What it means for us |
|---|---|
| reads `response["data"]` | the list must be under `data` |
| keys each model by `id`, and saves a user's choice by that ID | the ID must never change, or saved chats point at a model that is gone |
| shows `name`, or the ID when `name` is missing | send a human `name` |
| replaces `owned_by` with its own value | any stable owner is fine |
| skips a body that has an `error` key | an error must be OpenAI-shaped, or it is not recognised as one |

So the list holds one model, and every field is fixed:

```python
# Was (what a first try often does): details that move
ModelObject(id=settings.model_name, created=int(time.time()), owned_by="openai")

# Became: a virtual model, all of it fixed or configured
ModelObject(id="knowledge-base", created=MODEL_CREATED, owned_by="knowledge-service", name="Knowledge base")
```

The hidden fact is about `created`. It is a Unix timestamp. Computed with `time.time()`, it changes on every restart, and a client that compares model lists sees a "new" model each time. Here it is a constant: midnight UTC on 2026-11-25, the day this model was first served.

The ID names no provider and no language model. `knowledge-base` stays the same when the service moves from one OpenAI model to another, or to a local one in M15, because what the user chooses is "answer from the knowledge base", not "talk to gpt-something".

### 3. The same bearer check as every native route, refused in OpenAI's shape

The model list needs a bearer token and the `ask_questions` operation (the `user` role), checked exactly as on `POST /v1/questions`: who, then whether they may. What changes is only the shape of a refusal.

The hidden fact is that FastAPI's exception handlers belong to the whole app. When `authenticate` raises inside any route, the app's handler answers, and today that handler always writes the native shape:

| Moment | Request | Handler | Body |
|---|---|---|---|
| 1 | `GET /openai/v1/models` without a token | `_NotAuthenticated` handler | `{"code": "unauthenticated", "message": ..., "request_id": ...}` |
| 2 | Open WebUI reads it | | no `error` key, no `data` key: no models, and no error shown |

So the adapter's route catches the three refusals itself and answers in OpenAI's shape. To keep one source of truth for each refusal, the lesson first moves what each one says into a small `Refusal` value, shared by the native handlers and the adapter:

| Refusal | Status | Native code | OpenAI `type` | Extra header |
|---|---|---|---|---|
| no token, unknown token, another scheme | 401 | `unauthenticated` | `authentication_error` | `WWW-Authenticate: Bearer ...` |
| malformed `Authorization` header | 400 | `invalid_request` | `invalid_request_error` | `WWW-Authenticate: Bearer error="invalid_request"` |
| role does not permit `ask_questions` | 403 | `forbidden` | `permission_error` | none |
| tokens cannot be checked | 503 | `dependency_unavailable` | `server_error` | `Retry-After: 5` |
| unknown path or method under `/openai/v1` | 404, 405 | `not_found`, `method_not_allowed` | `invalid_request_error` | `Allow` for 405 |

OpenAI's error body has no field for a request ID, so the adapter sends it as the `X-Request-ID` header. Messages are the fixed native ones, so no token text ever reaches the body, a header, or a log.

The native routes behave exactly as before: the independent review compared 580 native requests, refusals included, before and after the change, byte for byte.

### 4. The model is configured, and only a usable ID is accepted

The model's ID and display name come from settings, `KNOWLEDGE_SERVICE_COMPATIBLE_MODEL_ID` and `KNOWLEDGE_SERVICE_COMPATIBLE_MODEL_NAME`, with the defaults `knowledge-base` and "Knowledge base". Whatever is configured is the only model shown.

| Value | Rule | Refused examples |
|---|---|---|
| ID | 1 to 64 of `a-z 0-9 . _ : -`, starting with a letter or digit | `Knowledge-Base`, `knowledge base`, `kb/1`, `kb?`, `-kb` |
| name | 1 to 64 visible characters, in NFC, no space around, at least one that shows | `" Knowledge"`, a line break, a lone accent, `e` + accent instead of `é` |

An ID a client cannot put in a URL or a setting without escaping is refused at start-up, not discovered by a user. `Settings` checks both values when it loads, so a bad deployment fails before it serves anything.

### Other review findings

An independent review attacked the prototype before this guide was written. Each finding below was fixed, with a test that failed before the fix.

- **Behind a path prefix, unknown paths had the native shape.** Served with `root_path="/api"`, as behind a proxy, `GET /api/openai/v1/nope` got `{"code": "not_found", ...}`, because the prefix check read the full path, `/api` included. Now the check removes `root_path` first, as routing does.
- **A name could show nothing.** A lone combining accent passed the rule, and `e` plus an accent counted as two characters. Now a name must be in NFC and hold at least one character that shows.
- **A refusal carried no request ID.** Now it is the `X-Request-ID` header.

### Known limits

- Only the list is served: `GET /openai/v1/models/{id}` answers an OpenAI-shaped 404.
- The prefix is case-sensitive, and `/openai` without `/v1` gets the native 404.
- In Open WebUI, an ordinary user sees a provider's model only after an admin gives access to it; that setup is part of M09-T08.
- No production entry point builds the app yet, so nothing calls `Settings.compatible_model()` outside tests.
- The tests use `httpx2`, the HTTP library OpenAI's client is built on; it comes with `openai`, not as a direct dependency.

## Step 1 — Read, predict, and see the check fail

No coding in this step.

Read the task file, then Blocks 1 to 4. Write down your answers before you code:

- If the model list lived at the native `/v1/models` with OpenAI's shape, which M09-T01 tests would fail?
- A request with no token reaches the model list. What would Open WebUI do with the native error body, and what does it do with OpenAI's?
- The service restarts. Which field of the model would change if it were built with `time.time()`?

Then run the test file this lesson creates, to see that it does not exist yet:

```bash
uv run pytest tests/test_compatible_models.py -q
```

Expected: `ERROR: file or directory not found: tests/test_compatible_models.py`, `no tests ran`, exit code 4.

## Step 2 — Write the compatibility package

Coding step.

**In plain words:** this new package is the only place that speaks OpenAI's words. It describes the one virtual model and checks its configured ID and name, builds the list in OpenAI's format, and writes an error in OpenAI's format with the native code and message.

| Part | What it does |
|---|---|
| `MODEL_CREATED`, `MODEL_OWNER` | the fixed creation time and owner |
| `CompatibleModel` | the configured ID and name, each checked against its rule |
| `ModelObject`, `ModelList` | OpenAI's model and list shapes, plus Open WebUI's `name` |
| `model_list` | the list a permitted caller sees |
| `CompatibleError`, `compatible_error` | OpenAI's error body, with a `type` per status |

Create `src/knowledge_service/compatibility/__init__.py`:

```python
"""The OpenAI-compatible adapter: OpenAI's protocol, translated to the native API.

ADR 0004: the native API keeps its own words, and only this package speaks
OpenAI's. Its routes live under `/openai/v1`, outside the native `/v1`
design, and stay out of the native OpenAPI document.
"""
```

Create `src/knowledge_service/compatibility/models.py`:

```python
"""The model list of the OpenAI-compatible adapter: one virtual knowledge model.

An OpenAI client, such as Open WebUI, asks `GET {base}/models` which models
it may choose. This service offers exactly one: a virtual model that answers
from the knowledge base through the native Question workflow, whatever
language model runs behind it. Its ID and display name come from
configuration, and its creation time and owner are fixed, so a client's
saved choice keeps working across restarts and upgrades, and nothing names
the provider or the real model behind it.

A refusal has OpenAI's error shape, `{"error": {...}}`, with the native
API's fixed codes and messages, so an OpenAI client reads it as an error
and never as a model list.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Midnight UTC on 2026-11-25, when the virtual model was first served. It
# never changes: a model whose creation time moves looks like a new model.
MODEL_CREATED: Final = 1_795_564_800
MODEL_OWNER: Final = "knowledge-service"
DEFAULT_MODEL_ID: Final = "knowledge-base"
DEFAULT_MODEL_NAME: Final = "Knowledge base"
MAX_MODEL_NAME: Final = 64
# Lowercase letters, digits, and `.`, `_`, `:`, `-`: an ID a client can put
# in a URL or a setting without escaping.
MODEL_ID_PATTERN: Final = r"[a-z0-9][a-z0-9._:-]{0,63}"
_MODEL_ID = re.compile(MODEL_ID_PATTERN)


class InvalidCompatibleModel(ValueError):
    """A configured model ID or name breaks its rule."""


@dataclass(frozen=True, slots=True)
class CompatibleModel:
    """The one virtual model the adapter offers, as configured."""

    model_id: str = DEFAULT_MODEL_ID
    name: str = DEFAULT_MODEL_NAME

    def __post_init__(self) -> None:
        if type(self.model_id) is not str or not _MODEL_ID.fullmatch(self.model_id):
            raise InvalidCompatibleModel(
                "the model ID must be 1 to 64 lowercase letters, digits,"
                " `.`, `_`, `:`, or `-`, starting with a letter or digit"
            )
        if (
            type(self.name) is not str
            or not self.name
            or len(self.name) > MAX_MODEL_NAME
            or self.name != self.name.strip()
            or not self.name.isprintable()
            # One spelling of each letter, so the length counts what shows.
            or unicodedata.normalize("NFC", self.name) != self.name
            or not any(_shows(character) for character in self.name)
        ):
            raise InvalidCompatibleModel(
                f"the model name must be visible text of 1 to {MAX_MODEL_NAME}"
                " characters in NFC, with no space around it"
            )


def _shows(character: str) -> bool:
    """Whether a character is seen on its own: not a mark, space, or control."""
    return unicodedata.category(character)[0] not in "MZC"


class ModelObject(BaseModel):
    """One model, as OpenAI's `GET /models` lists it, with Open WebUI's `name`."""

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str
    name: str


class ModelList(BaseModel):
    """OpenAI's list of models: all of them, in one page."""

    object: Literal["list"] = "list"
    data: list[ModelObject]


def model_list(model: CompatibleModel) -> ModelList:
    """The list a permitted caller sees: the one configured model."""
    return ModelList(
        data=[
            ModelObject(
                id=model.model_id,
                created=MODEL_CREATED,
                owned_by=MODEL_OWNER,
                name=model.name,
            )
        ]
    )


class CompatibleErrorDetail(BaseModel):
    """What went wrong, in OpenAI's words: a fixed message, a type, a code."""

    message: str
    type: str
    param: str | None = None
    code: str


class CompatibleError(BaseModel):
    """OpenAI's error body: one `error` object, never a list or a model."""

    error: CompatibleErrorDetail


# OpenAI's error type for each status this adapter answers with.
_ERROR_TYPES: Final = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    405: "invalid_request_error",
    500: "server_error",
    503: "server_error",
}


def compatible_error(
    *, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    """An error response in OpenAI's shape, with the native code and message."""
    body = CompatibleError(
        error=CompatibleErrorDetail(
            message=message,
            type=_ERROR_TYPES.get(status_code, "server_error"),
            code=code,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )
```

`_shows` asks Unicode for each character's category: a mark (M), a space (Z), or a control or format character (C) shows nothing on its own. A name needs at least one character that is none of those.

Check: `uv run pyright src/knowledge_service/compatibility` reports 0 errors.

## Step 3 — Configure the model

Coding step.

**In plain words:** the model's ID and display name become settings, with the defaults from Step 2. When the settings load, they build the model once, so a bad ID or name stops the service at start-up with the rule it broke.

In `src/knowledge_service/settings.py`, add the import:

```python
from knowledge_service.compatibility.models import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_NAME,
    CompatibleModel,
)
```

and after `redis_health_timeout_seconds`, add:

```python
    # The one model the OpenAI-compatible adapter offers, as clients see it
    # (`compatibility.models`). Changing the ID makes saved choices stale.
    compatible_model_id: str = DEFAULT_MODEL_ID
    compatible_model_name: str = DEFAULT_MODEL_NAME

    @model_validator(mode="after")
    def check_compatible_model(self) -> Self:
        # Raises `InvalidCompatibleModel`, a ValueError, naming the rule.
        self.compatible_model()
        return self

    def compatible_model(self) -> CompatibleModel:
        """The virtual model the OpenAI-compatible adapter offers."""
        return CompatibleModel(
            model_id=self.compatible_model_id, name=self.compatible_model_name
        )
```

`InvalidCompatibleModel` is a `ValueError`, so Pydantic turns it into the usual `ValidationError` naming the field.

Check:

```bash
uv run python -c "from knowledge_service.settings import Settings; print(Settings().compatible_model())"
```

Expected: `CompatibleModel(model_id='knowledge-base', name='Knowledge base')`.

## Step 4 — Serve the model list

Coding step.

**In plain words:** the app gets the adapter's one route. It checks the token and the role exactly as the native routes do, and answers with the list, or with the refusal in OpenAI's shape. To share each refusal's status, code, message, and headers between the native handlers and the adapter, the handlers now take them from small shared values instead of writing them inline. Any unknown path or method under the adapter's prefix is answered in OpenAI's shape too.

| Part | What it does |
|---|---|
| `Refusal`, `failure_refusal`, `not_authenticated_refusal`, `FORBIDDEN` | what each refusal says, whatever shape carries it |
| `native_error` | a `Refusal` in the native `ErrorResponse` shape |
| `COMPATIBLE_PREFIX`, `is_compatible` | the adapter's prefix, read without `root_path` |
| `compatible_refusal` | a `Refusal` in OpenAI's shape, with `X-Request-ID` |
| `list_compatible_models` | `GET /openai/v1/models`, out of the native OpenAPI document |

In `src/knowledge_service/app.py`, add the imports:

```python
from dataclasses import dataclass, field
```

```python
from knowledge_service.compatibility.models import (
    CompatibleModel,
    ModelList,
    compatible_error,
    model_list,
)
```

Replace `failure_response` with the shared refusals, the adapter's helpers, and a `failure_response` that uses them; they end just before `request_id_from`:

```python
@dataclass(frozen=True, slots=True)
class Refusal:
    """What a refused request is told, whichever error shape carries it."""

    status_code: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict[str, str])


def failure_refusal(kind: FailureKind) -> Refusal:
    """The refusal for `kind`: a response not yet started has no stream."""
    policy = policy_for(kind)
    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message
    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")
    # An outage or a limit says when to send the request again.
    retry_after = stream_failure(kind, StreamPhase.BEFORE_HEADERS).retry_after_seconds
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return Refusal(status_code, public_code, client_message, headers)


def not_authenticated_refusal(error: AuthenticationError) -> Refusal:
    """A missing, invalid, or malformed bearer token, with its RFC 6750 challenge."""
    malformed = error is AuthenticationError.INVALID_REQUEST
    # RFC 6750 §3: no error code when no Bearer token was sent.
    challenge = f'Bearer error="{error}"' if error else "Bearer"
    if malformed:
        return Refusal(
            400,
            "invalid_request",
            "The Authorization header is malformed",
            {"WWW-Authenticate": challenge},
        )
    return Refusal(
        401,
        "unauthenticated",
        "A valid access token is required",
        {"WWW-Authenticate": challenge},
    )


# Not an RFC 6750 challenge: another token for the same User has the same
# roles, so there is nothing for the client to retry.
FORBIDDEN = Refusal(403, "forbidden", "Your role does not permit this operation")


def native_error(refusal: Refusal, request_id: UUID) -> JSONResponse:
    """`refusal` in the native API's `ErrorResponse` shape."""
    response = error_response(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        request_id=request_id,
    )
    response.headers.update(refusal.headers)
    return response


def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    """The native JSON error for `kind`."""
    return native_error(failure_refusal(kind), request_id)


# Where the OpenAI-compatible adapter lives; a client's base URL ends here.
COMPATIBLE_PREFIX = "/openai/v1"


def is_compatible(request: Request) -> bool:
    """Whether a request is for the OpenAI-compatible adapter.

    The path is read without `root_path`, as routing reads it, so a service
    behind a path prefix such as `/api` still answers in OpenAI's shape.
    """
    path = request.url.path
    root_path: str = request.scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    return path == COMPATIBLE_PREFIX or path.startswith(f"{COMPATIBLE_PREFIX}/")


def compatible_refusal(refusal: Refusal, request: Request) -> JSONResponse:
    """`refusal` in OpenAI's error shape.

    OpenAI's body has no field for a request ID, so it travels as the
    `X-Request-ID` header, for matching a client's error with the logs.
    """
    return compatible_error(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        headers={**refusal.headers, "X-Request-ID": str(request_id_from(request))},
    )
```

Give `create_app` the model, after `synchronizations`:

```python
    compatible_model: CompatibleModel | None = None,
```

and right after the `FastAPI(...)` line:

```python
    offered_model = compatible_model or CompatibleModel()
```

Make the two identity handlers use the shared refusals:

```python
    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
        return native_error(
            not_authenticated_refusal(refused.error), request_id_from(request)
        )

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(
        request: Request, denied: PermissionDenied
    ) -> JSONResponse:
        logger.info("request not permitted: %s", denied.operation)
        return native_error(FORBIDDEN, request_id_from(request))
```

Let the handler for unknown paths and methods pick the shape by prefix:

```python
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # An unknown path or method: the same error shape as every other
        # route under that prefix, OpenAI's for the compatible adapter.
        code, message = HTTP_ERRORS.get(
            error.status_code, ("http_error", "The request cannot be served")
        )
        refusal = Refusal(error.status_code, code, message, dict(error.headers or {}))
        if is_compatible(request):
            return compatible_refusal(refusal, request)
        return native_error(refusal, request_id_from(request))
```

Add the route before `/health/live`:

```python
    # The OpenAI-compatible adapter (ADR 0004): OpenAI's shapes, so it stays
    # out of the native OpenAPI document.
    @app.get(
        f"{COMPATIBLE_PREFIX}/models", response_model=ModelList, include_in_schema=False
    )
    async def list_compatible_models(request: Request) -> ModelList | JSONResponse:
        # Who, then whether they may: the same checks as the native routes,
        # answered in OpenAI's error shape.
        try:
            caller = await authenticate(request)
            require(caller.roles, Operation.ASK_QUESTIONS)
        except _NotAuthenticated as refused:
            refusal = not_authenticated_refusal(refused.error)
        except _CannotAuthenticate:
            refusal = failure_refusal(FailureKind.DEPENDENCY)
        except PermissionDenied as denied:
            logger.info("request not permitted: %s", denied.operation)
            refusal = FORBIDDEN
        else:
            return model_list(offered_model)
        return compatible_refusal(refusal, request)
```

The route catches the three refusals that `authenticate` and `require` raise, because the app's handlers for them would write the native shape. A refusal is logged the same way as on the native routes, by its operation, never by any token text.

Check: `uv run pytest tests/test_app.py tests/test_app_authentication.py tests/test_api_routes.py -q` passes, except the role test, which Step 5 fixes.

## Step 5 — Teach the role test the new route

Coding step.

**In plain words:** the role test lists every route the app serves, with the operation it needs, and fails on any route it does not know. The new route needs `ask_questions`. It asks no store, so for a permitted caller the test checks the answer itself instead of a store call.

In `tests/test_app_authentication.py`, add to `ROUTES`, and a set after it:

```python
    # The OpenAI-compatible adapter, outside the native route table.
    ("GET", "/openai/v1/models"): Operation.ASK_QUESTIONS,
}
# Routes that ask no store: a permitted caller is seen by the answer alone.
STORELESS = {("GET", "/openai/v1/models")}
```

and in `test_each_protected_route_admits_exactly_the_roles_that_permit_it`, after the 403 assertion:

```python
    if route in STORELESS:
        assert (response.status_code == 200) is allowed
        return
```

The route is not added to `api_routes.ROUTES`: that table is the native `/v1` design.

Check:

```bash
uv run pytest tests/test_app_authentication.py tests/test_api_routes.py -q
```

Expected: all pass.

## Step 6 — Write the protocol tests

Coding step.

**In plain words:** the main test file reads the model list with OpenAI's own Python client, as Open WebUI's server would, and checks each refusal arrives as OpenAI's error with the native code. It also checks the edges: unknown paths, a proxy prefix, the native OpenAPI document, and the configured model's rules. The second file logs real users in through Keycloak and lists the model with their real tokens.

| File | What it proves |
|---|---|
| `tests/test_compatible_models.py` | the SDK lists exactly the one model; the configured model is the only one; the body is byte-identical across app instances; each refusal's exact OpenAI body and challenge; the SDK raises its own error types; 503 with `Retry-After`; no token text in the response or log; unknown paths and methods in OpenAI's shape; the native API keeps its shape; nothing in the native OpenAPI document; ID and name rules; settings; a proxy prefix; `X-Request-ID` |
| `tests/test_compatible_models_integration.py` | each Keycloak demo user's real token lists the model; a token with one character changed is refused |

Create `tests/test_compatible_models.py`:

```python
"""The OpenAI-compatible model list, read by OpenAI's own client.

The protocol test uses the `openai` SDK, as Open WebUI's server would use
the same endpoint: if the SDK reads the list, an OpenAI client can. Every
refusal must reach that client as OpenAI's error shape with the native
code, and the bearer token is checked exactly as on the native routes.
"""

import logging
from typing import Any

import httpx
import httpx2
import openai
import pytest
from pydantic import ValidationError

from knowledge_service.app import create_app
from knowledge_service.compatibility.models import (
    MODEL_CREATED,
    MODEL_OWNER,
    CompatibleModel,
    InvalidCompatibleModel,
)
from knowledge_service.identity.context import Authenticator
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.settings import Settings
from test_authorization_context import MARKER, POLICY, Verifier, verified

TOKENS = {
    "user-token": verified(),
    "staff-token": verified(
        realm_access={"roles": ["content-manager", "evaluator", "administrator"]}
    ),
    MARKER: verified(azp="account-console"),
}
BASE = "http://t/openai/v1"


def app_with(
    *,
    model: CompatibleModel | None = None,
    verifier: Verifier | None = None,
    authenticated: bool = True,
) -> Any:
    authenticator = Authenticator(verifier or Verifier(TOKENS), POLICY)
    return create_app(
        authenticator=authenticator if authenticated else None,
        compatible_model=model,
    )


def sdk(app: Any, token: str) -> openai.AsyncOpenAI:
    """OpenAI's client, pointed at the adapter, sending `token` as its key."""
    return openai.AsyncOpenAI(
        base_url=BASE,
        api_key=token,
        max_retries=0,
        # The SDK's own HTTP library, httpx2, with the app as its transport.
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


async def get(app: Any, path: str, **headers: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(path, headers=headers)


# The list, as OpenAI's client reads it.


async def test_openais_client_lists_exactly_the_one_knowledge_model() -> None:
    page = await sdk(app_with(), "user-token").models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]
    model = page.data[0]
    assert (model.object, model.created, model.owned_by) == (
        "model",
        MODEL_CREATED,
        MODEL_OWNER,
    )
    assert (model.model_extra or {})["name"] == "Knowledge base"


async def test_the_configured_model_is_the_only_one_shown() -> None:
    configured = CompatibleModel(model_id="team-docs:v2", name="Team docs")

    page = await sdk(app_with(model=configured), "user-token").models.list()

    assert [(m.id, (m.model_extra or {})["name"]) for m in page.data] == [
        ("team-docs:v2", "Team docs")
    ]


async def test_the_list_is_the_same_byte_for_byte_after_a_restart() -> None:
    first = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )
    second = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )

    assert first.status_code == 200
    assert first.content == second.content
    assert first.json() == {
        "object": "list",
        "data": [
            {
                "id": "knowledge-base",
                "object": "model",
                "created": MODEL_CREATED,
                "owned_by": MODEL_OWNER,
                "name": "Knowledge base",
            }
        ],
    }


# Bearer authentication, and its refusals in OpenAI's shape.


UNAUTHENTICATED = (
    "authentication_error",
    "unauthenticated",
    "A valid access token is required",
)


@pytest.mark.parametrize(
    ("headers", "status", "error", "challenge"),
    [
        ({}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer not-a-token"},
            401,
            UNAUTHENTICATED,
            'Bearer error="invalid_token"',
        ),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer two words"},
            400,
            (
                "invalid_request_error",
                "invalid_request",
                "The Authorization header is malformed",
            ),
            'Bearer error="invalid_request"',
        ),
        (
            {"Authorization": "Bearer staff-token"},
            403,
            (
                "permission_error",
                "forbidden",
                "Your role does not permit this operation",
            ),
            None,
        ),
    ],
    ids=[
        "no token",
        "unknown token",
        "another scheme",
        "malformed bearer",
        "no user role",
    ],
)
async def test_each_refusal_is_an_openai_error_with_the_native_code(
    headers: dict[str, str],
    status: int,
    error: tuple[str, str, str],
    challenge: str | None,
) -> None:
    response = await get(app_with(), "/openai/v1/models", **headers)

    error_type, code, message = error
    assert response.status_code == status
    assert response.json() == {
        "error": {"message": message, "type": error_type, "param": None, "code": code}
    }
    assert response.headers.get("www-authenticate") == challenge


@pytest.mark.parametrize(
    ("token", "error"),
    [
        ("not-a-token", openai.AuthenticationError),
        ("staff-token", openai.PermissionDeniedError),
    ],
)
async def test_openais_client_raises_its_own_error_for_each_refusal(
    token: str, error: type[openai.APIStatusError]
) -> None:
    with pytest.raises(error) as raised:
        await sdk(app_with(), token).models.list()

    assert raised.value.code == (
        "forbidden" if error is openai.PermissionDeniedError else "unauthenticated"
    )


@pytest.mark.parametrize(
    "app",
    [
        app_with(authenticated=False),
        app_with(verifier=Verifier(error=IdentityProviderUnavailable("down"))),
    ],
    ids=["no authenticator", "identity provider down"],
)
async def test_when_tokens_cannot_be_checked_the_answer_is_503(app: Any) -> None:
    response = await get(app, "/openai/v1/models", Authorization="Bearer user-token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert response.json()["error"]["type"] == "server_error"
    assert response.headers["retry-after"] == "5"


async def test_a_refusal_names_no_token_text_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    # MARKER is a valid token from an untrusted client: refused as unknown.
    response = await get(
        app_with(), "/openai/v1/models", Authorization=f"Bearer {MARKER}"
    )

    assert response.status_code == 401
    assert MARKER not in response.text + str(response.headers) + caplog.text


# The adapter's edges.


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/openai/v1/nothing", 404, "not_found"),
        ("GET", "/openai/v1/models/knowledge-base", 404, "not_found"),
        ("POST", "/openai/v1/models", 405, "method_not_allowed"),
        ("GET", "/openai/v1/models/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_under_the_adapter_is_an_openai_error(
    method: str, path: str, status: int, code: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with()), base_url="http://t"
    ) as http:
        response = await http.request(method, path)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


async def test_the_native_api_keeps_its_own_error_shape() -> None:
    response = await get(app_with(), "/v1/nothing")

    assert set(response.json()) == {"code", "message", "request_id"}


def test_the_adapter_is_not_in_the_native_openapi_document() -> None:
    spec = app_with().openapi()

    assert not any(path.startswith("/openai") for path in spec["paths"])
    assert "ModelList" not in spec.get("components", {}).get("schemas", {})


# The configured model.


@pytest.mark.parametrize(
    "model_id",
    ["", "Knowledge-Base", "knowledge base", "-knowledge", "a" * 65, "kb/1", "kb?"],
)
def test_a_model_id_a_client_could_not_use_as_is_is_refused(model_id: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(model_id=model_id)


@pytest.mark.parametrize(
    "name",
    [
        "",
        " Knowledge",
        "Knowledge\n",
        "a" * 65,
        chr(0x301),
        "Cafe" + chr(0x301),
        chr(0x200B) + chr(0x301),
    ],
    ids=[
        "empty",
        "space",
        "line break",
        "too long",
        "a lone accent",
        "decomposed",
        "nothing visible",
    ],
)
def test_a_model_name_is_visible_text(name: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(name=name)


def test_settings_configure_the_model_and_refuse_a_bad_one() -> None:
    settings = Settings(compatible_model_id="team-docs", compatible_model_name="Team")

    assert settings.compatible_model() == CompatibleModel("team-docs", "Team")
    with pytest.raises(ValidationError):
        Settings(compatible_model_id="Team Docs")


async def test_behind_a_path_prefix_an_unknown_path_is_still_an_openai_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with(), root_path="/api"),
        base_url="http://t",
    ) as http:
        listed = await http.get(
            "/api/openai/v1/models", headers={"Authorization": "Bearer user-token"}
        )
        unknown = await http.get("/api/openai/v1/nothing")

    assert listed.status_code == 200
    assert (unknown.status_code, set(unknown.json())) == (404, {"error"})


@pytest.mark.parametrize(
    "path", ["/openai/v1/models", "/openai/v1/nothing"], ids=["refused", "unknown"]
)
async def test_a_refusal_carries_the_request_id_as_a_header(path: str) -> None:
    request_id = "0b8f1a52-3c1d-4e6f-9a7b-2d4c6e8f0a1b"

    response = await get(app_with(), path, **{"X-Request-ID": request_id})

    assert response.status_code in {401, 404}
    assert response.headers["x-request-id"] == request_id
```

Create `tests/test_compatible_models_integration.py`:

```python
"""The OpenAI-compatible model list with real Keycloak tokens.

A demo user logs in through Keycloak's login form, as Open WebUI's users
will, and OpenAI's own client lists the models with that access token. A
token with one character changed is refused in OpenAI's error shape. The
tests skip themselves when Keycloak or its secrets are absent.
"""

from collections.abc import AsyncIterator

import httpx
import httpx2
import openai
import pytest

from keycloak_flow import CLIENT_ID, ISSUER, SECRETS, local_secrets, log_in
from knowledge_service.app import create_app
from knowledge_service.identity.context import Authenticator, ContextPolicy
from knowledge_service.identity.oidc import OidcVerifier

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in ("alice", "bob", "carol")}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


async def access_token(keycloak: httpx.AsyncClient, username: str) -> str:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    return tokens.access_token


def sdk(keycloak: httpx.AsyncClient, token: str) -> openai.AsyncOpenAI:
    app = create_app(
        authenticator=Authenticator(
            OidcVerifier(ISSUER, AUDIENCE, keycloak),
            ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
        )
    )
    return openai.AsyncOpenAI(
        base_url="http://t/openai/v1",
        api_key=token,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


@pytest.mark.parametrize("username", list(PASSWORDS))
async def test_each_demo_user_sees_the_knowledge_model(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    token = await access_token(keycloak, username)

    page = await sdk(keycloak, token).models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]


async def test_a_token_with_one_character_changed_is_refused(
    keycloak: httpx.AsyncClient,
) -> None:
    token = await access_token(keycloak, "alice")
    # The last character of the signature, so the token still parses.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(openai.AuthenticationError) as raised:
        await sdk(keycloak, tampered).models.list()

    assert raised.value.code == "unauthenticated"
```

OpenAI's client is built on `httpx2`, not on `httpx`, so it gets an `httpx2.AsyncClient` with the app as its transport: no network, the real client code.

Check:

```bash
uv run pytest tests/test_compatible_models.py -q
uv run pytest tests/test_compatible_models_integration.py -q -m integration
```

Expected: `37 passed`, then `4 passed` (the second needs `make keycloak-up`; without Keycloak it skips).

## Step 7 — Drop the role check, on purpose

No coding in this step.

This step proves the tests guard "only a caller with the user role sees the model". In `src/knowledge_service/app.py`, in `list_compatible_models`, delete the line

```python
            require(caller.roles, Operation.ASK_QUESTIONS)
```

and run with bytecode off:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_compatible_models.py -q
```

Expected: `2 failed, 35 passed`: `test_each_refusal_is_an_openai_error_with_the_native_code[no user role]` and `test_openais_client_raises_its_own_error_for_each_refusal[staff-token-PermissionDeniedError]`. A staff member without the user role now sees the model. `tests/test_app_authentication.py` fails too, for every role set without `user`. Put the line back and run again: `37 passed`.

What would falsify the design: an OpenAI client that cannot read the list, a model whose ID or creation time changes between restarts, a caller without the user role who sees the model, a refusal under `/openai/v1` in the native shape or with token text in it, or an OpenAI-shaped schema in the native OpenAPI document.

## Step 8 — Update the architecture and threat model

No coding in this step.

In `docs/architecture.md`, after the paragraph that ends with "native route or schema uses its field names (ADR 0004).", add:

```markdown
`compatibility` is the OpenAI-compatible adapter. It lives under
`/openai/v1`, so an OpenAI client's base URL ends there, outside the
native `/v1` design and out of the native OpenAPI document. Its
`GET /openai/v1/models` needs a bearer token and `ask_questions` (the
user role), checked exactly as on the native routes, and lists one
virtual model: the configured `compatible_model_id` and
`compatible_model_name` (`knowledge-base`, "Knowledge base"), with a
fixed creation time and owner, so a client's saved choice survives
restarts and nothing names the provider or the language model behind
it. Every refusal under `/openai/v1`, unknown paths and methods
included, has OpenAI's error shape, `{"error": {...}}`, with the
native code and message, and the request ID as the `X-Request-ID`
header; the prefix is read without the server's `root_path`.
```

In `docs/threat-model.md`, in the entry points table, replace the row `| OpenAI-compatible adapter, Open WebUI | Users | M09 |` with:

```markdown
| `GET /openai/v1/models` | Any client that reaches the API, Open WebUI among them | Lists the one virtual knowledge model. Needs a bearer token and the `ask_questions` operation (the `user` role), checked as on the native routes; refusals have OpenAI's error shape with the native codes and never quote the token; the list names no provider or language model |
| Chat completions through the adapter, Open WebUI | Users | M09-T05 to M09-T08 |
```

Check: `make docs-check` passes.

## Step 9 — Run the gates and write the evidence

No coding in this step.

Add the new files to git before the hooks run: pre-commit only looks at files git tracks.

```bash
git add -A
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --cached --check
uv run pre-commit run
```

On the prototype these gave: 375 files formatted, all checks passed, 0 errors, docs-check exit 0, `3734 passed` unit, `952 passed` integration with Keycloak's secrets and Redis available, and every hook passed on the staged files.

Then write `docs/evidence/M09-T04-openai-compatible-models.md` in your own words: the invariant and owners, your predictions from Step 1 and what happened, the expected failing check, why the adapter lives at `/openai/v1`, the model's fixed details, the refusal table, the deliberate break, the gate results, and what would falsify the design. The route makes no external call of its own: only the identity provider's key fetch, already bounded by M07. Nothing is stored, so there is nothing to replay.

## Completion checklist

- [ ] `GET /openai/v1/models` lists exactly one model, the configured `knowledge-base` by default, with `created` and `owned_by` fixed.
- [ ] OpenAI's Python client reads the list, and raises its own error types for 401 and 403.
- [ ] Every refusal under `/openai/v1`, unknown paths and methods included, has OpenAI's error shape with the native code and message, the right challenge or `Retry-After`, and `X-Request-ID`.
- [ ] A caller without the user role gets 403; no token text appears in a response or a log.
- [ ] The native API and its OpenAPI document are unchanged, and no `/openai` path or OpenAI schema appears in that document.
- [ ] A bad configured ID or name stops `Settings` from loading.
- [ ] `tests/test_compatible_models.py` (37) and `tests/test_compatible_models_integration.py` (4, with Keycloak) pass.
- [ ] The deliberate break gave 2 failed, 35 passed, and the restored code passes.
- [ ] `docs/architecture.md` and `docs/threat-model.md` are updated, and every gate in Step 9 passes, hooks included.
- [ ] The evidence file is written in your own words.

## Reflection questions

1. The task says `/v1/models`, but the route is `/openai/v1/models`. Explain how an OpenAI client finds the route, and why putting it inside this service's own `/v1` would have broken the M09-T01 design.
2. The model's `created` is a fixed number, not the current time. What would a client see after each restart if it were the current time, and why does that matter for a saved chat in Open WebUI?
3. The model's ID is `knowledge-base`, not the name of the OpenAI model that writes the answers. What changes for users when the service later switches its language model, under each choice?
4. A request without a token reaches the model list. Walk through what Open WebUI would do with the native error body, and what it does with OpenAI's error shape.
5. The native refusal handlers were refactored to share `Refusal` values with the adapter. How does the lesson show that no native response changed, and why does that matter more than the new route's own tests?
6. Behind a proxy that adds `/api` in front of every path, unknown adapter paths got the native error shape. Why did routing still work while the shape check failed?
