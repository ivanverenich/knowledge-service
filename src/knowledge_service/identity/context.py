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
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.oidc import TokenRejected, VerifiedToken
from knowledge_service.roles import ApplicationRole

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# The longest subject or group path a token may name.
MAX_NAME_LENGTH = 255
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
BEARER = re.compile(r"Bearer +([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE | re.ASCII)
# Letters, marks that combine with them, digits, punctuation, and symbols.
VISIBLE = frozenset(
    {"Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Nd", "Nl", "No"}
    | {"Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "Sk", "So"}
)
# Characters in those categories that still print as nothing: the combining
# grapheme joiner, Hangul and Khmer fillers, Mongolian and other variation
# selectors, and the blank Braille pattern.
BLANK = frozenset(
    chr(code)
    for code in (
        0x034F,
        0x115F,
        0x1160,
        0x17B4,
        0x17B5,
        0x180B,
        0x180C,
        0x180D,
        0x180F,
        0x2800,
        0x3164,
        0xFFA0,
        *range(0xFE00, 0xFE10),
    )
)


class AuthenticationError(StrEnum):
    """The RFC 6750 error a refused request gets.

    `NONE` is a request with no Bearer token at all: it gets a bare
    challenge. `INVALID_REQUEST` is a malformed Authorization header, a 400.
    `INVALID_TOKEN` is a token that is not good, a 401.
    """

    NONE = ""
    INVALID_REQUEST = "invalid_request"
    INVALID_TOKEN = "invalid_token"  # noqa: S105 - an RFC 6750 error code


class Unauthenticated(Exception):
    """The request carries no usable identity; `reason` names why, for logs."""

    def __init__(self, reason: str, *, error: AuthenticationError) -> None:
        super().__init__(f"unauthenticated: {reason}")
        self.reason = reason
        self.error = error


class ClaimDefect(StrEnum):
    """Why verified claims cannot become an Authorization Context."""

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


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_roles: int = 64
    max_name_length: int = MAX_NAME_LENGTH

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


def group_id_for(issuer: str, path: str) -> GroupId:
    """The Group an issuer's full group path is, the same for every token.

    The path is used exactly as the provider wrote it: `/Engineering` and
    `/engineering` are two groups, as they are in Keycloak.
    """
    return GroupId(uuid5(IDENTITY_NAMESPACE, f"group\x00{issuer}\x00{path}"))


def build_authorization_context(
    token: VerifiedToken, policy: ContextPolicy
) -> AuthorizationContext:
    """The User and Groups a verified token names, or `Unauthenticated`.

    The token must come from a trusted client (`azp`), name a visible
    subject, and carry a `groups` claim, if any, that is a list of at most
    `max_groups` strings. A path that is not a plain full path is left
    out, so one odd group costs its member that group, never the others.
    """
    claims = token.claims
    issuer: object = claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise _defect(ClaimDefect.ISSUER)
    client: object = claims.get("azp")
    if not isinstance(client, str) or client not in policy.trusted_clients:
        raise _defect(ClaimDefect.UNTRUSTED_CLIENT)
    if not visible_name(token.subject, policy.max_name_length):
        raise _defect(ClaimDefect.SUBJECT)
    return AuthorizationContext(
        user_id=user_id_for(issuer, token.subject),
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
        if len(headers) > 1:
            raise Unauthenticated("repeated", error=AuthenticationError.INVALID_REQUEST)
        if not headers or not headers[0].lower().startswith("bearer"):
            # No token at all, or another scheme such as Basic.
            raise Unauthenticated("missing", error=AuthenticationError.NONE)
        found = BEARER.fullmatch(headers[0])
        if found is None:
            raise Unauthenticated(
                "malformed", error=AuthenticationError.INVALID_REQUEST
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
    listed: object = claims.get("groups", [])
    if not isinstance(listed, list):
        raise _defect(ClaimDefect.GROUPS_MALFORMED)
    paths = cast("list[object]", listed)
    if len(paths) > policy.max_groups:
        raise _defect(ClaimDefect.GROUPS_OVERSIZED)
    named: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            raise _defect(ClaimDefect.GROUPS_MALFORMED)
        named.add(path)
    kept = {path for path in named if plain_group_path(path, policy.max_name_length)}
    if ignored := len(named - kept):
        # A count only: the names may be anything the provider holds.
        logger.warning("group claim entries ignored: %d", ignored)
    return kept


def plain_group_path(path: str, max_length: int = MAX_NAME_LENGTH) -> bool:
    """A full path such as `/finance/payroll`, with nothing ambiguous in it.

    Keycloak writes a `/` inside a group's name as `~/`, but leaves `~`
    itself alone, so a path with `~` could be either of two groups. Only a
    plain path ever reaches an Authorization Context.
    """
    return (
        path.startswith("/")
        and path != "/"
        and "~" not in path
        and visible_name(path, max_length)
    )


def visible_name(name: str, max_length: int = MAX_NAME_LENGTH) -> bool:
    """Non-empty, within the length limit, and every character visible.

    Spaces are allowed inside a name, not at either end. Only a visible
    subject ever reaches an Authorization Context.
    """
    return (
        0 < len(name) <= max_length
        and name == name.strip()
        and all(
            char == " " or (unicodedata.category(char) in VISIBLE and char not in BLANK)
            for char in name
        )
    )


def _defect(defect: ClaimDefect) -> Unauthenticated:
    return Unauthenticated(str(defect), error=AuthenticationError.INVALID_TOKEN)
