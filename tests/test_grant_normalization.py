"""Source principals to Access Grant subjects: canonical, stable, fail-closed.

The examples pin the mapping; the properties show the result never depends
on the principals' order and that a principal the directory cannot resolve
never grants anything.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.access import Subject
from knowledge_service.identity.context import (
    ContextPolicy,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import VerifiedToken
from knowledge_service.ingestion.grant_normalization import (
    InvalidPrincipalDirectory,
    NormalizedAccess,
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
    normalize_access,
)
from knowledge_service.ingestion.sources import SourceAccess

ISSUER = "http://localhost:8180/realms/knowledge-service"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
CAROL = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03"
DIRECTORY = PrincipalDirectory(
    issuer=ISSUER,
    users={"carol@example.test": CAROL},
    groups={"engineering": "/engineering", "eng-all": "/engineering"},
)


class Name(str):
    """A string subclass could compare or hash as another name."""

    __slots__ = ()


ENGINEERING = Subject.group(group_id_for(ISSUER, "/engineering"))
CAROL_SUBJECT = Subject.user(user_id_for(ISSUER, CAROL))
MARKER = "SEEDED-SECRET-5e1a"

MALFORMED = UnresolvedReason.MALFORMED
UNKNOWN = UnresolvedReason.UNKNOWN


def normalized(*principals: str, directory: Any = DIRECTORY) -> NormalizedAccess:
    return normalize_access(
        SourceAccess(public=False, principals=principals), directory
    )


# --- Success: mapped principals become the token's own IDs ----------------------------


def test_mapped_principals_become_canonical_subjects() -> None:
    access = normalized("group:engineering", "user:carol@example.test")

    assert access.readers == tuple(
        sorted([ENGINEERING, CAROL_SUBJECT], key=lambda s: (s.kind, str(s.identifier)))
    )
    assert access.unresolved == ()


def test_a_source_group_is_the_group_a_token_for_that_path_carries() -> None:
    # alice's token lists /engineering; the Source calls the group "engineering".
    token = VerifiedToken(
        subject=ALICE,
        key_id="key-1",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        claims={
            "iss": ISSUER,
            "sub": ALICE,
            "azp": "open-webui",
            "groups": ["/engineering"],
        },
    )
    reader = build_authorization_context(
        token, ContextPolicy(trusted_clients=frozenset({"open-webui"}))
    )

    (subject,) = normalized("group:engineering").readers

    assert subject.identifier in reader.group_ids


def test_a_public_item_is_readable_by_everyone_with_or_without_a_directory() -> None:
    for directory in (DIRECTORY, None):
        access = normalize_access(SourceAccess(public=True), directory)
        assert access == NormalizedAccess(readers=(Subject.public(),), unresolved=())


def test_two_source_names_for_one_group_give_one_subject() -> None:
    assert normalized("group:engineering", "group:eng-all").readers == (ENGINEERING,)


def test_readers_come_back_in_one_order_within_a_kind() -> None:
    directory = PrincipalDirectory(
        issuer=ISSUER, groups={"engineering": "/engineering", "finance": "/finance"}
    )

    access = normalized("group:finance", "group:engineering", directory=directory)

    assert [str(subject.identifier) for subject in access.readers] == sorted(
        str(group_id_for(ISSUER, path)) for path in ("/engineering", "/finance")
    )


def test_an_id_with_a_colon_is_matched_whole() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, groups={"eng:all": "/engineering"})

    assert normalized("group:eng:all", directory=directory).readers == (ENGINEERING,)
    assert normalized("group:all", directory=directory).readers == ()


def test_a_path_with_a_trailing_slash_is_another_group() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, groups={"eng": "/engineering/"})

    assert normalized("group:eng", directory=directory).readers != (ENGINEERING,)


def test_the_same_names_from_another_issuer_are_other_subjects() -> None:
    other = PrincipalDirectory(
        issuer="https://idp.example.test/realms/other",
        groups={"engineering": "/engineering"},
    )

    assert normalized("group:engineering", directory=other).readers != (ENGINEERING,)


# --- Denial: anything unresolved grants nothing, and is reported ----------------------


def test_a_private_item_with_no_principals_is_readable_by_nobody() -> None:
    assert normalized() == NormalizedAccess(readers=(), unresolved=())


@pytest.mark.parametrize(
    ("principal", "reason"),
    [
        ("group:contractors", UNKNOWN),
        ("user:mallory@example.test", UNKNOWN),
        ("group:Engineering", UNKNOWN),
        ("user:engineering", UNKNOWN),
        ("group:carol@example.test", UNKNOWN),
        ("hr", MALFORMED),
        ("Group:engineering", MALFORMED),
        ("team:engineering", MALFORMED),
        ("group:", MALFORMED),
        ("group: engineering", MALFORMED),
        ("group:engineering ", MALFORMED),
        (":engineering", MALFORMED),
        ("public", MALFORMED),
    ],
)
def test_a_principal_that_does_not_resolve_grants_nothing(
    principal: str, reason: UnresolvedReason
) -> None:
    access = normalized(principal, "group:engineering")

    assert access.readers == (ENGINEERING,)
    assert access.unresolved == (UnresolvedPrincipal(principal, reason),)


def test_without_a_directory_no_principal_resolves() -> None:
    access = normalized("group:engineering", "hr", directory=None)

    assert access.readers == ()
    assert access.unresolved == (
        UnresolvedPrincipal("group:engineering", UNKNOWN),
        UnresolvedPrincipal("hr", MALFORMED),
    )


def test_a_group_principal_never_resolves_through_the_user_mapping() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, users={"admins": CAROL})

    assert normalized("group:admins", directory=directory).readers == ()


# --- The directory: exact, private, and read-only -------------------------------------


@pytest.mark.parametrize(
    "settings",
    [
        {"issuer": ""},
        {"issuer": " " + ISSUER},
        {"issuer": ISSUER, "users": {"": CAROL}},
        {"issuer": ISSUER, "users": {"carol ": CAROL}},
        {"issuer": ISSUER, "users": {"carol": ""}},
        {"issuer": ISSUER, "users": {"carol": 7}},
        {"issuer": ISSUER, "users": [("carol", CAROL)]},
        {"issuer": ISSUER, "groups": {"engineering": "engineering"}},
        {"issuer": ISSUER, "groups": {"everyone": "/"}},
        {"issuer": ISSUER, "groups": {"engineering": "/engineering\n"}},
        {"issuer": ISSUER, "groups": {"engineering": "/a~/b"}},
        {"issuer": ISSUER, "groups": {"engineering": "/eng" + chr(0x200B)}},
        {"issuer": ISSUER, "groups": {"engineering": "/e" + chr(0) + "g"}},
        {"issuer": ISSUER, "groups": {"engineering": "/" + "e" * 255}},
        {"issuer": ISSUER, "users": {"carol": "ca" + chr(0x200B) + "rol"}},
        {"issuer": chr(0x200B) + ISSUER},
        {"issuer": ISSUER, "users": {Name("carol"): CAROL}},
        {"issuer": ISSUER, "users": {"carol": Name(CAROL)}},
    ],
    ids=[
        "empty-issuer",
        "padded-issuer",
        "empty-user-id",
        "padded-user-id",
        "empty-subject",
        "number-subject",
        "list-of-pairs",
        "relative-path",
        "root-path",
        "padded-path",
        "tilde-path",
        "hidden-path",
        "nul-path",
        "long-path",
        "hidden-subject",
        "hidden-issuer",
        "str-subclass-key",
        "str-subclass-subject",
    ],
)
def test_a_directory_with_a_partial_or_padded_name_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(InvalidPrincipalDirectory):
        PrincipalDirectory(**settings)


def test_the_directory_keeps_its_own_copy_and_cannot_be_changed() -> None:
    groups = {"engineering": "/engineering"}
    directory = PrincipalDirectory(issuer=ISSUER, groups=groups)

    groups["contractors"] = "/engineering"

    assert normalized("group:contractors", directory=directory).readers == ()
    with pytest.raises(TypeError):
        directory.groups["contractors"] = "/engineering"  # type: ignore[index]


# --- Telemetry: the principal is kept, but never shown --------------------------------


def test_an_unresolved_principal_is_kept_but_left_out_of_reprs() -> None:
    access = normalized(f"user:{MARKER}", f"{MARKER}")

    assert {item.principal for item in access.unresolved} == {
        f"user:{MARKER}",
        MARKER,
    }
    assert MARKER not in repr(access) + str(access)


def test_the_directory_repr_names_no_one() -> None:
    directory = PrincipalDirectory(
        issuer=ISSUER, users={MARKER: CAROL}, groups={"x": f"/{MARKER}"}
    )

    assert MARKER not in repr(directory) + str(directory)


# --- Properties: order never matters, and unresolved never grants ---------------------


KNOWN = ["group:engineering", "group:eng-all", "user:carol@example.test"]
PRINCIPALS = st.lists(
    st.one_of(
        st.sampled_from(KNOWN),
        st.builds(
            "{}:{}".format,
            st.sampled_from(["user", "group", "Group", "team", ""]),
            st.text(max_size=12),
        ),
        st.text(max_size=20),
    ),
    unique=True,
    max_size=12,
).filter(lambda principals: all(principal.strip() for principal in principals))


@given(principals=PRINCIPALS, data=st.data())
def test_normalization_does_not_depend_on_the_order_of_principals(
    principals: list[str], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(principals))

    assert normalized(*shuffled) == normalized(*principals)


@given(principals=PRINCIPALS)
def test_an_unresolved_principal_never_grants_and_is_always_reported(
    principals: list[str],
) -> None:
    access = normalized(*principals)
    unresolved = {item.principal for item in access.unresolved}
    resolved = [principal for principal in principals if principal not in unresolved]

    # Every principal is either a reader or reported, never both.
    assert unresolved <= set(principals)
    assert set(resolved) <= set(KNOWN)
    # Taking every unresolved principal away changes no reader.
    assert normalized(*resolved).readers == access.readers
    assert normalized(*resolved).unresolved == ()
    # And nothing beyond the mapped subjects is ever granted.
    assert set(access.readers) <= {ENGINEERING, CAROL_SUBJECT}
