"""The native HTTP API's routes: one table, and the rules every row follows.

Every route the service serves or plans is a row here, with the Operation an
Application Role must permit and the task that builds it. `route_problems`
checks the naming, identifier, and pagination rules; the tests check that the
app serves exactly the served rows, each behind its Operation.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.roles import Operation

API_PREFIX = "/v1"
# Words that end a path after an ID: one thing of that item, such as its
# feedback, and something done to it, such as a retry. Every other word must
# name a list, in the plural.
SINGLE_RESOURCES = frozenset({"feedback", "diagnostics"})
ACTIONS = frozenset({"enable", "disable", "retry", "cancel"})

_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
_PARAMETER = re.compile(r"\{([a-z_]+)\}")
_TASK = re.compile(r"M\d{2}-T\d{2}")


class Method(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RouteStatus(StrEnum):
    SERVED = "served"
    PLANNED = "planned"


@dataclass(frozen=True, slots=True)
class Route:
    """One route: who may call it, whether it is served, and who builds it.

    `task` is the curriculum task that built or builds it; None marks a
    planned route no task schedules yet. A `paginated` route takes `limit`
    and `cursor` and answers `{"items": [...], "next_cursor": ...}`.
    """

    method: Method
    path: str
    operation: Operation
    status: RouteStatus
    task: str | None
    summary: str
    paginated: bool = False


@dataclass(frozen=True, slots=True)
class RouteProblem:
    """A rule one route breaks."""

    route: Route
    rule: str


_GET, _POST, _PUT, _PATCH, _DELETE = Method
_SERVED, _PLANNED = RouteStatus.SERVED, RouteStatus.PLANNED
_MESSAGE = "/v1/conversations/{conversation_id}/messages/{message_id}"

ROUTES: tuple[Route, ...] = (
    # Questions
    Route(
        _POST,
        "/v1/questions",
        Operation.ASK_QUESTIONS,
        _SERVED,
        "M09-T01",
        "Ask a Question; the response streams its Answer",
    ),
    # Conversations, each only its owner's
    Route(
        _GET,
        "/v1/conversations",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List the caller's Conversations",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "Read one of the caller's Conversations",
    ),
    Route(
        _DELETE,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _SERVED,
        "M07-T16",
        "Delete one of the caller's Conversations",
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}/messages",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List a Conversation's Questions and Answers",
        paginated=True,
    ),
    # Feedback on one of the caller's Answers
    Route(
        _PUT,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Rate an Answer up or down; the same rating again changes nothing",
    ),
    Route(
        _DELETE,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Remove the caller's rating of an Answer",
    ),
    # Diagnostics of one of the caller's Answers: counts, timings, and usage
    Route(
        _GET,
        f"{_MESSAGE}/diagnostics",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "How an Answer was made, without any Document text",
    ),
    # Sources
    Route(
        _GET,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "List Sources",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "Register a Source",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "Read a Source's configuration",
    ),
    Route(
        _PATCH,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "Change a Source's configuration",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/enable",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "Let a Source synchronize again",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/disable",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "Stop a Source from synchronizing",
    ),
    # Synchronization: durable Jobs, never broker messages
    Route(
        _POST,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "Start a synchronization, or return the one already open",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "List a Source's synchronization Jobs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/jobs/{job_id}",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M08-T07",
        "Read a synchronization Job and its newest attempt",
    ),
    # Jobs that failed for good, for an administrator
    Route(
        _GET,
        "/v1/job-decisions",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "List Jobs that failed for good, newest first",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/retry",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Queue a Job that failed for good once more",
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/cancel",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Cancel a Job that has not finished",
    ),
    # Evaluation
    Route(
        _GET,
        "/v1/evaluation-datasets",
        Operation.MANAGE_EVALUATION_DATASETS,
        _PLANNED,
        "M10-T01",
        "List Evaluation Datasets",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Start a paid evaluation run",
    ),
    Route(
        _GET,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "List evaluation runs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/evaluation-runs/{evaluation_run_id}",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Read an evaluation run and its results",
    ),
)


def served_routes() -> tuple[Route, ...]:
    """The rows the app must serve, in table order."""
    return tuple(route for route in ROUTES if route.status is RouteStatus.SERVED)


def route_problems(routes: Iterable[Route]) -> tuple[RouteProblem, ...]:
    """Every rule each route breaks; an empty tuple means the design holds."""
    routes = tuple(routes)
    seen = Counter((route.method, route.path) for route in routes)
    problems: list[RouteProblem] = []
    for route in routes:
        problems += [RouteProblem(route, rule) for rule in _broken_rules(route)]
        if seen[route.method, route.path] > 1:
            problems.append(RouteProblem(route, "the same method and path twice"))
    return tuple(problems)


def _broken_rules(route: Route) -> list[str]:
    broken: list[str] = []
    if type(route.method) is not Method:
        broken.append("no Method")
    if type(route.operation) is not Operation:
        broken.append("no Operation")
    if type(route.status) is not RouteStatus:
        broken.append("no RouteStatus")
    elif route.status is RouteStatus.SERVED and route.task is None:
        broken.append("served without a task")
    if route.task is not None and not _TASK.fullmatch(route.task):
        broken.append("a task that is not a task ID")
    if not route.path.startswith(f"{API_PREFIX}/"):
        return [*broken, "not under /v1"]
    segments = route.path.removeprefix(f"{API_PREFIX}/").split("/")
    names: set[str] = set()
    previous: str | None = None
    for position, segment in enumerate(segments):
        parameter = _PARAMETER.fullmatch(segment)
        if parameter is not None:
            if previous is None or parameter[1] != _identifier_of(previous):
                broken.append(f"{segment} does not name the list before it")
            if parameter[1] in names:
                broken.append(f"{segment} appears twice")
            names.add(parameter[1])
        elif not _WORD.fullmatch(segment):
            broken.append(f"{segment!r} is not lowercase words joined by '-'")
        elif segment in SINGLE_RESOURCES or segment in ACTIONS:
            after_id = previous is not None and _PARAMETER.fullmatch(previous)
            if position != len(segments) - 1 or not after_id:
                broken.append(f"{segment!r} must end the path, after an ID")
            if segment in ACTIONS and route.method is not Method.POST:
                broken.append(f"{segment!r} is an action, so only POST")
        elif not _is_list(segment):
            broken.append(f"{segment!r} is not a list, a single resource, or an action")
        previous = segment
    lists = route.method is Method.GET and _is_list(segments[-1])
    if route.paginated and not lists:
        broken.append("paginated, but not a GET of a list")
    if lists and not route.paginated:
        broken.append("a GET of a list that is not paginated")
    return broken


def _is_list(segment: str) -> bool:
    """A plural word, such as `sources` or `job-decisions`."""
    return (
        _WORD.fullmatch(segment) is not None
        and segment.endswith("s")
        and segment not in SINGLE_RESOURCES
        and segment not in ACTIONS
    )


def _identifier_of(collection: str) -> str | None:
    """`sources` -> `source_id`, `entries` -> `entry_id`; None if not a list.

    An irregular plural, such as `analyses`, has no rule here: name such a
    list differently, or add the rule with a test.
    """
    if not _is_list(collection):
        return None
    singular = f"{collection[:-3]}y" if collection.endswith("ies") else collection[:-1]
    return f"{singular.replace('-', '_')}_id"
