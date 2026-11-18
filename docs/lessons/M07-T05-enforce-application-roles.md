# M07-T05 — Enforce Application Roles

Task: [M07-T05 — Enforce Application Roles](../curriculum/milestones/M07-identity-and-authorization/M07-T05-enforce-application-roles.md)

## What you will do

This lesson decides what each kind of person may do with the service, separately from what they may read. M07-T04 gave every request a reader: the User and Groups that decide which Documents a search may return. Now every request also gets its Application Roles from the token, and an endpoint runs only when one of those roles permits its operation.

- **Four roles, written as a table:** user, content manager, evaluator, and administrator each permit a fixed list of operations. Roles do not inherit, and an operation no role lists is denied to everyone.
- **Kept apart from reading:** the token's roles never change the reader. An administrator reads exactly the Documents the same person would read without the role.
- **Checked at the door:** both answer endpoints need the `user` role. A caller without it gets 403 before the request body is read.
- **Every route accounted for:** a test lists every route the app serves with the operation it needs, so a new endpoint fails the tests until someone decides who may call it.

| The task asks for | Where it happens |
|---|---|
| Define user, content manager, evaluator, and administrator permissions | Step 2: `src/knowledge_service/roles.py` |
| Independently from Document Access Grants | Step 3: `Caller` keeps the reader and the roles apart; Step 6 tests it |
| A permission matrix test covering every protected operation and default denial | Step 5: `tests/test_roles.py`; Step 6: every route in `tests/test_app_authentication.py` |
| Tests: success, highest-risk edge, typed failure or denial | Steps 5 and 6 |
| Telemetry holds no seeded sensitive text | Step 6: the marker tests |
| Update only what the change made inaccurate | Step 8: ADR 0013, `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 6 write code and tests. Steps 1, 7, and 8 do not, though Step 8 edits three documents.

Terms used in this lesson:

- **Application Role:** a permission to operate the service, such as asking questions or starting a sync. It never permits reading a Document; only Access Grants do that.
- **Operation:** one thing the service does that needs a role, such as `ask_questions` or `configure_sources`.
- **Permission matrix:** the table of which role permits which operation. Like a key card that opens some doors in a building and not others.
- **Default denial:** anything not explicitly permitted is refused. A new operation, or a new endpoint, is closed until someone opens it on purpose.
- **Realm roles:** the roles Keycloak assigns to a user for the whole realm. The token carries them as `realm_access.roles`, for example `["content-manager", "user"]` for bob.
- **403 versus 401:** 401 means "we do not know who you are"; 403 means "we know who you are, and you may not do this". Logging in again does not change a 403.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Roles are a fixed table, not code in each endpoint.** `PERMISSIONS` in `roles.py` lists, for each role, the operations it permits. An endpoint asks `require(roles, operation)`; it never checks a role name itself. One place to read means one place to review.
- **No inheritance.** An administrator is not automatically a user. Carol can ask questions because Keycloak gives her both roles. Inheritance would quietly widen every role above `user` whenever `user` gains an operation.
- **Exact types only.** `ApplicationRole` is a string enum, and in Python `"user" == ApplicationRole.USER` is true. So `permits` refuses plain strings, other collections, and subclasses with a `TypeError`. Otherwise a claim read straight from a token could skip the mapping that checks it.
- **Roles come only from realm roles.** `application_roles` reads `realm_access.roles`. A name that is not one of the four, such as Keycloak's `offline_access`, is left out and only counted in the log. Client roles (`resource_access`), groups, headers, and body fields never give a role. A malformed or oversized roles claim refuses the whole token, as the `groups` claim does.
- **Reader and roles travel apart.** `Authenticator.authenticate` now returns a `Caller` with two fields: `reader`, built from `sub` and `groups` exactly as before, and `roles`. The Evidence source still gets only the reader. Nothing about roles can reach a search.
- **The order at the door.** First identity (401 or 400), then the role (403), then the body. A caller without the role cannot make the service read or parse anything they send.
- **403 has no challenge.** RFC 6750's `insufficient_scope` is for OAuth scopes a client could request again. A role belongs to the User, so another token would not help; the response is a plain 403 `forbidden`.
- **Only one operation has an endpoint today.** `ask_questions` is enforced on both answer endpoints. The other seven operations exist in the table, so their rules are decided and tested now, and each gets its check when its endpoint is built.
- **What the review found.** An independent adversarial review tried role names that only look right (`User`, `user `, fullwidth and Cyrillic letters, zero-width characters), every claim shape, client roles, groups named like roles, headers, cookies, query parameters, and body fields, and temporary roles and groups in the real Keycloak. It could not give anyone a role they should not have, skip the check, or leak token text. It found these gaps, all fixed with tests that failed before:
  - The route test only caught an endpoint with no check at all. An endpoint that refused only callers with no roles, a route added another way (a plain Starlette route, a websocket), or one under `/health/` all passed. Now a hand-written list names every route with its operation, and each protected route is tried with all sixteen role combinations.
  - The before-the-body test covered only the existing endpoints. Now it runs for every protected route in the list.
  - In Keycloak, a role mapped to a group, or a role that contains another, also lands in `realm_access.roles`. Whoever manages group membership, which also decides Access Grants, could then hand out roles. Now a config test refuses a realm file that maps roles to groups or uses composite roles.
  - Three type checks had no test: a `frozenset` subclass passed to `permits`, a `Caller` subclass returned by an authenticator, and a `Caller` that could be changed after it was made. Each now has one.
- **Known limits.** `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; the production entry point decides whether to serve them. The other seven operations have no endpoint yet; configuring Sources, syncing, and evaluation are scripts a developer runs without identity. A `PermissionDenied` raised deep inside a workflow becomes a 500, not a 403, because it does not escape the endpoint.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 7 checks the last prediction.

Create `docs/evidence/M07-T05-enforce-application-roles.md`:

```markdown
# M07-T05 — Enforce Application Roles evidence

## Invariant

A request may run an operation only when one of its caller's Application
Roles permits it in `PERMISSIONS`; roles do not inherit, and an operation no
role lists is denied. Roles come only from the verified token's
`realm_access.roles`, and they never change the Authorization Context, so no
role permits reading a Document. Both answer endpoints need `ask_questions`,
checked after identity and before the body is read; a caller without it gets
403. Every route the app serves is listed with its operation or as public.
No token, claim, or role text reaches a response or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_roles.py -q` fails with
`ModuleNotFoundError: No module named 'knowledge_service.roles'`.

## Predictions

- What carol (roles `user` and `administrator`, no groups) gets when she asks a question, and which Documents her search may return:
- What a valid token whose only role is `administrator` gets from `POST /v1/answer/stream`, and whether its body is read:
- What a token whose `realm_access.roles` is the string `"user"` instead of a list gets:
- Which tests fail if the line that checks the role in `admit` is deleted:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the role policy

**In plain words:** this file is the rule book. It names the four roles and the eight things the service can be asked to do, and it says, in one table, which role allows which thing. Any code that wants to know "may this person do that?" asks this file, and gets either a yes or a typed refusal.

| Part | What it does |
|---|---|
| `ApplicationRole` | The four roles, by the names Keycloak puts in the token |
| `Operation` | The eight operations from `docs/security.md` |
| `PERMISSIONS` | The matrix: each role and the operations it permits; read-only |
| `permits` | Whether any of the roles permits the operation; refuses names, other collections, and subclasses |
| `require` | The same check, raising `PermissionDenied` with the operation |

Create `src/knowledge_service/roles.py`:

```python
"""Application Roles: what a caller may do with the service.

A role permits operations on the service itself, such as asking questions
or starting a Synchronization Run. It never permits reading a Document:
that is decided only by Access Grants and the Authorization Context. Roles
do not inherit from each other, and an operation no role lists is denied
to everyone.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ApplicationRole(StrEnum):
    """A role the identity provider assigns, by its name in the token."""

    USER = "user"
    CONTENT_MANAGER = "content-manager"
    EVALUATOR = "evaluator"
    ADMINISTRATOR = "administrator"


class Operation(StrEnum):
    """Something the service does that needs an Application Role."""

    ASK_QUESTIONS = "ask_questions"
    MANAGE_OWN_CONVERSATIONS = "manage_own_conversations"
    CONFIGURE_SOURCES = "configure_sources"
    START_SYNCHRONIZATION_RUNS = "start_synchronization_runs"
    MANAGE_EVALUATION_DATASETS = "manage_evaluation_datasets"
    RUN_PAID_EVALUATION = "run_paid_evaluation"
    MANAGE_POLICY = "manage_policy"
    MANAGE_OPERATIONAL_CONFIGURATION = "manage_operational_configuration"


PERMISSIONS: Mapping[ApplicationRole, frozenset[Operation]] = MappingProxyType(
    {
        ApplicationRole.USER: frozenset(
            {Operation.ASK_QUESTIONS, Operation.MANAGE_OWN_CONVERSATIONS}
        ),
        ApplicationRole.CONTENT_MANAGER: frozenset(
            {Operation.CONFIGURE_SOURCES, Operation.START_SYNCHRONIZATION_RUNS}
        ),
        ApplicationRole.EVALUATOR: frozenset(
            {Operation.MANAGE_EVALUATION_DATASETS, Operation.RUN_PAID_EVALUATION}
        ),
        ApplicationRole.ADMINISTRATOR: frozenset(
            {Operation.MANAGE_POLICY, Operation.MANAGE_OPERATIONAL_CONFIGURATION}
        ),
    }
)


class PermissionDenied(Exception):
    """None of the caller's roles permits `operation`."""

    def __init__(self, operation: Operation) -> None:
        super().__init__(f"not permitted: {operation}")
        self.operation = operation


def permits(roles: frozenset[ApplicationRole], operation: Operation) -> bool:
    """Whether any of `roles` lists `operation`.

    Only exact `ApplicationRole` and `Operation` values are accepted: a
    plain string equal to a role's name would otherwise match it, and a
    claim read straight from a token would skip the mapping that checks it.
    """
    if type(operation) is not Operation:
        raise TypeError("the operation must be an Operation")
    if type(roles) is not frozenset:
        raise TypeError("the roles must be a frozenset")
    if any(type(role) is not ApplicationRole for role in roles):
        raise TypeError("every role must be an ApplicationRole")
    return any(operation in PERMISSIONS[role] for role in roles)


def require(roles: frozenset[ApplicationRole], operation: Operation) -> None:
    """Return if any of `roles` permits `operation`, else `PermissionDenied`."""
    if not permits(roles, operation):
        raise PermissionDenied(operation)
```

`PERMISSIONS` is a `MappingProxyType`, a read-only view of a dictionary, so no code can add an operation to a role while the service runs.

**Check:** `uv run pyright src/knowledge_service/roles.py` reports `0 errors`.

### Step 3 — Coding step: roles from the token, apart from the reader

**In plain words:** this step teaches the door from M07-T04 to read the token's roles too. It picks the four known role names out of the token's realm roles, ignores any other name, and refuses a roles claim that has the wrong shape or is far too long. The result of the door is now a caller: what they may read, unchanged, and separately what they may do.

| Change | What it does |
|---|---|
| Module docstring | Says the door gives a `Caller` and that roles never change the reader |
| `ClaimDefect` | Two new reasons: `roles_malformed`, `roles_oversized` |
| `ContextPolicy.max_roles` | At most 64 role names, like groups |
| `Caller` | The reader and the roles, checked for their exact types, frozen |
| `application_roles` | Reads `realm_access.roles`, keeps the four known names, counts the rest |
| `Authenticator.authenticate` | Returns a `Caller` instead of a bare `AuthorizationContext` |

In `src/knowledge_service/identity/context.py`:

**1.** Find:

```python
"""Turn a request's bearer token into the Authorization Context.

`Authenticator.authenticate` is the only way a request gets an
`AuthorizationContext`: it reads one `Authorization: Bearer` header, has the
token verified, and maps the verified claims to typed identifiers. Nothing
else in a request, no other header and no field of the body, names the User
or their Groups.

The mapping is deterministic and needs no storage. A User is the issuer and
the token's `sub`; a Group is the issuer and one full group path, such as
`/engineering`. Each becomes a UUIDv5, so the same person or group always
gets the same ID, and the same name from another issuer never collides with
it. A group path no Access Grant names simply matches nothing.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
```

Replace with:

```python
"""Turn a request's bearer token into the Authorization Context and roles.

`Authenticator.authenticate` is the only way a request gets a `Caller`: it
reads one `Authorization: Bearer` header, has the token verified, and maps
the verified claims to typed identifiers and Application Roles. Nothing
else in a request, no other header and no field of the body, names the User,
their Groups, or their roles.

The mapping is deterministic and needs no storage. A User is the issuer and
the token's `sub`; a Group is the issuer and one full group path, such as
`/engineering`. Each becomes a UUIDv5, so the same person or group always
gets the same ID, and the same name from another issuer never collides with
it. A group path no Access Grant names simply matches nothing.

Roles come from the provider's realm roles and are kept apart from the
Authorization Context: a role never changes what the caller may read.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
```

**2.** Find:

```python
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.oidc import TokenRejected, VerifiedToken

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
```

Replace with:

```python
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.oidc import TokenRejected, VerifiedToken
from knowledge_service.roles import ApplicationRole

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
```

**3.** Find:

```python

    ISSUER = "issuer"
    SUBJECT = "subject"
    UNTRUSTED_CLIENT = "untrusted_client"
    GROUPS_MALFORMED = "groups_malformed"
    GROUPS_OVERSIZED = "groups_oversized"


class TokenVerifier(Protocol):
    """Verifies a bearer token; `OidcVerifier` is the implementation."""

    async def verify(self, token: str) -> VerifiedToken: ...
```

Replace with:

```python

    ISSUER = "issuer"
    SUBJECT = "subject"
    UNTRUSTED_CLIENT = "untrusted_client"
    GROUPS_MALFORMED = "groups_malformed"
    GROUPS_OVERSIZED = "groups_oversized"
    ROLES_MALFORMED = "roles_malformed"
    ROLES_OVERSIZED = "roles_oversized"


class TokenVerifier(Protocol):
    """Verifies a bearer token; `OidcVerifier` is the implementation."""

    async def verify(self, token: str) -> VerifiedToken: ...
```

**4.** Find:

```python
@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_name_length: int = 255

    def __post_init__(self) -> None:
        if type(self.trusted_clients) is not frozenset or not self.trusted_clients:
            raise ValueError("trusted_clients must be a non-empty frozenset")
        if self.max_groups <= 0 or self.max_name_length <= 0:
            raise ValueError("limits must be positive")


def user_id_for(issuer: str, subject: str) -> UserId:
    """The User an issuer's subject is, the same for every token."""
    return UserId(uuid5(IDENTITY_NAMESPACE, f"user\x00{issuer}\x00{subject}"))

```

Replace with:

```python
@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_roles: int = 64
    max_name_length: int = 255

    def __post_init__(self) -> None:
        if type(self.trusted_clients) is not frozenset or not self.trusted_clients:
            raise ValueError("trusted_clients must be a non-empty frozenset")
        if min(self.max_groups, self.max_roles, self.max_name_length) <= 0:
            raise ValueError("limits must be positive")


@dataclass(frozen=True, slots=True)
class Caller:
    """One request's caller: what they may read, and what they may do.

    `reader` alone decides which Documents they may see; `roles` alone
    decide which operations they may run.
    """

    reader: AuthorizationContext
    roles: frozenset[ApplicationRole]

    def __post_init__(self) -> None:
        if type(self.reader) is not AuthorizationContext:
            raise TypeError("the reader must be an AuthorizationContext")
        if type(self.roles) is not frozenset:
            raise TypeError("the roles must be a frozenset")
        if any(type(role) is not ApplicationRole for role in self.roles):
            raise TypeError("every role must be an ApplicationRole")


def user_id_for(issuer: str, subject: str) -> UserId:
    """The User an issuer's subject is, the same for every token."""
    return UserId(uuid5(IDENTITY_NAMESPACE, f"user\x00{issuer}\x00{subject}"))

```

**5.** Find:

```python
        group_ids=frozenset(
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
        ),
    )


class Authenticator:
    """Turns one request's `Authorization` header into its context."""

    def __init__(self, verifier: TokenVerifier, policy: ContextPolicy) -> None:
        self._verifier = verifier
        self._policy = policy

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        """The context for the request's `Authorization` header values.

        Raises `Unauthenticated` for no Bearer header, more than one header,
        a malformed one, a token the verifier rejects, or claims that cannot
        be mapped; the verifier's `IdentityProviderUnavailable` passes
        through.
        """
```

Replace with:

```python
        group_ids=frozenset(
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
        ),
    )


def application_roles(
    token: VerifiedToken, policy: ContextPolicy
) -> frozenset[ApplicationRole]:
    """The Application Roles in the token's realm roles, or `Unauthenticated`.

    `realm_access.roles`, if present, must be a list of at most `max_roles`
    strings. A name that is not an Application Role, such as Keycloak's
    `offline_access`, is left out and counted; roles of single clients
    (`resource_access`) are never read.
    """
    access: object = token.claims.get("realm_access", {})
    if not isinstance(access, Mapping):
        raise _defect(ClaimDefect.ROLES_MALFORMED)
    listed: object = cast("Mapping[str, object]", access).get("roles", [])
    if not isinstance(listed, list):
        raise _defect(ClaimDefect.ROLES_MALFORMED)
    names = cast("list[object]", listed)
    if len(names) > policy.max_roles:
        raise _defect(ClaimDefect.ROLES_OVERSIZED)
    named: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            raise _defect(ClaimDefect.ROLES_MALFORMED)
        named.add(name)
    known = {role.value: role for role in ApplicationRole}
    roles = frozenset(known[name] for name in named if name in known)
    if ignored := len(named - known.keys()):
        # A count only, as for groups.
        logger.warning("role claim entries ignored: %d", ignored)
    return roles


class Authenticator:
    """Turns one request's `Authorization` header into its caller."""

    def __init__(self, verifier: TokenVerifier, policy: ContextPolicy) -> None:
        self._verifier = verifier
        self._policy = policy

    async def authenticate(self, headers: list[str]) -> Caller:
        """The caller for the request's `Authorization` header values.

        Raises `Unauthenticated` for no Bearer header, more than one header,
        a malformed one, a token the verifier rejects, or claims that cannot
        be mapped; the verifier's `IdentityProviderUnavailable` passes
        through.
        """
```

**6.** Find:

```python
            )
        try:
            verified = await self._verifier.verify(found[1])
        except TokenRejected as rejected:
            reason = str(rejected.reason)
        else:
            return build_authorization_context(verified, self._policy)
        # Raised outside `except`, so nothing about the token is chained.
        raise Unauthenticated(reason, error=AuthenticationError.INVALID_TOKEN)


def _group_paths(claims: Mapping[str, Any], policy: ContextPolicy) -> set[str]:
    """The distinct plain full group paths in the `groups` claim."""
```

Replace with:

```python
            )
        try:
            verified = await self._verifier.verify(found[1])
        except TokenRejected as rejected:
            reason = str(rejected.reason)
        else:
            return Caller(
                reader=build_authorization_context(verified, self._policy),
                roles=application_roles(verified, self._policy),
            )
        # Raised outside `except`, so nothing about the token is chained.
        raise Unauthenticated(reason, error=AuthenticationError.INVALID_TOKEN)


def _group_paths(claims: Mapping[str, Any], policy: ContextPolicy) -> set[str]:
    """The distinct plain full group paths in the `groups` claim."""
```

`application_roles` gets the claims after `OidcVerifier` checked the signature, so they come from Keycloak. The checks are there because Keycloak's configuration can change, and an unexpected shape must refuse, not crash.

**Check:** `uv run pyright src/knowledge_service/identity` reports `0 errors`.

### Step 4 — Coding step: the endpoints require the role

**In plain words:** this step makes both answer endpoints ask the rule book before doing anything. After the door says who is calling, the endpoint checks that the caller's roles allow asking questions. If not, it answers 403 and stops, without reading what was sent. Only the reader part of the caller goes on to the search.

| Change | What it does |
|---|---|
| Imports | `Caller` from the door; `Operation`, `PermissionDenied`, `require` from the rule book |
| `RequestAuthenticator` | Now returns a `Caller` |
| `admit(request, operation)` | Identity, then `require`, then the body |
| `authenticate` | Returns the `Caller`; anything that is not exactly a `Caller` is a 503 |
| `PermissionDenied` handler | 403 `forbidden`, no `WWW-Authenticate`, and a log line naming only the operation |
| Both endpoints | Ask for `Operation.ASK_QUESTIONS`; the streamed one passes `caller.reader` on |

In `src/knowledge_service/app.py`:

**1.** Find:

```python
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identity.context import AuthenticationError, Unauthenticated
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.workflow import AnswerWorkflow

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]
# A Question is at most 4,000 characters; even escaped as JSON it fits.
MAX_BODY_BYTES = 65_536


class RequestAuthenticator(Protocol):
    """Gives a request's Authorization Context from its `Authorization` headers.

    `Authenticator` is the implementation; it raises `Unauthenticated`, or
    `IdentityProviderUnavailable` when tokens cannot be checked.
    """

    async def authenticate(self, headers: list[str]) -> AuthorizationContext: ...


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error

```

Replace with:

```python
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]
# A Question is at most 4,000 characters; even escaped as JSON it fits.
MAX_BODY_BYTES = 65_536


class RequestAuthenticator(Protocol):
    """Gives a request's caller from its `Authorization` headers.

    `Authenticator` is the implementation; it raises `Unauthenticated`, or
    `IdentityProviderUnavailable` when tokens cannot be checked.
    """

    async def authenticate(self, headers: list[str]) -> Caller: ...


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error

```

**2.** Find:

```python
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
) -> FastAPI:
    """The HTTP application.

    Both answer endpoints need a bearer token: without an `authenticator`
    they answer 503, and the reader is only ever the context it returns.
    """
    app = FastAPI(title="knowledge-service")

    async def admit(request: Request) -> tuple[AuthorizationContext, Question]:
        """The reader, then the Question: the body is not read without a reader."""
        reader = await authenticate(request)
        return reader, await read_question(request)

    async def authenticate(request: Request) -> AuthorizationContext:
        if authenticator is None:
            raise _CannotAuthenticate
        # Every Authorization header, whatever the case of its name.
        headers = [
            value.decode("latin-1")
            for name, value in request.headers.raw
            if name.lower() == b"authorization"
        ]
        try:
            return await authenticator.authenticate(headers)
        except Unauthenticated as refused:
            logger.info("request not authenticated: %s", refused.reason)
            error = refused.error
        except IdentityProviderUnavailable:
            logger.warning("request not authenticated: identity provider unavailable")
            raise _CannotAuthenticate from None
        except Exception as unexpected:
            # Only the type: the message may quote the token or its claims.
            logger.error("authentication failed: %s", type(unexpected).__name__)
            raise _CannotAuthenticate from None
        raise _NotAuthenticated(error)

    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
```

Replace with:

```python
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
) -> FastAPI:
    """The HTTP application.

    Both answer endpoints need a bearer token and the role that permits
    asking: without an `authenticator` they answer 503, and the reader is
    only ever the context it returns.
    """
    app = FastAPI(title="knowledge-service")

    async def admit(request: Request, operation: Operation) -> tuple[Caller, Question]:
        """The caller, their permission, then the Question, in that order.

        The body is not read until the caller is known and permitted.
        """
        caller = await authenticate(request)
        require(caller.roles, operation)
        return caller, await read_question(request)

    async def authenticate(request: Request) -> Caller:
        if authenticator is None:
            raise _CannotAuthenticate
        # Every Authorization header, whatever the case of its name.
        headers = [
            value.decode("latin-1")
            for name, value in request.headers.raw
            if name.lower() == b"authorization"
        ]
        try:
            caller = await authenticator.authenticate(headers)
        except Unauthenticated as refused:
            logger.info("request not authenticated: %s", refused.reason)
            error = refused.error
        except IdentityProviderUnavailable:
            logger.warning("request not authenticated: identity provider unavailable")
            raise _CannotAuthenticate from None
        except Exception as unexpected:
            # Only the type: the message may quote the token or its claims.
            logger.error("authentication failed: %s", type(unexpected).__name__)
            raise _CannotAuthenticate from None
        else:
            if type(caller) is not Caller:
                logger.error("authentication failed: not a Caller")
                raise _CannotAuthenticate
            return caller
        raise _NotAuthenticated(error)

    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
```

**3.** Find:

```python
        )
        # RFC 6750 §3: no error code when no Bearer token was sent.
        response.headers["WWW-Authenticate"] = (
            f'Bearer error="{refused.error}"' if refused.error else "Bearer"
        )
        return response

    @app.exception_handler(_CannotAuthenticate)
    async def cannot_authenticate_handler(
        request: Request, _error: _CannotAuthenticate
    ) -> JSONResponse:
        return failure_response(
```

Replace with:

```python
        )
        # RFC 6750 §3: no error code when no Bearer token was sent.
        response.headers["WWW-Authenticate"] = (
            f'Bearer error="{refused.error}"' if refused.error else "Bearer"
        )
        return response

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(
        request: Request, denied: PermissionDenied
    ) -> JSONResponse:
        logger.info("request not permitted: %s", denied.operation)
        # Not an RFC 6750 challenge: another token for the same User has the
        # same roles, so there is nothing for the client to retry.
        return error_response(
            status_code=403,
            code="forbidden",
            message="Your role does not permit this operation",
            request_id=request_id_from(request),
        )

    @app.exception_handler(_CannotAuthenticate)
    async def cannot_authenticate_handler(
        request: Request, _error: _CannotAuthenticate
    ) -> JSONResponse:
        return failure_response(
```

**4.** Find:

```python
            content={"status": "ready"},
        )

    @app.post("/v1/answer", response_model=Answer)
    async def answer(request: Request) -> Answer | JSONResponse:
        # The ungrounded M01 path searches nothing, so it needs proof of
        # identity but no reader; M09-T01 replaces it.
        _reader, question = await admit(request)
        request_id = request_id_from(request)

        if workflow is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
```

Replace with:

```python
            content={"status": "ready"},
        )

    @app.post("/v1/answer", response_model=Answer)
    async def answer(request: Request) -> Answer | JSONResponse:
        # The ungrounded M01 path searches nothing, so it needs proof of
        # identity and the role, but no reader; M09-T01 replaces it.
        _caller, question = await admit(request, Operation.ASK_QUESTIONS)
        request_id = request_id_from(request)

        if workflow is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
```

**5.** Find:

```python
                kind=classify_failure(error),
                request_id=request_id,
            )

    @app.post("/v1/answer/stream")
    async def answer_stream(request: Request) -> Response:
        reader, question = await admit(request)
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request, streamer.open(question, reader=reader, request_id=request_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
```

Replace with:

```python
                kind=classify_failure(error),
                request_id=request_id,
            )

    @app.post("/v1/answer/stream")
    async def answer_stream(request: Request) -> Response:
        caller, question = await admit(request, Operation.ASK_QUESTIONS)
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request,
                streamer.open(question, reader=caller.reader, request_id=request_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
```

**Check:** `uv run pyright src` reports `0 errors`. Of the older endpoint tests, 27 now fail with 503, because the stand-in authenticator still returns a bare reader instead of a `Caller`; Step 5 fixes it.

### Step 5 — Coding step: the permission matrix and the stand-in caller

**In plain words:** this step writes the rule book's own test: a hand-made copy of the table that every role and operation is checked against, so a change to either fails until someone updates both. It also gives the stand-in authenticator from M07-T04 a proper caller with the `user` role, so the older tests pass again, and it pins the Keycloak rule that roles are given to users directly, never through groups.

| File | Change |
|---|---|
| `tests/identity_fakes.py` | `CALLER`: `READER` with the `user` role; `StaticAuthenticator` returns it |
| `tests/test_roles.py` | The matrix, all sixteen role combinations, default denial, no inheritance, exact types only, read-only table |
| `tests/test_keycloak_config.py` | No group carries roles and no role is composite |

In `tests/identity_fakes.py`:

Find:

```python
"""A stand-in authenticator for endpoint tests that are not about identity.

It accepts one fixed bearer token and gives one fixed reader, so tests of
streaming and failures can call the protected endpoints. The identity tests
use the real `Authenticator`.
"""

from uuid import UUID

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import AuthenticationError, Unauthenticated

READER = AuthorizationContext(
    user_id=UserId(UUID(int=0xA11CE)), group_ids=frozenset({GroupId(UUID(int=0xE))})
)
AUTHORIZATION = {"Authorization": "Bearer reader-token"}


class StaticAuthenticator:
    """Gives `READER` for `AUTHORIZATION`, and refuses anything else."""

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        if headers == [AUTHORIZATION["Authorization"]]:
            return READER
        error = (
            AuthenticationError.INVALID_TOKEN if headers else AuthenticationError.NONE
        )
        raise Unauthenticated("test", error=error)


```

Replace with:

```python
"""A stand-in authenticator for endpoint tests that are not about identity.

It accepts one fixed bearer token and gives one fixed caller, a reader with
the `user` role, so tests of streaming and failures can call the protected
endpoints. The identity tests
use the real `Authenticator`.
"""

from uuid import UUID

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.roles import ApplicationRole

READER = AuthorizationContext(
    user_id=UserId(UUID(int=0xA11CE)), group_ids=frozenset({GroupId(UUID(int=0xE))})
)
CALLER = Caller(reader=READER, roles=frozenset({ApplicationRole.USER}))
AUTHORIZATION = {"Authorization": "Bearer reader-token"}


class StaticAuthenticator:
    """Gives `CALLER` for `AUTHORIZATION`, and refuses anything else."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if headers == [AUTHORIZATION["Authorization"]]:
            return CALLER
        error = (
            AuthenticationError.INVALID_TOKEN if headers else AuthenticationError.NONE
        )
        raise Unauthenticated("test", error=error)


```

Create `tests/test_roles.py`:

```python
"""The permission matrix: which Application Role permits which operation.

`MATRIX` is written out by hand, so a change to `PERMISSIONS` or a new
`Operation` fails here until someone decides who may run it.
"""

from itertools import combinations
from typing import Any, cast

import pytest

from knowledge_service.roles import (
    PERMISSIONS,
    ApplicationRole,
    Operation,
    PermissionDenied,
    permits,
    require,
)

USER = ApplicationRole.USER
CONTENT_MANAGER = ApplicationRole.CONTENT_MANAGER
EVALUATOR = ApplicationRole.EVALUATOR
ADMINISTRATOR = ApplicationRole.ADMINISTRATOR

MATRIX: dict[Operation, set[ApplicationRole]] = {
    Operation.ASK_QUESTIONS: {USER},
    Operation.MANAGE_OWN_CONVERSATIONS: {USER},
    Operation.CONFIGURE_SOURCES: {CONTENT_MANAGER},
    Operation.START_SYNCHRONIZATION_RUNS: {CONTENT_MANAGER},
    Operation.MANAGE_EVALUATION_DATASETS: {EVALUATOR},
    Operation.RUN_PAID_EVALUATION: {EVALUATOR},
    Operation.MANAGE_POLICY: {ADMINISTRATOR},
    Operation.MANAGE_OPERATIONAL_CONFIGURATION: {ADMINISTRATOR},
}
ROLE_SETS = [
    frozenset(chosen)
    for size in range(len(ApplicationRole) + 1)
    for chosen in combinations(ApplicationRole, size)
]


def test_the_matrix_names_every_operation_and_every_role() -> None:
    assert set(MATRIX) == set(Operation)
    assert set(PERMISSIONS) == set(ApplicationRole)


@pytest.mark.parametrize("operation", list(Operation))
@pytest.mark.parametrize("role", list(ApplicationRole))
def test_each_role_permits_exactly_its_operations(
    role: ApplicationRole, operation: Operation
) -> None:
    assert permits(frozenset({role}), operation) is (role in MATRIX[operation])


@pytest.mark.parametrize(
    "roles", ROLE_SETS, ids=lambda roles: "+".join(sorted(roles)) or "none"
)
def test_several_roles_permit_what_any_one_of_them_does(
    roles: frozenset[ApplicationRole],
) -> None:
    for operation, allowed in MATRIX.items():
        assert permits(roles, operation) is bool(roles & allowed)


@pytest.mark.parametrize("operation", list(Operation))
def test_no_role_permits_nothing(operation: Operation) -> None:
    with pytest.raises(PermissionDenied) as denied:
        require(frozenset(), operation)

    assert denied.value.operation is operation


def test_an_administrator_is_not_a_user() -> None:
    # Roles do not inherit: carol asks questions because she is also a user.
    with pytest.raises(PermissionDenied):
        require(frozenset({ADMINISTRATOR}), Operation.ASK_QUESTIONS)

    require(frozenset({ADMINISTRATOR, USER}), Operation.ASK_QUESTIONS)


@pytest.mark.parametrize(
    ("roles", "operation"),
    [
        (frozenset({"user"}), Operation.ASK_QUESTIONS),
        (frozenset({USER}), "ask_questions"),
        ({USER}, Operation.ASK_QUESTIONS),
        ([USER], Operation.ASK_QUESTIONS),
        (type("Roles", (frozenset,), {})({USER}), Operation.ASK_QUESTIONS),
        (frozenset({USER, "administrator"}), Operation.MANAGE_POLICY),
    ],
    ids=["role-name", "operation-name", "set", "list", "frozenset-subclass", "mixed"],
)
def test_names_and_other_collections_are_refused_not_matched(
    roles: Any, operation: Any
) -> None:
    # "user" == ApplicationRole.USER in Python; only the exact types count.
    with pytest.raises(TypeError):
        permits(roles, operation)


def test_the_permissions_cannot_be_changed_at_run_time() -> None:
    with pytest.raises(TypeError):
        cast(Any, PERMISSIONS)[USER] = frozenset(Operation)

    assert PERMISSIONS[USER] == frozenset(
        {Operation.ASK_QUESTIONS, Operation.MANAGE_OWN_CONVERSATIONS}
    )
```

In `tests/test_keycloak_config.py`:

Find:

```python
        for user in config["users"]
    } == {
        "alice": (["/engineering"], ["user"]),
        "bob": (["/finance"], ["user", "content-manager"]),
        "carol": ([], ["user", "administrator"]),
    }


def test_each_demo_user_keeps_one_id_across_resets() -> None:
    ids = [UUID(user["id"]) for user in realm()["users"]]

    assert len(set(ids)) == len(ids) == 3
```

Replace with:

```python
        for user in config["users"]
    } == {
        "alice": (["/engineering"], ["user"]),
        "bob": (["/finance"], ["user", "content-manager"]),
        "carol": ([], ["user", "administrator"]),
    }


def test_application_roles_are_given_to_users_only_never_through_groups() -> None:
    # Groups also decide Access Grants. A role mapped to a group, or a role
    # that contains another, would let whoever manages one hand out the other.
    config = realm()

    assert all("realmRoles" not in group for group in config["groups"])
    assert all("clientRoles" not in group for group in config["groups"])
    assert all(not role.get("composite") for role in config["roles"]["realm"])


def test_each_demo_user_keeps_one_id_across_resets() -> None:
    ids = [UUID(user["id"]) for user in realm()["users"]]

    assert len(set(ids)) == len(ids) == 3
```

**Check:**

```bash
uv run pytest tests/test_roles.py tests/test_keycloak_config.py tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py -q
```

Expected: `180 passed`: 65 for the matrix, 21 for the config, and the 94 older endpoint tests.

### Step 6 — Coding step: roles at the door and at every route

**In plain words:** this step tests the roles where they matter: at the door and at the endpoints. It checks that the right role names become roles and every other shape is refused or ignored, that roles never change what a caller reads, that every route the app serves is listed with what it needs, and that a caller without the role is stopped before anything runs or anything is read. One more test logs in alice, bob, and carol through the real Keycloak and checks their roles.

| File | Tests |
|---|---|
| `tests/test_authorization_context.py` | Test tokens get the `user` role; realm roles to Application Roles; unknown names counted, client roles and groups ignored; malformed and oversized claims refused; roles never change the reader; `Caller` takes exact types and cannot be changed; the marker test covers role names |
| `tests/test_app_authentication.py` | 403 without the role on both endpoints; every route listed with its operation; each protected route tried with all sixteen role sets; the role checked before the body on every protected route; an administrator reads what the same user reads; an authenticator returning anything but a `Caller` is a 503 |
| `tests/test_authentication_integration.py` | alice, bob, and carol have exactly the roles the realm gives them |

In `tests/test_authorization_context.py`:

**1.** Find:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import (
    IDENTITY_NAMESPACE,
    AuthenticationError,
    Authenticator,
    ContextPolicy,
    Unauthenticated,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import (
    IdentityProviderUnavailable,
    Rejection,
    TokenRejected,
    VerifiedToken,
)

ISSUER = "http://localhost:8180/realms/knowledge-service"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
POLICY = ContextPolicy(trusted_clients=frozenset({"open-webui"}))
MARKER = "SEEDED-SECRET-91c2"

```

Replace with:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import (
    IDENTITY_NAMESPACE,
    AuthenticationError,
    Authenticator,
    Caller,
    ContextPolicy,
    Unauthenticated,
    application_roles,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import (
    IdentityProviderUnavailable,
    Rejection,
    TokenRejected,
    VerifiedToken,
)
from knowledge_service.roles import ApplicationRole

ISSUER = "http://localhost:8180/realms/knowledge-service"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
POLICY = ContextPolicy(trusted_clients=frozenset({"open-webui"}))
MARKER = "SEEDED-SECRET-91c2"

```

**2.** Find:

```python
    """A token the verifier accepted, with these claims over alice's."""
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": ALICE,
        "azp": "open-webui",
        "groups": ["/engineering"],
        **claims,
    }
    payload = {name: value for name, value in payload.items() if value is not None}
    return VerifiedToken(
        subject=payload.get("sub", ALICE),
        key_id="key-1",
```

Replace with:

```python
    """A token the verifier accepted, with these claims over alice's."""
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": ALICE,
        "azp": "open-webui",
        "groups": ["/engineering"],
        "realm_access": {"roles": ["user"]},
        **claims,
    }
    payload = {name: value for name, value in payload.items() if value is not None}
    return VerifiedToken(
        subject=payload.get("sub", ALICE),
        key_id="key-1",
```

**3.** Find:

```python
        return self.tokens[token]


def defect(token: VerifiedToken) -> str:
    with pytest.raises(Unauthenticated) as raised:
        build_authorization_context(token, POLICY)
    return raised.value.reason


# --- Mapping claims to IDs ------------------------------------------------------------


```

Replace with:

```python
        return self.tokens[token]


def defect(token: VerifiedToken) -> str:
    with pytest.raises(Unauthenticated) as raised:
        build_authorization_context(token, POLICY)
    return raised.value.reason


def role_defect(token: VerifiedToken) -> str:
    with pytest.raises(Unauthenticated) as raised:
        application_roles(token, POLICY)
    assert raised.value.error is AuthenticationError.INVALID_TOKEN
    return raised.value.reason


# --- Mapping claims to IDs ------------------------------------------------------------


```

**4.** Find:

```python
@pytest.mark.parametrize(
    "settings",
    [
        {"trusted_clients": frozenset[str]()},
        {"trusted_clients": {"open-webui"}},
        {"trusted_clients": frozenset({"open-webui"}), "max_groups": 0},
    ],
)
def test_a_policy_that_trusts_no_client_or_allows_nothing_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        ContextPolicy(**settings)


# --- The Authorization header ---------------------------------------------------------


async def test_a_bearer_token_gives_the_context_of_its_claims() -> None:
    verifier = Verifier({"good-token": verified()})

    context = await Authenticator(verifier, POLICY).authenticate(["Bearer good-token"])

    assert context.user_id == user_id_for(ISSUER, ALICE)
    assert verifier.seen == ["good-token"]


@pytest.mark.parametrize(
    "header", ["Bearer good-token", "bearer good-token", "BEARER  good-token"]
)
```

Replace with:

```python
@pytest.mark.parametrize(
    "settings",
    [
        {"trusted_clients": frozenset[str]()},
        {"trusted_clients": {"open-webui"}},
        {"trusted_clients": frozenset({"open-webui"}), "max_groups": 0},
        {"trusted_clients": frozenset({"open-webui"}), "max_roles": 0},
    ],
)
def test_a_policy_that_trusts_no_client_or_allows_nothing_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        ContextPolicy(**settings)


# --- Application Roles: what the caller may do, never what they may read -------------


USER = ApplicationRole.USER
CONTENT_MANAGER = ApplicationRole.CONTENT_MANAGER
ADMINISTRATOR = ApplicationRole.ADMINISTRATOR
ALICE_READER = AuthorizationContext(
    user_id=user_id_for(ISSUER, ALICE),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)


@pytest.mark.parametrize(
    ("names", "roles"),
    [
        (["user"], {USER}),
        (["content-manager", "user"], {USER, CONTENT_MANAGER}),
        (["administrator", "user", "user"], {USER, ADMINISTRATOR}),
        ([], set[ApplicationRole]()),
    ],
    ids=["alice", "bob", "carol-listed-twice", "none"],
)
def test_realm_roles_become_application_roles(
    names: list[str], roles: set[ApplicationRole]
) -> None:
    token = verified(realm_access={"roles": names})

    assert application_roles(token, POLICY) == roles


@pytest.mark.parametrize(
    "claims",
    [
        {"realm_access": None},
        {"realm_access": {}},
    ],
    ids=["no-realm-access", "no-roles-list"],
)
def test_a_token_without_realm_roles_has_no_roles(claims: dict[str, Any]) -> None:
    assert application_roles(verified(**claims), POLICY) == frozenset()


def test_roles_that_are_not_application_roles_are_left_out_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    names = [
        "user",
        "offline_access",
        "default-roles-knowledge-service",
        "Administrator",
        "admin",
        "administrator ",
    ]

    roles = application_roles(verified(realm_access={"roles": names}), POLICY)

    assert roles == {USER}
    assert caplog.messages == ["role claim entries ignored: 5"]


def test_a_client_role_is_not_an_application_role() -> None:
    token = verified(
        realm_access={"roles": ["user"]},
        resource_access={"open-webui": {"roles": ["administrator"]}},
    )

    assert application_roles(token, POLICY) == {USER}


def test_a_group_named_like_a_role_gives_no_role() -> None:
    token = verified(groups=["/administrator"], realm_access={"roles": ["user"]})

    assert application_roles(token, POLICY) == {USER}


@pytest.mark.parametrize(
    "access",
    [
        ["user"],
        "user",
        {"roles": None},
        {"roles": "user"},
        {"roles": {"user": True}},
        {"roles": ["user", 7]},
        {"roles": ["user", None]},
        {"roles": [["administrator"]]},
    ],
    ids=[
        "list",
        "string",
        "roles-null",
        "roles-string",
        "roles-object",
        "number",
        "null",
        "nested",
    ],
)
def test_a_realm_roles_claim_that_is_not_a_list_of_names_gives_no_caller(
    access: object,
) -> None:
    assert role_defect(verified(realm_access=access)) == "roles_malformed"


def test_too_many_roles_give_no_caller() -> None:
    names = ["user", *(f"role-{n}" for n in range(64))]

    assert role_defect(verified(realm_access={"roles": names})) == "roles_oversized"


@pytest.mark.parametrize(
    "names",
    [[], ["user"], ["administrator"], ["user", "content-manager", "evaluator"]],
)
async def test_roles_never_change_what_the_caller_may_read(names: list[str]) -> None:
    token = verified(realm_access={"roles": names})
    verifier = Verifier({"token": token})

    caller = await Authenticator(verifier, POLICY).authenticate(["Bearer token"])

    assert caller.reader == ALICE_READER


@pytest.mark.parametrize(
    ("reader", "roles"),
    [
        ("alice", frozenset({USER})),
        (ALICE_READER, {USER}),
        (ALICE_READER, frozenset({"user"})),
        (ALICE_READER, frozenset({USER, "administrator"})),
    ],
    ids=["reader-name", "roles-set", "role-name", "mixed"],
)
def test_a_caller_takes_only_a_context_and_application_roles(
    reader: Any, roles: Any
) -> None:
    with pytest.raises(TypeError):
        Caller(reader=reader, roles=roles)


def test_a_caller_cannot_be_changed_after_it_is_made() -> None:
    caller = Caller(reader=ALICE_READER, roles=frozenset({USER}))

    with pytest.raises(AttributeError):
        caller.roles = frozenset(ApplicationRole)  # type: ignore[misc]

    assert caller.roles == {USER}


# --- The Authorization header ---------------------------------------------------------


async def test_a_bearer_token_gives_the_context_of_its_claims() -> None:
    verifier = Verifier({"good-token": verified()})

    caller = await Authenticator(verifier, POLICY).authenticate(["Bearer good-token"])

    assert caller.reader.user_id == user_id_for(ISSUER, ALICE)
    assert verifier.seen == ["good-token"]


@pytest.mark.parametrize(
    "header", ["Bearer good-token", "bearer good-token", "BEARER  good-token"]
)
```

**5.** Find:

```python

    seen = " ".join(
        f"{error!s} {error!r} {error.__context__!r} {error.__cause__!r}"
        for error in errors
    )
    ignored = await Authenticator(
        Verifier({"groups": verified(groups=[f"/{MARKER}~"])}), POLICY
    ).authenticate(["Bearer groups"])

    assert ignored.group_ids == frozenset()
    assert MARKER not in seen + caplog.text
```

Replace with:

```python

    seen = " ".join(
        f"{error!s} {error!r} {error.__context__!r} {error.__cause__!r}"
        for error in errors
    )
    ignored = await Authenticator(
        Verifier(
            {
                "names": verified(
                    groups=[f"/{MARKER}~"], realm_access={"roles": [MARKER]}
                )
            }
        ),
        POLICY,
    ).authenticate(["Bearer names"])

    assert ignored.reader.group_ids == frozenset()
    assert ignored.roles == frozenset()
    assert MARKER not in seen + caplog.text
```

In `tests/test_app_authentication.py`:

**1.** Find:

```python
"""Only the verifier's output can make a request's Authorization Context.

The app gets the real `Authenticator` over a fake verifier. Each test checks
what the Evidence source was asked as, so a reader that came from anywhere
but a verified token would show up here.
"""

import logging
from typing import Any, cast

import httpx
import pytest

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import Authenticator, group_id_for, user_id_for
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
    ALICE,
    ISSUER,
    MARKER,
    POLICY,
    Verifier,
    verified,
)
from test_streaming import QUESTION, REQUEST_ID, Model, Source, streamer

ALICE_CONTEXT = AuthorizationContext(
    user_id=user_id_for(ISSUER, ALICE),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
TOKENS = {
    "alice-token": verified(),
    "outsider-token": verified(azp="account-console"),
    "crowded-token": verified(groups=[f"/g{n}" for n in range(65)]),
}


class Workflow:
    """Counts the ungrounded answers it was asked for."""

```

Replace with:

```python
"""Only the verifier's output can make a request's caller.

The app gets the real `Authenticator` over a fake verifier. Each test checks
what the Evidence source was asked as, so a reader that came from anywhere
but a verified token would show up here, and that a caller without the role
an endpoint needs is refused before anything runs.
"""

import logging
from typing import Any, cast

import httpx
import pytest

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
    ALICE,
    ISSUER,
    MARKER,
    POLICY,
    Verifier,
    verified,
)
from test_roles import MATRIX, ROLE_SETS
from test_streaming import QUESTION, REQUEST_ID, Model, Source, streamer

ALICE_CONTEXT = AuthorizationContext(
    user_id=user_id_for(ISSUER, ALICE),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
TOKENS = {
    "alice-token": verified(),
    "outsider-token": verified(azp="account-console"),
    "crowded-token": verified(groups=[f"/g{n}" for n in range(65)]),
    "roleless-token": verified(realm_access={"roles": []}),
    "staff-token": verified(
        realm_access={"roles": ["content-manager", "evaluator", "administrator"]}
    ),
    "admin-user-token": verified(realm_access={"roles": ["administrator", "user"]}),
}


class Workflow:
    """Counts the ungrounded answers it was asked for."""

```

**2.** Find:

```python
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_request"'
    assert source.readers == []


def raw_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }
```

Replace with:

```python
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_request"'
    assert source.readers == []


def raw_scope(
    headers: list[tuple[bytes, bytes]], path: str = "/v1/answer/stream"
) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }
```

**3.** Find:

```python
    assert source.readers == []


class Broken:
    """An authenticator that fails in a way nobody planned for."""

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        raise KeyError(MARKER)


async def test_an_unexpected_authenticator_error_is_a_503_naming_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = Source()
```

Replace with:

```python
    assert source.readers == []


class Broken:
    """An authenticator that fails in a way nobody planned for."""

    async def authenticate(self, headers: list[str]) -> Caller:
        raise KeyError(MARKER)


class Careless:
    """An authenticator that returns something other than exactly a `Caller`."""

    def __init__(self, returned: object) -> None:
        self.returned = returned

    async def authenticate(self, headers: list[str]) -> Caller:
        return cast(Caller, self.returned)


class LooseCaller(Caller):
    """A subclass could change what `roles` or `reader` answer."""

    __slots__ = ()


async def test_an_unexpected_authenticator_error_is_a_503_naming_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = Source()
```

**4.** Find:

```python

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert MARKER not in response.text + caplog.text
    assert "authentication failed: KeyError" in caplog.messages
    assert source.readers == []


async def test_the_streamer_refuses_a_reader_that_is_not_a_context() -> None:
    with pytest.raises(InvalidAnswerRequest):
        await streamer(Model()).open(
```

Replace with:

```python

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert MARKER not in response.text + caplog.text
    assert "authentication failed: KeyError" in caplog.messages
    assert source.readers == []


@pytest.mark.parametrize(
    "returned",
    [ALICE_CONTEXT, LooseCaller(reader=ALICE_CONTEXT, roles=frozenset())],
    ids=["bare-reader", "caller-subclass"],
)
async def test_an_authenticator_that_returns_no_caller_is_a_503(
    returned: object, caplog: pytest.LogCaptureFixture
) -> None:
    source = Source()
    app = create_app(
        streamer=streamer(Model(), source=source), authenticator=Careless(returned)
    )

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert "authentication failed: not a Caller" in caplog.messages
    assert source.readers == []


async def test_the_streamer_refuses_a_reader_that_is_not_a_context() -> None:
    with pytest.raises(InvalidAnswerRequest):
        await streamer(Model()).open(
```

**5.** Find:

```python

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 401
    assert workflow.calls == 0


# --- When tokens cannot be checked: 503, never a guess --------------------------------


async def test_an_unavailable_identity_provider_is_a_503_with_retry_after() -> None:
```

Replace with:

```python

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 401
    assert workflow.calls == 0


# --- Application Roles: who may ask, never what they may read -------------------------


@pytest.mark.parametrize("token", ["roleless-token", "staff-token"])
@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_a_caller_without_the_user_role_gets_403_and_nothing_runs(
    path: str, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = Source()
    workflow = Workflow()
    app = create_app(
        workflow=cast(AnswerWorkflow, workflow),
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(TOKENS), POLICY),
    )

    async with client(app) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert "www-authenticate" not in response.headers
    assert (source.readers, workflow.calls) == ([], 0)
    assert "request not permitted: ask_questions" in caplog.messages


# Every route the app serves, written out by hand, and the operation it needs;
# None marks a route anyone may call. A new route fails the first test below
# until someone decides who may call it.
ROUTES: dict[tuple[str, str], Operation | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health/ready"): None,
    ("GET", "/openapi.json"): None,
    ("GET", "/docs"): None,
    ("GET", "/docs/oauth2-redirect"): None,
    ("GET", "/redoc"): None,
    ("POST", "/v1/answer"): Operation.ASK_QUESTIONS,
    ("POST", "/v1/answer/stream"): Operation.ASK_QUESTIONS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]


class Counting:
    """A workflow that records that it was reached, then stops."""

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: Any, *, request_id: Any) -> Any:
        self.calls += 1
        raise RuntimeError("reached")


def full_app(
    source: Source, workflow: Any = None, tokens: dict[str, Any] | None = None
) -> Any:
    """The app with both answer endpoints wired, so every route is real."""
    return create_app(
        workflow=cast(AnswerWorkflow, workflow or Counting()),
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
    )


def test_every_route_the_app_serves_is_listed_with_its_operation() -> None:
    served: set[tuple[str, str]] = set()
    for route in full_app(Source()).routes:
        # A websocket or mounted route has no methods, and must be listed too.
        methods: set[str] = getattr(route, "methods", None) or {"ANY"}
        served |= {(method, getattr(route, "path", "?")) for method in methods}

    assert served - {("HEAD", path) for _, path in ROUTES} == set(ROUTES)


@pytest.mark.parametrize(
    "roles", ROLE_SETS, ids=lambda roles: "+".join(sorted(roles)) or "none"
)
@pytest.mark.parametrize(
    ("route", "operation"), PROTECTED, ids=[path for (_, path), _ in PROTECTED]
)
async def test_each_protected_route_admits_exactly_the_roles_that_permit_it(
    route: tuple[str, str],
    operation: Operation,
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow = Source(), Counting()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token})
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    assert bool(source.readers or workflow.calls) is allowed


@pytest.mark.parametrize("path", [path for (_, path), _ in PROTECTED])
async def test_the_role_is_checked_before_the_body_is_read(path: str) -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer roleless-token")]
    await full_app(Source())(raw_scope(headers, path), receive, send)

    assert sent[0]["status"] == 403
    assert read == []


async def test_an_administrator_reads_exactly_what_the_same_user_reads() -> None:
    plain, admin = Source(), Source()

    await ask(app_with(plain), {"Authorization": "Bearer alice-token"})
    response = await ask(app_with(admin), {"Authorization": "Bearer admin-user-token"})

    assert response.status_code == 200
    assert admin.readers == plain.readers == [ALICE_CONTEXT]


# --- When tokens cannot be checked: 503, never a guess --------------------------------


async def test_an_unavailable_identity_provider_is_a_503_with_retry_after() -> None:
```

`ROUTES` is the permission matrix for endpoints. It is written by hand on purpose: the first test compares it with what the app really serves, so a new route of any kind fails until it is listed, and a listed route with an operation is then tried with every role set.

In `tests/test_authentication_integration.py`:

**1.** Find:

```python
"""The whole path against the local Keycloak (`make keycloak-up`).

A demo user logs in through the login form; their access token goes to the
app, and the Evidence source sees exactly their User and Groups. The tests
skip themselves when Keycloak or its generated secrets are missing.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
```

Replace with:

```python
"""The whole path against the local Keycloak (`make keycloak-up`).

A demo user logs in through the login form; their access token goes to the
app, and the Evidence source sees exactly their User and Groups, whatever
Application Roles they also hold. The tests skip themselves when Keycloak or
its generated secrets are missing.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
```

**2.** Find:

```python
    Authenticator,
    ContextPolicy,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import OidcVerifier
from test_streaming import QUESTION, Model, Source, streamer

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
USERS: dict[str, tuple[str, list[str]]] = {
    "alice": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01", ["/engineering"]),
    "bob": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02", ["/finance"]),
    "carol": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03", []),
}
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in USERS}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
```

Replace with:

```python
    Authenticator,
    ContextPolicy,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import OidcVerifier
from knowledge_service.roles import ApplicationRole
from test_streaming import QUESTION, Model, Source, streamer

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
USERS: dict[str, tuple[str, list[str]]] = {
    "alice": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01", ["/engineering"]),
    "bob": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02", ["/finance"]),
    "carol": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03", []),
}
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in USERS}
ROLES = {
    "alice": {ApplicationRole.USER},
    "bob": {ApplicationRole.USER, ApplicationRole.CONTENT_MANAGER},
    "carol": {ApplicationRole.USER, ApplicationRole.ADMINISTRATOR},
}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
```

**3.** Find:

```python
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


def app_for(keycloak: httpx.AsyncClient, source: Source) -> Any:
    authenticator = Authenticator(
        OidcVerifier(ISSUER, AUDIENCE, keycloak),
        ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
    )
    return create_app(
        streamer=streamer(Model(), source=source), authenticator=authenticator
    )


async def ask(app: Any, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
```

Replace with:

```python
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


def authenticator_for(keycloak: httpx.AsyncClient) -> Authenticator:
    return Authenticator(
        OidcVerifier(ISSUER, AUDIENCE, keycloak),
        ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
    )


def app_for(keycloak: httpx.AsyncClient, source: Source) -> Any:
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=authenticator_for(keycloak),
    )


async def ask(app: Any, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
```

**4.** Find:

```python
    source = Source()
    app = app_for(keycloak, source)

    assert (await ask(app, tokens.id_token)).status_code == 401
    assert (await ask(app, None)).status_code == 401
    assert source.readers == []
```

Replace with:

```python
    source = Source()
    app = app_for(keycloak, source)

    assert (await ask(app, tokens.id_token)).status_code == 401
    assert (await ask(app, None)).status_code == 401
    assert source.readers == []


@pytest.mark.parametrize("username", list(USERS))
async def test_each_demo_user_has_exactly_the_roles_the_realm_gives_them(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )

    caller = await authenticator_for(keycloak).authenticate(
        [f"Bearer {tokens.access_token}"]
    )

    assert caller.roles == ROLES[username]
```

**Check:** run the narrow tests first:

```bash
uv run pytest tests/test_authorization_context.py tests/test_app_authentication.py -q
uv run pytest -m integration tests/test_authentication_integration.py -q
```

Expected: `163 passed`, then `7 passed` with Keycloak running (`7 skipped` without it).

### Step 7 — No coding in this step: break it on purpose

This step removes the role check from the endpoints, watches the right tests fail, and puts it back.

**Action 1:** in `admit` in `src/knowledge_service/app.py`, delete this line:

```python
        require(caller.roles, operation)
```

**Action 2:** run `uv run pytest tests/test_app_authentication.py -q`. Expected: `22 failed, 52 passed`:

- `test_a_caller_without_the_user_role_gets_403_and_nothing_runs`, all 4 cases
- `test_each_protected_route_admits_exactly_the_roles_that_permit_it`, the 16 cases whose role set has no `user`
- `test_the_role_is_checked_before_the_body_is_read`, both routes

The second test shows the damage: an evaluator or a caller with no role at all would reach the Evidence source.

**Action 3:** put the line back and run the tests again: `74 passed`.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 8 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. ADR 0013 lists the top-level domain modules, so it gains `roles`; the architecture and the threat model describe the role check and its limits.

**Action 1:** update three documents.

In `docs/adr/0013-group-code-into-packages-by-module.md`:

Find:

```markdown
| `knowledge_service.ingestion.parsers` | The format parsers and the structural blocks they produce |
| `knowledge_service.embeddings` | The EmbeddingModel port and its OpenAI adapter, batching, publication, index configurations, model migrations |
| `knowledge_service.retrieval` | Vector search, retrieval filters, the vector benchmark |
| `knowledge_service.persistence` | `schema` (every table, the one `metadata` Alembic compares) and one module per kind of record |
| `knowledge_service.identity` | Who is calling: the OIDC token verifier (M07-T03), and the Authorization Context built from its result |

Domain values (`identifiers`, `access`, `documents`, `chunks`, `language`,
`audit`, `jobs`, `conversations`, `evaluation`), configuration, the database
runtime, and the HTTP application stay at the top level, where every module
may import them.

## Consequences

- Imports name the module that owns the code, such as
```

Replace with:

```markdown
| `knowledge_service.ingestion.parsers` | The format parsers and the structural blocks they produce |
| `knowledge_service.embeddings` | The EmbeddingModel port and its OpenAI adapter, batching, publication, index configurations, model migrations |
| `knowledge_service.retrieval` | Vector search, retrieval filters, the vector benchmark |
| `knowledge_service.persistence` | `schema` (every table, the one `metadata` Alembic compares) and one module per kind of record |
| `knowledge_service.identity` | Who is calling: the OIDC token verifier (M07-T03), and the Authorization Context built from its result |

Domain values (`identifiers`, `access`, `roles`, `documents`, `chunks`,
`language`, `audit`, `jobs`, `conversations`, `evaluation`), configuration, the database
runtime, and the HTTP application stay at the top level, where every module
may import them.

## Consequences

- Imports name the module that owns the code, such as
```

In `docs/architecture.md`:

Find:

```markdown
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

Replace with:

```markdown
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
`PERMISSIONS` lists the operations each role permits, roles do not inherit,
an operation no role lists is denied, and no role permits reading a
Document. Both answer endpoints `require` the `ask_questions` operation
before they read the body, and refuse with 403 otherwise.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

In `docs/threat-model.md`:

**1.** Find:

```markdown

## Actors

| Actor | Trust | What they may try |
|---|---|---|
| User | Authenticated, reads only granted Documents | Read Documents not granted to them; learn that one exists; spend others' budget |
| Content manager, Evaluator, Administrator | Application Roles; never imply Document access | Use a role to read content the role does not grant |
| Document author | Writes into a Source; never trusted | Plant instructions, fake citations, invisible characters, or huge files in a Document |
| Model provider and its replies | Approved, but replies are untrusted data | Return invented Citations, unsupported claims, or malformed JSON; keep what it was sent |
| Unauthenticated client | None | Call the API without an identity; flood it |
| Operator | Reads logs and dashboards | See Question, Evidence, or Answer text in telemetry |
| Supply-chain attacker | None | Ship a hostile dependency or image |

```

Replace with:

```markdown

## Actors

| Actor | Trust | What they may try |
|---|---|---|
| User | Authenticated, reads only granted Documents | Read Documents not granted to them; learn that one exists; spend others' budget |
| Content manager, Evaluator, Administrator | Application Roles from the token's realm roles; never imply Document access | Use a role to read content the role does not grant |
| Document author | Writes into a Source; never trusted | Plant instructions, fake citations, invisible characters, or huge files in a Document |
| Model provider and its replies | Approved, but replies are untrusted data | Return invented Citations, unsupported claims, or malformed JSON; keep what it was sent |
| Unauthenticated client | None | Call the API without an identity; flood it |
| Operator | Reads logs and dashboards | See Question, Evidence, or Answer text in telemetry |
| Supply-chain attacker | None | Ship a hostile dependency or image |

```

**2.** Find:

```markdown
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token, checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
```

Replace with:

```markdown
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
```

**3.** Find:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | Source group normalization (M07-T06) |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

```

Replace with:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | Source group normalization (M07-T06) |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

```

**4.** Find:

```markdown
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; Source groups get their own mapping in M07-T06. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
```

Replace with:

```markdown
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; Source groups get their own mapping in M07-T06. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
```

**Action 2:** run the gates in order, with Keycloak running:

```bash
uv run pytest tests/test_roles.py tests/test_authorization_context.py tests/test_app_authentication.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2687 passed for `make test-unit`, 136 more than M07-T04, and 419 passed for `make test-integration`, 3 more.

**Action 3:** complete the evidence file:

- **Design:** the roles and operations, where roles come from, how they stay apart from the reader, the order at the door, and the status codes.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why a table and no inheritance, why exact types, and why the route list is written by hand; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T05 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T06, and commit with `m07-t05: enforce application roles`.

## Reflection questions

1. An endpoint calls `require(caller.roles, Operation.ASK_QUESTIONS)` instead of checking `"user" in roles`. What does it no longer have to know, and what mistake can it no longer make?
2. Someone proposes that an administrator should automatically have every other role. Which operations would carol gain, and why could that be dangerous for the operation that costs money?
3. A later change caches answers to speed things up. Should the cache key include the caller's roles, the reader, or both? Explain what would leak or break in each wrong choice.
4. Keycloak lets an admin map a role to a group. Why did we forbid that in the realm file, given that groups also decide which Documents someone may read?
5. Bob is a content manager and a user, in `/finance`. Write what his `Caller` holds, which operations he may run, and which Documents his search may return.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `knowledge_service.roles` defines the four roles, the eight operations, and a read-only table, with no inheritance and exact types only.
- [ ] The door returns a `Caller`: the reader exactly as before, and the roles from `realm_access.roles` only.
- [ ] Both answer endpoints need `ask_questions`, checked after identity and before the body; a caller without it gets 403.
- [ ] The older tests pass with the stand-in caller, and the matrix and config tests pass (180).
- [ ] `tests/test_authorization_context.py` and `tests/test_app_authentication.py` pass (163), and the integration tests pass against Keycloak (7).
- [ ] You ran the deliberate break, saw the 22 tests fail, and undid it.
- [ ] ADR 0013, `docs/architecture.md`, and `docs/threat-model.md` describe the roles and their limits.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
