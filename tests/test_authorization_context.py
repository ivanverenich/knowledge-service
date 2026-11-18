"""The Authorization Context: from a verified token's claims to typed IDs.

`Authenticator` is tested with a fake verifier: which tokens are genuine is
M07-T03's concern; here the claims are given and the mapping is checked.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

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


def verified(**claims: object) -> VerifiedToken:
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
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        claims=payload,
    )


class Verifier:
    """Accepts the tokens it was given, rejects or fails on the rest."""

    def __init__(
        self,
        tokens: dict[str, VerifiedToken] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.tokens = tokens or {}
        self.error = error
        self.seen: list[str] = []

    async def verify(self, token: str) -> VerifiedToken:
        self.seen.append(token)
        if self.error is not None:
            raise self.error
        if token not in self.tokens:
            raise TokenRejected(Rejection.SIGNATURE)
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


def test_a_verified_token_names_its_user_and_groups() -> None:
    context = build_authorization_context(
        verified(groups=["/engineering", "/finance/payroll"]), POLICY
    )

    assert context == AuthorizationContext(
        user_id=user_id_for(ISSUER, ALICE),
        group_ids=frozenset(
            {
                group_id_for(ISSUER, "/engineering"),
                group_id_for(ISSUER, "/finance/payroll"),
            }
        ),
    )


def test_ids_are_stable_and_scoped_to_their_issuer() -> None:
    # Pinned: a change would orphan every stored grant for a User or Group.
    assert UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31") == IDENTITY_NAMESPACE
    assert user_id_for(ISSUER, ALICE) == user_id_for(ISSUER, ALICE)
    assert user_id_for(ISSUER, ALICE) != user_id_for(f"{ISSUER}-other", ALICE)
    assert group_id_for(ISSUER, "/a") != group_id_for("https://other.test", "/a")
    # A User and a Group with the same name are never the same UUID.
    assert user_id_for(ISSUER, "/a").value != group_id_for(ISSUER, "/a").value
    assert type(user_id_for(ISSUER, ALICE)) is UserId
    assert type(group_id_for(ISSUER, "/a")) is GroupId


def test_a_user_in_no_group_has_no_groups() -> None:
    for token in (verified(groups=None), verified(groups=[])):
        assert build_authorization_context(token, POLICY).group_ids == frozenset()


def test_a_group_listed_twice_is_one_group() -> None:
    context = build_authorization_context(
        verified(groups=["/engineering", "/engineering"]), POLICY
    )

    assert context.group_ids == {group_id_for(ISSUER, "/engineering")}


def test_the_token_text_itself_never_becomes_an_id() -> None:
    # Only the issuer and the names inside the verified claims count.
    one = build_authorization_context(verified(), POLICY)
    two = build_authorization_context(verified(), POLICY)

    assert one == two


# --- Refusals: untrusted, malformed, oversized ----------------------------------------


@pytest.mark.parametrize("client", ["account-console", "", None, ["open-webui"]])
def test_a_token_issued_to_another_client_gives_no_context(client: object) -> None:
    assert defect(verified(azp=client)) == "untrusted_client"


@pytest.mark.parametrize(
    "subject",
    ["", " alice", "alice\n", "al\x00ice", "al" + chr(0x200B) + "ice", "a" * 256],
)
def test_a_subject_that_is_not_a_printable_name_gives_no_context(
    subject: str,
) -> None:
    assert defect(verified(sub=subject)) == "subject"


@pytest.mark.parametrize(
    "groups",
    ["/engineering", {"/engineering": True}, [["/engineering"]], [42], [None]],
    ids=["string", "object", "nested", "number", "null"],
)
def test_a_groups_claim_that_is_not_a_list_of_names_gives_no_context(
    groups: object,
) -> None:
    assert defect(verified(groups=groups)) == "groups_malformed"


def hidden(*codes: int) -> str:
    """Characters that print as nothing, built from their code points."""
    return "".join(chr(code) for code in codes)


@pytest.mark.parametrize(
    "path",
    [
        "engineering",
        "/",
        "/engineering ",
        "/eng\nineering",
        "/eng" + hidden(0x202E) + "ineering",
        "/engineering" + hidden(0x200B),
        "/engineering" + hidden(0x3164),
        "/engineering" + hidden(0x2800),
        "/engineering" + hidden(0x034F),
        "/engineering" + hidden(0xFE0F),
        "/" + "a" * 255,
        "/finance~/payroll",
        "/finance~",
    ],
    ids=[
        "not-a-path",
        "root",
        "trailing-space",
        "newline",
        "bidi",
        "zero-width-space",
        "hangul-filler",
        "braille-blank",
        "grapheme-joiner",
        "variation-selector",
        "too-long",
        "escaped-slash",
        "tilde",
    ],
)
def test_a_group_path_that_is_not_plain_is_left_out_and_counted(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    context = build_authorization_context(
        verified(groups=[path, "/engineering"]), POLICY
    )

    assert context.group_ids == {group_id_for(ISSUER, "/engineering")}
    assert caplog.messages == ["group claim entries ignored: 1"]


@pytest.mark.parametrize(
    "path", ["/Data Science", "/finance/payroll", "/Інженерія", "/zzr18é", "/.."]
)
def test_plain_paths_in_any_script_are_kept(path: str) -> None:
    context = build_authorization_context(verified(groups=[path]), POLICY)

    assert context.group_ids == {group_id_for(ISSUER, path)}


def test_a_nested_group_and_a_name_with_a_slash_are_never_one_group() -> None:
    # With slashes escaped, Keycloak writes a top-level group "a/b" as
    # "/a~/b" and group "b" inside "a" as "/a/b". Only the second is kept.
    context = build_authorization_context(verified(groups=["/a/b", "/a~/b"]), POLICY)

    assert context.group_ids == {group_id_for(ISSUER, "/a/b")}


def test_group_paths_keep_their_case() -> None:
    assert group_id_for(ISSUER, "/Engineering") != group_id_for(ISSUER, "/engineering")


def test_a_token_without_an_issuer_gives_no_context() -> None:
    assert defect(verified(iss=None)) == "issuer"


def test_too_many_groups_give_no_context() -> None:
    at_limit = verified(groups=[f"/g{number}" for number in range(64)])
    over = verified(groups=[f"/g{number}" for number in range(65)])

    assert len(build_authorization_context(at_limit, POLICY).group_ids) == 64
    assert defect(over) == "groups_oversized"


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
async def test_the_scheme_is_case_insensitive_and_spaces_may_repeat(
    header: str,
) -> None:
    verifier = Verifier({"good-token": verified()})

    await Authenticator(verifier, POLICY).authenticate([header])

    assert verifier.seen == ["good-token"]


NONE = AuthenticationError.NONE
INVALID_REQUEST = AuthenticationError.INVALID_REQUEST


@pytest.mark.parametrize(
    ("headers", "reason", "error"),
    [
        ([], "missing", NONE),
        (["Basic YWxpY2U6c2VjcmV0"], "missing", NONE),
        (["good-token"], "missing", NONE),
        (["Bearer good-token", "Bearer good-token"], "repeated", INVALID_REQUEST),
        (["Bearer"], "malformed", INVALID_REQUEST),
        (["Bearer good-token extra"], "malformed", INVALID_REQUEST),
        (["Bearer good token"], "malformed", INVALID_REQUEST),
        (["Bearer good-token\n"], "malformed", INVALID_REQUEST),
        (["Bearer good-" + chr(0x17F) + "oken"], "malformed", INVALID_REQUEST),
    ],
    ids=[
        "missing",
        "basic",
        "no-scheme",
        "repeated",
        "no-token",
        "trailing-word",
        "space-inside",
        "newline",
        "non-ascii-letter",
    ],
)
async def test_a_header_that_is_not_one_bearer_token_is_refused_unverified(
    headers: list[str], reason: str, error: AuthenticationError
) -> None:
    verifier = Verifier({"good-token": verified()})

    with pytest.raises(Unauthenticated) as raised:
        await Authenticator(verifier, POLICY).authenticate(headers)

    assert (raised.value.reason, raised.value.error) == (reason, error)
    assert verifier.seen == []


async def test_a_token_the_verifier_rejects_is_refused_with_its_reason() -> None:
    verifier = Verifier(error=TokenRejected(Rejection.EXPIRED))

    with pytest.raises(Unauthenticated) as raised:
        await Authenticator(verifier, POLICY).authenticate(["Bearer old-token"])

    assert raised.value.reason == "expired"
    assert raised.value.__context__ is None


async def test_an_unavailable_identity_provider_is_not_an_unauthenticated_user() -> (
    None
):
    verifier = Verifier(error=IdentityProviderUnavailable("down"))

    with pytest.raises(IdentityProviderUnavailable):
        await Authenticator(verifier, POLICY).authenticate(["Bearer any-token"])


async def test_no_token_or_claim_text_reaches_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    verifier = Verifier(
        {
            MARKER: verified(sub=f"{MARKER}\n"),
            "client": verified(azp=MARKER),
        }
    )
    errors: list[BaseException] = []
    for header in (
        f"Bearer {MARKER}",
        "Bearer client",
        f"Basic {MARKER}",
        f"Bearer {MARKER}-rejected",
    ):
        with pytest.raises(Unauthenticated) as raised:
            await Authenticator(verifier, POLICY).authenticate([header])
        errors.append(raised.value)

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
