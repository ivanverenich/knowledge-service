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
