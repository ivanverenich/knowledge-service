"""The OpenAI-compatible model list, read by OpenAI's own client.

The protocol test uses the `openai` SDK, as Open WebUI's server would use
the same endpoint: if the SDK reads the list, an OpenAI client can. Every
refusal must reach that client as OpenAI's error shape with the native
code, and the bearer token is checked exactly as on the native routes.
"""

import logging
from typing import Any

import httpx
import httpx2
import openai
import pytest
from pydantic import ValidationError

from knowledge_service.app import create_app
from knowledge_service.compatibility.models import (
    MODEL_CREATED,
    MODEL_OWNER,
    CompatibleModel,
    InvalidCompatibleModel,
)
from knowledge_service.identity.context import Authenticator
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.settings import Settings
from test_authorization_context import MARKER, POLICY, Verifier, verified

TOKENS = {
    "user-token": verified(),
    "staff-token": verified(
        realm_access={"roles": ["content-manager", "evaluator", "administrator"]}
    ),
    MARKER: verified(azp="account-console"),
}
BASE = "http://t/openai/v1"


def app_with(
    *,
    model: CompatibleModel | None = None,
    verifier: Verifier | None = None,
    authenticated: bool = True,
) -> Any:
    authenticator = Authenticator(verifier or Verifier(TOKENS), POLICY)
    return create_app(
        authenticator=authenticator if authenticated else None,
        compatible_model=model,
    )


def sdk(app: Any, token: str) -> openai.AsyncOpenAI:
    """OpenAI's client, pointed at the adapter, sending `token` as its key."""
    return openai.AsyncOpenAI(
        base_url=BASE,
        api_key=token,
        max_retries=0,
        # The SDK's own HTTP library, httpx2, with the app as its transport.
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


async def get(app: Any, path: str, **headers: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(path, headers=headers)


# The list, as OpenAI's client reads it.


async def test_openais_client_lists_exactly_the_one_knowledge_model() -> None:
    page = await sdk(app_with(), "user-token").models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]
    model = page.data[0]
    assert (model.object, model.created, model.owned_by) == (
        "model",
        MODEL_CREATED,
        MODEL_OWNER,
    )
    assert (model.model_extra or {})["name"] == "Knowledge base"


async def test_the_configured_model_is_the_only_one_shown() -> None:
    configured = CompatibleModel(model_id="team-docs:v2", name="Team docs")

    page = await sdk(app_with(model=configured), "user-token").models.list()

    assert [(m.id, (m.model_extra or {})["name"]) for m in page.data] == [
        ("team-docs:v2", "Team docs")
    ]


async def test_the_list_is_the_same_byte_for_byte_after_a_restart() -> None:
    first = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )
    second = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )

    assert first.status_code == 200
    assert first.content == second.content
    assert first.json() == {
        "object": "list",
        "data": [
            {
                "id": "knowledge-base",
                "object": "model",
                "created": MODEL_CREATED,
                "owned_by": MODEL_OWNER,
                "name": "Knowledge base",
            }
        ],
    }


# Bearer authentication, and its refusals in OpenAI's shape.


UNAUTHENTICATED = (
    "authentication_error",
    "unauthenticated",
    "A valid access token is required",
)


@pytest.mark.parametrize(
    ("headers", "status", "error", "challenge"),
    [
        ({}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer not-a-token"},
            401,
            UNAUTHENTICATED,
            'Bearer error="invalid_token"',
        ),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer two words"},
            400,
            (
                "invalid_request_error",
                "invalid_request",
                "The Authorization header is malformed",
            ),
            'Bearer error="invalid_request"',
        ),
        (
            {"Authorization": "Bearer staff-token"},
            403,
            (
                "permission_error",
                "forbidden",
                "Your role does not permit this operation",
            ),
            None,
        ),
    ],
    ids=[
        "no token",
        "unknown token",
        "another scheme",
        "malformed bearer",
        "no user role",
    ],
)
async def test_each_refusal_is_an_openai_error_with_the_native_code(
    headers: dict[str, str],
    status: int,
    error: tuple[str, str, str],
    challenge: str | None,
) -> None:
    response = await get(app_with(), "/openai/v1/models", **headers)

    error_type, code, message = error
    assert response.status_code == status
    assert response.json() == {
        "error": {"message": message, "type": error_type, "param": None, "code": code}
    }
    assert response.headers.get("www-authenticate") == challenge


@pytest.mark.parametrize(
    ("token", "error"),
    [
        ("not-a-token", openai.AuthenticationError),
        ("staff-token", openai.PermissionDeniedError),
    ],
)
async def test_openais_client_raises_its_own_error_for_each_refusal(
    token: str, error: type[openai.APIStatusError]
) -> None:
    with pytest.raises(error) as raised:
        await sdk(app_with(), token).models.list()

    assert raised.value.code == (
        "forbidden" if error is openai.PermissionDeniedError else "unauthenticated"
    )


@pytest.mark.parametrize(
    "app",
    [
        app_with(authenticated=False),
        app_with(verifier=Verifier(error=IdentityProviderUnavailable("down"))),
    ],
    ids=["no authenticator", "identity provider down"],
)
async def test_when_tokens_cannot_be_checked_the_answer_is_503(app: Any) -> None:
    response = await get(app, "/openai/v1/models", Authorization="Bearer user-token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert response.json()["error"]["type"] == "server_error"
    assert response.headers["retry-after"] == "5"


async def test_a_refusal_names_no_token_text_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    # MARKER is a valid token from an untrusted client: refused as unknown.
    response = await get(
        app_with(), "/openai/v1/models", Authorization=f"Bearer {MARKER}"
    )

    assert response.status_code == 401
    assert MARKER not in response.text + str(response.headers) + caplog.text


# The adapter's edges.


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/openai/v1/nothing", 404, "not_found"),
        ("GET", "/openai/v1/models/knowledge-base", 404, "not_found"),
        ("POST", "/openai/v1/models", 405, "method_not_allowed"),
        ("GET", "/openai/v1/models/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_under_the_adapter_is_an_openai_error(
    method: str, path: str, status: int, code: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with()), base_url="http://t"
    ) as http:
        response = await http.request(method, path)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


async def test_the_native_api_keeps_its_own_error_shape() -> None:
    response = await get(app_with(), "/v1/nothing")

    assert set(response.json()) == {"code", "message", "request_id"}


def test_the_adapter_is_not_in_the_native_openapi_document() -> None:
    spec = app_with().openapi()

    assert not any(path.startswith("/openai") for path in spec["paths"])
    assert "ModelList" not in spec.get("components", {}).get("schemas", {})


# The configured model.


@pytest.mark.parametrize(
    "model_id",
    ["", "Knowledge-Base", "knowledge base", "-knowledge", "a" * 65, "kb/1", "kb?"],
)
def test_a_model_id_a_client_could_not_use_as_is_is_refused(model_id: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(model_id=model_id)


@pytest.mark.parametrize(
    "name",
    [
        "",
        " Knowledge",
        "Knowledge\n",
        "a" * 65,
        chr(0x301),
        "Cafe" + chr(0x301),
        chr(0x200B) + chr(0x301),
    ],
    ids=[
        "empty",
        "space",
        "line break",
        "too long",
        "a lone accent",
        "decomposed",
        "nothing visible",
    ],
)
def test_a_model_name_is_visible_text(name: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(name=name)


def test_settings_configure_the_model_and_refuse_a_bad_one() -> None:
    settings = Settings(compatible_model_id="team-docs", compatible_model_name="Team")

    assert settings.compatible_model() == CompatibleModel("team-docs", "Team")
    with pytest.raises(ValidationError):
        Settings(compatible_model_id="Team Docs")


async def test_behind_a_path_prefix_an_unknown_path_is_still_an_openai_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with(), root_path="/api"),
        base_url="http://t",
    ) as http:
        listed = await http.get(
            "/api/openai/v1/models", headers={"Authorization": "Bearer user-token"}
        )
        unknown = await http.get("/api/openai/v1/nothing")

    assert listed.status_code == 200
    assert (unknown.status_code, set(unknown.json())) == (404, {"error"})


@pytest.mark.parametrize(
    "path", ["/openai/v1/models", "/openai/v1/nothing"], ids=["refused", "unknown"]
)
async def test_a_refusal_carries_the_request_id_as_a_header(path: str) -> None:
    request_id = "0b8f1a52-3c1d-4e6f-9a7b-2d4c6e8f0a1b"

    response = await get(app_with(), path, **{"X-Request-ID": request_id})

    assert response.status_code in {401, 404}
    assert response.headers["x-request-id"] == request_id
