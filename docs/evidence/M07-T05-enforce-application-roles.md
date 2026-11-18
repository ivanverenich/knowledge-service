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

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What carol (roles `user` and `administrator`, no groups) gets when she asks a question, and which Documents her search may return: 200 and a streamed answer, because `user` permits `ask_questions`; her reader is only her UserId with no Groups, so her search may return only Documents granted to everyone or to her personally; `administrator` adds nothing to what she reads.
- What a valid token whose only role is `administrator` gets from `POST /v1/answer/stream`, and whether its body is read: 403 `forbidden` with no `WWW-Authenticate` header and the log line `request not permitted: ask_questions`; the body is never read, and the Evidence source is never called.
- What a token whose `realm_access.roles` is the string `"user"` instead of a list gets: 401 with `WWW-Authenticate: Bearer error="invalid_token"`; the whole token is refused as `roles_malformed`, so it does not even get a reader.
- Which tests fail if the line that checks the role in `admit` is deleted: 22 in `tests/test_app_authentication.py`: all 4 cases of `test_a_caller_without_the_user_role_gets_403_and_nothing_runs`, the 16 cases of `test_each_protected_route_admits_exactly_the_roles_that_permit_it` whose role set lacks `user`, and both cases of `test_the_role_is_checked_before_the_body_is_read`.

## Design

- **Policy module.** `src/knowledge_service/roles.py` is a new top-level domain module (listed in ADR 0013). `ApplicationRole` names the four roles as Keycloak writes them (`user`, `content-manager`, `evaluator`, `administrator`); `Operation` names the eight operations from `docs/security.md`; `PERMISSIONS`, a read-only `MappingProxyType`, gives each role exactly two operations. Roles do not inherit, and an operation no role lists is denied.
- **Checks.** `permits(roles, operation)` accepts only an exact `frozenset` of exact `ApplicationRole` values and an exact `Operation`, and raises `TypeError` otherwise, because `"user" == ApplicationRole.USER` in Python. `require` raises `PermissionDenied(operation)`.
- **Where roles come from.** `application_roles` in `identity/context.py` reads only `realm_access.roles` of the verified token. It must be a list of at most 64 strings, or the whole token is refused as `roles_malformed` or `roles_oversized` (401 `invalid_token`). Names that are not Application Roles are left out and counted in the log; `resource_access`, groups, headers, and body fields never give a role.
- **Apart from reading.** `Authenticator.authenticate` returns `Caller(reader, roles)`: `reader` is built from `sub` and `groups` exactly as in M07-T04, and only the reader reaches the Evidence source. `Caller` is frozen and checks its exact types.
- **Order and status codes.** `admit(request, operation)` authenticates (400 or 401), then `require`s the operation (403 `forbidden`, no `WWW-Authenticate`, log line `request not permitted: <operation>`), and only then reads the body. Both answer endpoints need `ask_questions`. An authenticator that returns anything but exactly a `Caller` gives 503.
- **Keycloak.** Roles are mapped to users directly; a config test refuses a realm file that maps roles to groups or uses composite roles.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_roles.py -q` failed with `ModuleNotFoundError: No module named 'knowledge_service.roles'`. After Step 4 and before Step 5, 27 older endpoint tests failed with 503, because the stand-in authenticator returned a bare reader.
- `uv run pytest tests/test_roles.py tests/test_keycloak_config.py tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py -q`: 180 passed (65 matrix, 21 config, 94 older endpoint tests).
- `uv run pytest tests/test_authorization_context.py tests/test_app_authentication.py -q`: 163 passed. `uv run pytest -m integration tests/test_authentication_integration.py -q`: 7 passed against the running Keycloak (alice `{user}`, bob `{user, content-manager}`, carol `{user, administrator}`).
- Gates, with Keycloak running: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2687 passed (+136); `make test-integration` 419 passed (+3); `make docs-check` passed.
- Deliberate break: without `require(caller.roles, operation)` in `admit`, 22 failed, 52 passed in `tests/test_app_authentication.py`: all 4 cases of `test_a_caller_without_the_user_role_gets_403_and_nothing_runs`, the 16 role sets without `user` in `test_each_protected_route_admits_exactly_the_roles_that_permit_it` (each reached the Evidence source or the workflow), and both routes of `test_the_role_is_checked_before_the_body_is_read`. Restored: 74 passed. The prediction was right.
- Known limits: `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the routes; the other seven operations have no entry point yet (Source and sync administration in M09-T02 and M09-T03, routes with role requirements in M09-T01, evaluation in M10); the administrator operations have no planned endpoint; a malformed roles claim refuses the whole token; a `PermissionDenied` raised inside a workflow becomes a 500.

## Review

An independent adversarial review tried role names that only look right (`User`, `USER`, `user `, fullwidth and Cyrillic letters, zero-width characters, `user\0`, enum member names), every claim shape, client roles, groups named like roles, headers, cookies, query parameters, and body fields, and temporary roles, composite roles, and group role mappings in the real Keycloak, all cleaned up afterwards. It could not give anyone a role they should not have, skip the check, or leak token text, and 37 of its mutations were killed. It found these gaps, all fixed with tests that failed before:

- The route test only caught an endpoint with no check at all: an endpoint refusing only callers with no roles, a plain Starlette route, a websocket, and a route under `/health/` all passed. Now `ROUTES` lists every route by hand (`test_every_route_the_app_serves_is_listed_with_its_operation`), and each protected route is tried with all sixteen role sets (`test_each_protected_route_admits_exactly_the_roles_that_permit_it`).
- The before-the-body test covered only the existing endpoints. Now `test_the_role_is_checked_before_the_body_is_read` runs for every protected route in `ROUTES`.
- In Keycloak, a role mapped to a group or contained in a composite role also lands in `realm_access.roles`, so whoever manages groups could hand out roles. Now `test_application_roles_are_given_to_users_only_never_through_groups` refuses such a realm file.
- Three type checks had no test: a `frozenset` subclass passed to `permits`, a `Caller` subclass returned by an authenticator, and a mutable `Caller`. Each now has one (`test_names_and_other_collections_are_refused_not_matched[frozenset-subclass]`, `test_an_authenticator_that_returns_no_caller_is_a_503[caller-subclass]`, `test_a_caller_cannot_be_changed_after_it_is_made`).

Documented, not fixed: the public documentation routes, the whole-token refusal for a malformed roles claim, and a `PermissionDenied` inside a workflow becoming a 500.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **One table, no inheritance.** A table is one place to read and review; an endpoint asks `require` and never compares role names. Without inheritance, giving `user` a new operation never quietly gives it to administrators or evaluators, and carol asks questions only because Keycloak gives her `user` too.
- **Exact types.** `ApplicationRole` is a `StrEnum`, so a raw claim string would match a role in a `frozenset` lookup. Refusing anything but exact types means a role can only come from `application_roles`, which checks the claim.
- **The route list is written by hand.** Generating it from `app.routes` would approve whatever exists. A hand-written list forces a decision for each new route, and the test compares it with what the app really serves.
- **Left for later.** Role checks on the Source, synchronization, conversation, and evaluation routes come with those routes (M09-T01, M09-T02, M09-T03, M10). Source group normalization is M07-T06.

**Falsified assumption.** I assumed that a test sending a token with no roles to every route proves default denial. The review shows that an endpoint refusing only empty role sets, or a route the test did not enumerate (a websocket, a plain Starlette route, one under `/health/`), passed it. So "no roles gets 403 everywhere" is wrong as a proof of the matrix; only listing every route with its operation and trying every role set proves it.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It no longer has to know which roles permit asking, or that roles do not inherit; the table decides. It can no longer compare a raw string with a role, forget a role that also permits the operation, or let an administrator in by mistake.
2. Carol would gain configuring Sources and starting syncs, and managing evaluation datasets and running paid evaluation. Paid evaluation spends money on model calls, so an administrator account, a valuable target, could run up cost without anyone ever deciding she should.
3. The reader must be in the key, because it decides which Evidence an answer may use: without it one User's answer leaks to another. Roles must not be needed in the key, because they never change what is read; if the cache stored only by role, every `user` would share answers built from someone else's Documents.
4. Groups decide Access Grants, so whoever manages group membership already controls reading. If a group also carried a role, that person could hand out operations, such as paid evaluation or Source configuration, without anyone managing roles noticing. Keeping roles on users only keeps the two permissions in separate hands.
5. Bob's `Caller` holds his reader, his UserId and the Group of `/finance`, and the roles `{user, content-manager}`. He may ask questions, manage his own Conversations, configure Sources, and start Synchronization Runs. His search may return Documents granted to everyone, to him, or to `/finance`; content manager adds none.
