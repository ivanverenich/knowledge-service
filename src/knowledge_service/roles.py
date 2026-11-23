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
    RECOVER_JOBS = "recover_jobs"


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
            {
                Operation.MANAGE_POLICY,
                Operation.MANAGE_OPERATIONAL_CONFIGURATION,
                Operation.RECOVER_JOBS,
            }
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
