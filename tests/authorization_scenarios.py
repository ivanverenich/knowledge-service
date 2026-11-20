"""Seeded authorization scenarios that any interface's tests can reuse.

A scenario is a small company: people with tokens, and Documents with
Access Grants. Every Document is about the same topic and sits exactly on
the query vector, so for every reader the Documents they may not see are as
good a match as the ones they may. Each scenario holds:

- **People from real tokens.** Each person's Authorization Context is built
  by `build_authorization_context` from token claims, as a request's is, so
  group paths become Group IDs exactly as in production.
- **Nested and look-alike groups.** `/engineering` and
  `/engineering/backend` are two groups: a member of one does not read the
  other's Documents. `/Engineering` and `/engineering-ops` only look like
  `/engineering`.
- **Identical text.** A public and a private Document share the same body
  text, word for word. A reader of only the public one may see the text,
  but never the private one's title, identifiers, or provenance.
- **Revocations and deletions.** Some Documents are published to someone
  and then taken away, and some are deleted from the Source, so they are
  tombstoned while their Chunks are still stored; nobody may read either.
- **Both languages.** Documents and probes are in English and Ukrainian.

Every Document carries a unique marker word in its title and, unless it is
one of the twins, in its text. `may_read` states the eligibility rule in
plain Python, independently of the SQL; a test compares what an interface
returns with it, and searches everything a reader or a log could see for the
markers of Documents the reader may not read.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    ContextPolicy,
    Unauthenticated,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import VerifiedToken
from knowledge_service.roles import ApplicationRole

ISSUER = "https://identity.example/realms/knowledge"
CLIENT = "open-webui"
POLICY = ContextPolicy(trusted_clients=frozenset({CLIENT}))
EXPIRES_AT = datetime(2030, 1, 1, tzinfo=UTC)
# How long after publishing a Document is revoked or deleted.
LATER = timedelta(hours=1)

ENGINEERING = "/engineering"
BACKEND = "/engineering/backend"
GROUP_PATHS = (
    ENGINEERING,
    BACKEND,
    # Look-alikes: other groups whose paths resemble `/engineering`.
    "/Engineering",
    "/engineering-ops",
    "/finance/payroll",
)
PROBES = {
    "en": "How many days of annual leave do I get?",
    "uk": "Скільки днів щорічної відпустки я маю?",
}
TOPIC = {
    "en": "The annual leave policy gives every employee days of annual leave.",
    "uk": "Політика щорічної відпустки дає кожному працівнику дні щорічної відпустки.",
}


class Fate(StrEnum):
    """What happens to a Document after it is published."""

    KEPT = "kept"
    REVOKED = "revoked"
    DELETED = "deleted"


class Audience(StrEnum):
    """Who a Document is granted to."""

    PUBLIC = "public"
    USER = "user"
    GROUP = "group"
    NOBODY = "nobody"


@dataclass(frozen=True, slots=True)
class Person:
    """A User, by the claims of the token they would send."""

    name: str
    groups: tuple[str, ...]

    @property
    def subject(self) -> str:
        return f"sub-{self.name}"

    def token(self) -> VerifiedToken:
        return VerifiedToken(
            subject=self.subject,
            key_id="test-key",
            expires_at=EXPIRES_AT,
            claims={
                "iss": ISSUER,
                "sub": self.subject,
                "azp": CLIENT,
                "groups": list(self.groups),
            },
        )

    def context(self) -> AuthorizationContext:
        """The Authorization Context a request with this token gets."""
        return build_authorization_context(self.token(), POLICY)

    @property
    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.name}"}


@dataclass(frozen=True, slots=True)
class Secret:
    """One Document: its marker, language, audience, and fate."""

    marker: str
    language: str
    audience: Audience
    # The person or group path the Document is granted to, if any.
    grantee: str | None
    fate: Fate
    # Twins share one body text and carry their marker only in the title.
    twin: bool

    @property
    def external_id(self) -> str:
        return f"{self.marker}.txt"

    @property
    def title(self) -> str:
        return f"{self.marker} {PROBES[self.language]}"

    @property
    def text(self) -> str:
        if self.twin:
            return TOPIC[self.language]
        return f"{TOPIC[self.language]} {self.marker}."

    def readers(self) -> list[Subject]:
        """The Access Grants it is published with, before any revocation."""
        if self.audience is Audience.PUBLIC:
            return [Subject.public()]
        if self.audience is Audience.USER:
            assert self.grantee is not None
            return [Subject.user(user_id_for(ISSUER, f"sub-{self.grantee}"))]
        if self.audience is Audience.GROUP:
            assert self.grantee is not None
            return [Subject.group(group_id_for(ISSUER, self.grantee))]
        return []


@dataclass(frozen=True, slots=True)
class Scenario:
    seed: int
    people: tuple[Person, ...]
    secrets: tuple[Secret, ...]

    def person(self, name: str) -> Person:
        return next(person for person in self.people if person.name == name)

    def readable(self, person: Person) -> set[str]:
        """The markers of every Document `person` may read."""
        return {s.marker for s in self.secrets if may_read(person, s)}

    def forbidden(self, person: Person) -> set[str]:
        """The markers of every Document `person` may not read."""
        return {s.marker for s in self.secrets} - self.readable(person)


def may_read(person: Person, secret: Secret) -> bool:
    """The eligibility rule, written out without the SQL.

    A Document is readable when it is kept, not revoked or deleted, and is
    granted to everyone, to this person, or to a group whose path the token
    names exactly. A parent or child group never counts.
    """
    if secret.fate is not Fate.KEPT:
        return False
    if secret.audience is Audience.PUBLIC:
        return True
    if secret.audience is Audience.USER:
        return secret.grantee == person.name
    if secret.audience is Audience.GROUP:
        return secret.grantee in person.groups
    return False


def scenario(seed: int) -> Scenario:
    """A reproducible company of people and Documents for `seed`."""
    rng = random.Random(seed)  # noqa: S311 - a seeded, reproducible scenario
    people = (
        Person("alice", (ENGINEERING,)),
        Person("bob", (BACKEND,)),
        Person("carol", tuple(rng.sample(GROUP_PATHS, rng.randint(1, 3)))),
        Person("dan", ()),
    )
    secrets: list[Secret] = []
    # The fixed core: each risk is present in every scenario.
    fixed = [
        (Audience.GROUP, ENGINEERING),
        (Audience.GROUP, BACKEND),
        (Audience.GROUP, "/Engineering"),
        (Audience.USER, "alice"),
        (Audience.USER, "bob"),
        (Audience.NOBODY, None),
    ]
    for audience, grantee in fixed:
        secrets.append(_secret(rng, len(secrets), audience, grantee, Fate.KEPT))
    for fate in (Fate.REVOKED, Fate.DELETED):
        secrets.append(_secret(rng, len(secrets), Audience.GROUP, ENGINEERING, fate))
    # Twins: one body text, granted to everyone and to bob only.
    language = rng.choice(("en", "uk"))
    for audience, grantee in ((Audience.PUBLIC, None), (Audience.USER, "bob")):
        secrets.append(
            Secret(
                marker=f"twin{len(secrets)}s{seed}",
                language=language,
                audience=audience,
                grantee=grantee,
                fate=Fate.KEPT,
                twin=True,
            )
        )
    # Random extras, so seeds differ beyond the core.
    for _ in range(rng.randint(2, 5)):
        audience = rng.choice(list(Audience))
        grantee = _grantee(rng, audience, people)
        fate = rng.choices(list(Fate), weights=(6, 1, 1))[0]
        secrets.append(_secret(rng, len(secrets), audience, grantee, fate))
    return Scenario(seed=seed, people=people, secrets=tuple(secrets))


def _secret(
    rng: random.Random,
    index: int,
    audience: Audience,
    grantee: str | None,
    fate: Fate,
) -> Secret:
    return Secret(
        marker=f"mark{index}s{rng.randrange(10**6):06d}",
        language=rng.choice(("en", "uk")),
        audience=audience,
        grantee=grantee,
        fate=fate,
        twin=False,
    )


def _grantee(
    rng: random.Random, audience: Audience, people: Sequence[Person]
) -> str | None:
    if audience is Audience.USER:
        return rng.choice(people).name
    if audience is Audience.GROUP:
        return rng.choice(GROUP_PATHS)
    return None


class ScenarioAuthenticator:
    """Gives each person of a scenario the caller their token would give."""

    def __init__(self, people: Sequence[Person]) -> None:
        self._callers = {
            f"Bearer {person.name}": Caller(
                reader=person.context(), roles=frozenset({ApplicationRole.USER})
            )
            for person in people
        }

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in self._callers:
            return self._callers[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


def leaked(markers: set[str], *observed: Any) -> set[str]:
    """The markers that appear anywhere in what was observed."""
    text = "\n".join(
        item.decode() if isinstance(item, bytes) else str(item) for item in observed
    )
    return {marker for marker in markers if marker in text}
