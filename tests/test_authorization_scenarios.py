"""The scenario generator holds every risk it promises, for every seed.

The suite in `test_authorization_suite_integration.py` is only as strong as
its scenarios: a seed without a nested group or a twin would pass while
proving nothing about them. These tests need no database.
"""

import pytest

from authorization_scenarios import (
    BACKEND,
    ENGINEERING,
    ISSUER,
    Audience,
    Fate,
    Person,
    ScenarioAuthenticator,
    may_read,
    scenario,
)
from knowledge_service.identity.context import Unauthenticated, group_id_for

SEEDS = range(50)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_scenario_holds_each_risk(seed: int) -> None:
    case = scenario(seed)
    groups = {s.grantee for s in case.secrets if s.audience is Audience.GROUP}
    twins = [s for s in case.secrets if s.twin]

    assert {ENGINEERING, BACKEND, "/Engineering"} <= groups
    assert {Fate.REVOKED, Fate.DELETED} <= {s.fate for s in case.secrets}
    assert {s.language for s in case.secrets} == {"en", "uk"}
    # One body text, granted to everyone and to one person.
    assert len({s.text for s in twins}) == 1
    assert {s.audience for s in twins} == {Audience.PUBLIC, Audience.USER}
    for person in case.people:
        assert case.readable(person), person.name
        assert case.forbidden(person), person.name
    assert scenario(seed) == case


def test_a_child_groups_member_does_not_read_the_parents_documents() -> None:
    case = scenario(0)
    bob = case.person("bob")
    parent = next(s for s in case.secrets if s.grantee == ENGINEERING)

    assert not may_read(bob, parent)
    assert group_id_for(ISSUER, ENGINEERING) not in bob.context().group_ids


def test_look_alike_paths_are_other_groups() -> None:
    alice = scenario(0).person("alice")

    assert alice.context().group_ids == frozenset({group_id_for(ISSUER, ENGINEERING)})
    for other in ("/Engineering", "/engineering-ops", BACKEND):
        assert group_id_for(ISSUER, other) not in alice.context().group_ids


def test_an_ambiguous_group_path_gives_its_member_nothing() -> None:
    # Keycloak writes `/` inside a name as `~/`, so `~` makes a path ambiguous.
    eve = Person("eve", ("/engineering~/backend",))

    assert eve.context().group_ids == frozenset()


async def test_an_unknown_token_is_refused() -> None:
    authenticator = ScenarioAuthenticator(scenario(0).people)

    with pytest.raises(Unauthenticated):
        await authenticator.authenticate(["Bearer mallory"])
