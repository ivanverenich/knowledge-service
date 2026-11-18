# M07-T04 — Build the Authorization Context evidence

## Invariant

A request to `POST /v1/answer` or `POST /v1/answer/stream` gets an
Authorization Context only from one `Authorization: Bearer` header whose
token `OidcVerifier` accepts, issued to a trusted client: its User is the
issuer and `sub`, its Groups the issuer and each plain full group path, as
UUIDv5 IDs. No other header, cookie, or body field can name or widen it, and
the body is not read before it exists. Malformed headers get 400, missing or
bad tokens 401, an unreachable identity provider or a missing authenticator
503. No token or claim text reaches a response or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_app_authentication.py -q` fails
with `ModuleNotFoundError: No module named 'knowledge_service.identity.context'`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a request with no Authorization header and a body of `{not json` gets, before and after the change: before, 422, because the body is parsed and nobody asks who is calling; after, 401 with `WWW-Authenticate: Bearer` and no error code, and the body is never read.
- What carol's Authorization Context holds, and what a question about an engineering Document returns for her: her UserId (UUIDv5 of the issuer and her `sub`) and an empty set of Groups; once the database search filters by the reader (M07-T07/T08), a Document granted only to `/engineering` matches nothing for her, so she gets no Evidence from it.
- What happens to a user whose token lists `/engineering` and `/fin~ance`: the request succeeds; the context keeps only the `/engineering` Group, `/fin~ance` is left out, and the log gets `group claim entries ignored: 1` without the name.
- Which test fails if paths with `~` are kept: in `tests/test_authorization_context.py`, 4 fail: both `test_a_group_path_that_is_not_plain_is_left_out_and_counted` cases `[escaped-slash]` and `[tilde]`, `test_a_nested_group_and_a_name_with_a_slash_are_never_one_group`, and `test_no_token_or_claim_text_reaches_errors_or_logs`.

## Design

- **Module.** `src/knowledge_service/identity/context.py` holds `Authenticator`, `build_authorization_context`, `ContextPolicy`, and the ID functions. `Authenticator.authenticate` is the only code that creates an `AuthorizationContext` for a request.
- **IDs from names.** A User is `uuid5(IDENTITY_NAMESPACE, "user\0<issuer>\0<sub>")` and a Group is `uuid5(IDENTITY_NAMESPACE, "group\0<issuer>\0<path>")`, with the namespace fixed forever. Paths keep their case and are not normalized; a path no Access Grant names matches nothing.
- **Claim rules.** `iss` must be a non-empty string. `azp` must be a trusted client (`open-webui`). `sub` must be a visible name of at most 255 characters. `groups`, if present, must be a list of at most 64 strings, or the whole token is refused. A path that is not plain (no leading `/`, only `/`, a `~`, a hidden character, too long, or spaces at either end) is left out, and only the count is logged.
- **Keycloak.** `KC_SPI_GROUP__JPA__ESCAPE_SLASHES_IN_GROUP_PATH: "true"` makes Keycloak write a `/` inside a group name as `~/`; because `~` itself is not escaped, paths with `~` are left out.
- **Order.** Both answer endpoints take only the `Request`. `admit` first collects every `Authorization` header in any case and authenticates, and only then `read_question` reads at most 64 KiB and parses a `Question`. `AnswerStreamer.open` and `EvidenceSource.find` take `reader: AuthorizationContext`, and `open` refuses anything else.
- **Status codes (RFC 6750).** No header or another scheme: 401 with a bare `WWW-Authenticate: Bearer`. Two headers or a malformed one: 400 `invalid_request`. A rejected token or unusable claims: 401 `invalid_token`. An unreachable identity provider, an unexpected authenticator error, or no authenticator at all: 503. A body over 64 KiB: 413. An invalid body after authentication: 422.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_app_authentication.py -q` failed with `ModuleNotFoundError: No module named 'knowledge_service.identity.context'`. After Step 4 and before Step 5, the existing endpoint tests failed (64 failed, 30 passed), because their apps had no authenticator and `open` got no `reader`.
- `uv run pytest tests/test_keycloak_config.py -q`: 20 passed. Keycloak was recreated with the setting (`KC_SPI_GROUP__JPA__ESCAPE_SLASHES_IN_GROUP_PATH=true` in the container) and reported `Healthy`; the volume kept its data.
- `uv run pytest tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py -q`: 94 passed with the stand-in authenticator.
- `uv run pytest tests/test_authorization_context.py tests/test_app_authentication.py -q`: 93 passed. `uv run pytest -m integration tests/test_authentication_integration.py -q`: 4 passed against the running Keycloak (alice, bob, and carol become exactly their User and Groups; an ID token or no token reads nothing).
- Gates, with Keycloak running: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2551 passed (+94); `make test-integration` 416 passed (+4); `make docs-check` passed.
- Deliberate break: without `and "~" not in path` in `_plain_path`, 4 failed, 57 passed: `test_a_group_path_that_is_not_plain_is_left_out_and_counted[escaped-slash]` and `[tilde]`, `test_a_nested_group_and_a_name_with_a_slash_are_never_one_group` (a token listing `/a~/b` got the Group of `b` inside `a`), and `test_no_token_or_claim_text_reaches_errors_or_logs`. Restored: 61 passed. The prediction was right.
- Known limits: no production entry point assembles the app yet, and whoever does must pass an `Authenticator`; no Evidence source over the database takes the reader yet (M07-T07, M07-T08); Source groups get their own mapping in M07-T06; a group path with `~` or a hidden character gives its member nothing; the API schema no longer lists the request body.

## Review

An independent adversarial review tried spoofed headers, query parameters, cookies, and body fields, other clients' real tokens, fuzzed claims, raw ASGI requests, and the real Keycloak. It found no way to choose or widen the reader. It found these problems, all fixed, each with a test that failed before the fix:

- Keycloak gave a top-level group named `zzr18/child` and group `child` inside `zzr18` the same path, so both became one Group. Now Keycloak escapes slashes and paths with `~` are left out (`test_a_nested_group_and_a_name_with_a_slash_are_never_one_group`, `test_a_slash_inside_a_group_name_is_escaped_in_its_path`).
- FastAPI read and parsed the body before authentication: an anonymous request could send 125 MiB, and broken JSON got 422 instead of 401. Now identity comes first and the body is limited to 64 KiB (`test_a_body_is_not_read_before_the_reader_is_known`, `test_identity_is_decided_before_the_body_is_parsed`, `test_a_body_without_a_length_is_cut_off_at_the_limit`).
- Names with characters that print as nothing, such as the Hangul filler, passed as visible. Now only letters, marks, digits, punctuation, symbols, and inner spaces count, minus a list of blank characters (`test_a_subject_that_is_not_a_printable_name_gives_no_context`).
- One bad group locked its member out entirely. Now only that group is left out (`test_a_group_path_that_is_not_plain_is_left_out_and_counted`).
- Refusals did not follow RFC 6750: a `Basic` header got an error code, and a malformed header got 401. Now another scheme gets a bare challenge and a malformed header gets 400 (`test_a_malformed_authorization_header_is_a_400`).
- An unexpected error inside the authenticator became a 500. Now it is a 503 that logs only the error's type (`test_an_unexpected_authenticator_error_is_a_503_naming_only_its_type`).
- Two `Authorization` headers whose names differed in case were not both seen. Now every header is compared in lower case (`test_header_names_in_any_case_are_all_seen`).
- Two mutations survived every test: the streamer no longer checking its reader, and group paths folded to lower case. Each now has a test (`test_the_streamer_refuses_a_reader_that_is_not_a_context`, `test_group_paths_keep_their_case`).

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **IDs come from names, without a table.** UUIDv5 gives alice the same `UserId` on every login and needs no storage, no migration, and no lookup on every request. The issuer is part of the name, so the same `sub` or group path from another issuer never collides.
- **One odd group costs only itself.** A path that could mean two groups, or hides characters, is unsafe to map, but refusing the whole token would lock its member out of every other group too. Leaving it out only ever narrows what the reader sees, which fails safe; a claim that is not a list, or is oversized, is still refused as a whole, because then nothing in it can be trusted.
- **The body waits for identity.** If the body is read first, an anonymous caller makes the service buffer and parse whatever they send, and learns about validation before proving who they are. Authenticating first bounds that work to one header and a signature check.
- **Left for later.** Application roles (M07-T05), Source group mapping (M07-T06), and ACL predicates in vector and lexical search (M07-T07, M07-T08).

**Falsified assumption.** I assumed a full group path such as `/a/b` names exactly one group. The review showed that Keycloak, by default, gives a top-level group named `a/b` the same path as group `b` inside group `a`. So "a path is a unique group name" was wrong; it holds only with Keycloak's slash escaping on and paths containing `~` left out.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It no longer has to decide whom to trust. A user ID from a header is whatever the caller typed; the reader from `admit` already passed the signature, issuer, audience, client, and claim checks, and the body was read only after that. The calling code just passes the reader on.
2. The whole Authorization Context, both the `UserId` and the set of `GroupId`s, must be in the key. If it is not, Evidence found for alice in `/engineering` is served to carol, who is in no group, so she reads text she has no grant for.
3. Today they get 401 `invalid_token`: the token verifies, but its `azp`, `admin-ui`, is not in `trusted_clients`. To let them in you would add `admin-ui` to `ContextPolicy.trusted_clients` on purpose, and check what that client's tokens may claim.
4. Watch the `group claim entries ignored: N` warning: how often it appears and for how many users. Test a real Keycloak group whose name contains `~` or a slash and log in as its member; if the count is above zero for normal users, they are losing groups they need.
5. Her context holds only her `UserId`, the UUIDv5 of the issuer and her `sub`, and an empty set of Groups. A search can return only Documents granted to everyone or to her personally; nothing granted only to a group.
