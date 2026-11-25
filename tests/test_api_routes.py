"""The native API's route table, and the OpenAPI document of the app it describes.

The table is the design: every route served or planned, who may call it, and
whether it pages. These tests check that the table follows its own rules,
that the app serves exactly its served rows, and that the OpenAPI document
gives every route one error shape, one page shape, and IDs named after their
resource, with nothing shaped like a model provider's protocol.
"""

from dataclasses import replace
from typing import Any

import httpx
import pytest

from knowledge_service.api_routes import (
    ROUTES,
    Method,
    Route,
    RouteStatus,
    route_problems,
    served_routes,
)
from knowledge_service.app import COMMON_ERRORS, create_app
from knowledge_service.roles import Operation

SPEC: dict[str, Any] = create_app().openapi()
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]
V1_OPERATIONS = [
    (method.upper(), path, operation)
    for path, item in SPEC["paths"].items()
    if path.startswith("/v1/")
    for method, operation in item.items()
]
# Field names of the OpenAI Chat Completions protocol; the native API has its
# own words, and the compatible adapter (M09-T05) translates.
PROVIDER_FIELDS = frozenset(
    {
        "choices",
        "messages",
        "finish_reason",
        "object",
        "delta",
        "logprobs",
        "role",
        "content",
        "model",
        "id",
    }
)
# Every error status each served route documents, beyond the common six.
ROUTE_ERRORS = {
    ("POST", "/v1/questions"): {413, 429},
    ("DELETE", "/v1/conversations/{conversation_id}"): set[int](),
    ("GET", "/v1/jobs/{job_id}"): {404},
    ("GET", "/v1/job-decisions"): set[int](),
    ("POST", "/v1/jobs/{job_id}/retry"): {404, 409},
    ("POST", "/v1/jobs/{job_id}/cancel"): {404, 409},
    ("GET", "/v1/sources"): set[int](),
    ("POST", "/v1/sources"): {409, 413},
    ("GET", "/v1/sources/{source_id}"): {404},
    ("PATCH", "/v1/sources/{source_id}"): {404, 409, 413},
    ("POST", "/v1/sources/{source_id}/enable"): {404},
    ("POST", "/v1/sources/{source_id}/disable"): {404},
}
GOOD = Route(
    Method.GET,
    "/v1/sources",
    Operation.CONFIGURE_SOURCES,
    RouteStatus.PLANNED,
    "M09-T02",
    "List Sources",
    paginated=True,
)


def test_the_route_table_follows_every_rule() -> None:
    assert route_problems(ROUTES) == ()


def test_the_table_covers_every_resource_the_task_names() -> None:
    paths = {route.path for route in ROUTES}

    for resource in (
        "/v1/questions",
        "/v1/conversations",
        "/feedback",
        "/v1/sources",
        "/synchronizations",
        "/v1/evaluation-runs",
        "/diagnostics",
    ):
        assert any(resource in path for path in paths), resource


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"path": "/sources"}, "not under /v1"),
        ({"path": "/v2/sources"}, "not under /v1"),
        (
            {"path": "/v1/Sources", "paginated": False},
            "'Sources' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/source_list", "paginated": False},
            "'source_list' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/", "paginated": False},
            "'' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/{id}", "paginated": False},
            "{id} does not name the list before it",
        ),
        (
            {"path": "/v1/{source_id}", "paginated": False},
            "{source_id} does not name the list before it",
        ),
        (
            {"path": "/v1/sources/{source_id}/{job_id}", "paginated": False},
            "{job_id} does not name the list before it",
        ),
        ({"paginated": False}, "a GET of a list that is not paginated"),
        ({"method": Method.POST}, "paginated, but not a GET of a list"),
        (
            {"path": "/v1/sources/{source_id}", "method": Method.GET},
            "paginated, but not a GET of a list",
        ),
        ({"status": RouteStatus.SERVED, "task": None}, "served without a task"),
        ({"task": "T02"}, "a task that is not a task ID"),
        ({"operation": "configure_sources"}, "no Operation"),
        ({"status": "served", "task": None}, "no RouteStatus"),
        (
            {"path": "/v1/source", "paginated": False},
            "'source' is not a list, a single resource, or an action",
        ),
        (
            {
                "path": "/v1/sources/{source_id}/getsourcenow",
                "method": Method.POST,
                "paginated": False,
            },
            "'getsourcenow' is not a list, a single resource, or an action",
        ),
        (
            {"path": "/v1/sources/{source_id}/sources/{source_id}", "paginated": False},
            "{source_id} appears twice",
        ),
        (
            {"path": "/v1/feedback", "paginated": False},
            "'feedback' must end the path, after an ID",
        ),
        (
            {"path": "/v1/sources/{source_id}/retry", "paginated": False},
            "'retry' is an action, so only POST",
        ),
    ],
)
def test_each_rule_names_the_route_that_breaks_it(
    change: dict[str, Any], rule: str
) -> None:
    bad = replace(GOOD, **change)

    assert [problem.rule for problem in route_problems([bad])] == [rule]


def test_a_route_listed_twice_is_a_problem_both_times() -> None:
    problems = route_problems([GOOD, replace(GOOD, summary="Again")])

    assert [problem.rule for problem in problems] == [
        "the same method and path twice"
    ] * 2


def test_a_single_resource_at_the_end_of_a_get_is_not_a_list() -> None:
    diagnostics = replace(
        GOOD,
        path="/v1/conversations/{conversation_id}/messages/{message_id}/diagnostics",
        paginated=False,
    )

    assert route_problems([diagnostics]) == ()


@pytest.mark.parametrize(
    "path", ["/v1/entries/{entry_id}", "/v1/job-decisions/{job_decision_id}"]
)
def test_a_plural_in_ies_or_with_a_dash_names_its_identifier(path: str) -> None:
    assert route_problems([replace(GOOD, path=path, paginated=False)]) == ()


def test_the_app_serves_exactly_the_served_rows_under_v1() -> None:
    served: set[tuple[str, str]] = set()
    for route in create_app().routes:
        path: str = getattr(route, "path", "")
        methods: set[str] = getattr(route, "methods", None) or set()
        if path.startswith("/v1/"):
            served |= {(method, path) for method in methods}

    assert served == {(route.method.value, route.path) for route in served_routes()}


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    V1_OPERATIONS,
    ids=[f"{method} {path}" for method, path, _ in V1_OPERATIONS],
)
def test_every_error_a_route_documents_has_the_one_error_shape(
    method: str, path: str, operation: dict[str, Any]
) -> None:
    errors = {
        status: response
        for status, response in operation["responses"].items()
        if status.startswith(("4", "5"))
    }

    expected = {*COMMON_ERRORS, *ROUTE_ERRORS[method, path]}
    assert set(errors) == {str(status) for status in expected}
    for response in errors.values():
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_no_route_documents_the_framework_validation_error() -> None:
    assert "HTTPValidationError" not in SCHEMAS


def test_every_path_identifier_is_a_uuid_named_after_its_resource() -> None:
    for _method, path, operation in V1_OPERATIONS:
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                assert parameter["name"].endswith("_id"), path
                assert parameter["schema"]["format"] == "uuid", path


def test_every_id_field_of_every_schema_is_a_uuid() -> None:
    for name, schema in SCHEMAS.items():
        for field, value in schema.get("properties", {}).items():
            if field == "id" or field.endswith("_id"):
                formats = [value, *value.get("anyOf", [])]
                assert any(f.get("format") == "uuid" for f in formats), (name, field)


@pytest.mark.parametrize(
    "route",
    [route for route in served_routes() if route.paginated],
    ids=lambda route: route.path,
)
def test_every_served_list_takes_limit_and_cursor_and_answers_one_page_shape(
    route: Route,
) -> None:
    operation = SPEC["paths"][route.path][route.method.value.lower()]
    query = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]
    page = SCHEMAS[reference["$ref"].rsplit("/", 1)[1]]

    assert set(query) == {"limit", "cursor"}
    assert (
        query["limit"]["schema"]["minimum"],
        query["limit"]["schema"]["maximum"],
    ) == (
        1,
        100,
    )
    assert set(page["properties"]) == {"items", "next_cursor"}
    assert set(page["required"]) == {"items", "next_cursor"}
    assert page["properties"]["items"]["type"] == "array"


def test_the_question_route_documents_its_body() -> None:
    body = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]

    schema = body["content"]["application/json"]["schema"]
    assert (body["required"], set(schema["properties"])) == (True, {"text"})


def test_no_schema_is_shaped_like_a_model_provider_protocol() -> None:
    question = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]
    schemas = {
        **SCHEMAS,
        "Question": question["content"]["application/json"]["schema"],
    }
    for name, schema in schemas.items():
        assert not PROVIDER_FIELDS & set(schema.get("properties", {})), name
        assert not any(
            word in name.lower() for word in ("openai", "chat", "completion")
        ), name


async def call(method: str, path: str) -> httpx.Response:
    """A request with no token to the app with nothing wired."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as http:
        return await http.request(method, path)


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("POST", "/v1/answer", 404, "not_found"),
        ("GET", "/v1/nothing-here", 404, "not_found"),
        ("GET", "/v1/questions", 405, "method_not_allowed"),
        ("PUT", "/v1/job-decisions", 405, "method_not_allowed"),
        ("GET", "/v1/job-decisions/", 404, "not_found"),
        ("POST", "/v1/questions/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_answers_with_the_one_error_shape(
    method: str, path: str, status: int, code: str
) -> None:
    response = await call(method, path)

    assert response.status_code == status
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["code"] == code
