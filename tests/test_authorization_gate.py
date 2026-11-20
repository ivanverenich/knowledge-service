"""The M07 gate's new protected operation: a User deletes their Conversation.

`DELETE /v1/conversations/{conversation_id}` brings the three checks of the
milestone together. Identity: the owner is the User the verified token
names, and nothing in the request can name another. Role: the caller needs
`manage_own_conversations`, checked before the ID is even parsed. Grants: none
are needed, because a Conversation is the User's own, not a Document. These
tests run the real app and `Authenticator` over a fake store; the store's
own rules run in `test_authorization_gate_integration.py`.
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from knowledge_service.app import create_app
from knowledge_service.identifiers import ConversationId
from knowledge_service.identity.context import Authenticator, user_id_for
from knowledge_service.persistence.conversations import delete_conversation
from test_app_authentication import Deleting
from test_authorization_context import ALICE, ISSUER, POLICY, Verifier, verified

CONVERSATION = "0190a3a0-0000-7000-8000-000000000016"
REQUEST_ID = "0190a3a0-0000-7000-8000-0000000000aa"
MARKER = "GATE-MARKER-5531"
TOKENS = {
    "alice-token": verified(),
    "bob-token": verified(sub="bob"),
    "manager-token": verified(realm_access={"roles": ["content-manager"]}),
}


class Answering:
    """A store that deletes, and returns `found`, as a careless one might."""

    def __init__(self, found: bool) -> None:
        self.found = found

    async def delete(self, **request: Any) -> bool:
        return self.found


class Failing:
    """A Conversation store that raises `error` on every call."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def delete(self, **request: Any) -> None:
        raise self.error


def app_with(conversations: Any) -> Any:
    return create_app(
        authenticator=Authenticator(Verifier(TOKENS), POLICY),
        conversations=conversations,
    )


async def delete(
    app: Any,
    bearer: str | None = "alice-token",
    conversation_id: str = CONVERSATION,
    **extra: Any,
) -> httpx.Response:
    headers = {"X-Request-ID": REQUEST_ID, **extra.pop("headers", {})}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            "DELETE",
            f"/v1/conversations/{conversation_id}",
            headers=headers,
            **extra,
        )


# --- Success: the owner is whoever the token names ------------------------------


async def test_a_user_deletes_their_conversation_as_the_token_says() -> None:
    store = Deleting()

    response = await delete(app_with(store))

    assert response.status_code == 204
    assert response.content == b""
    assert store.calls == [
        {
            "owner_id": user_id_for(ISSUER, ALICE),
            "conversation_id": ConversationId.parse(CONVERSATION),
            "request_id": UUID(REQUEST_ID),
        }
    ]


async def test_nothing_in_the_request_can_name_another_owner() -> None:
    store = Deleting()
    bob = str(user_id_for(ISSUER, "bob").value)

    response = await delete(
        app_with(store),
        params={"owner_id": bob, "user_id": bob},
        headers={"X-User-ID": bob, "X-Forwarded-User": "bob"},
        json={"owner_id": bob},
    )

    assert response.status_code == 204
    assert [call["owner_id"] for call in store.calls] == [user_id_for(ISSUER, ALICE)]


async def test_two_users_deleting_the_same_id_each_act_only_as_themselves() -> None:
    store = Deleting()
    app = app_with(store)

    first = await delete(app, "alice-token")
    second = await delete(app, "bob-token")

    # Same answer, so bob cannot tell whether alice's Conversation existed.
    assert (first.status_code, first.content) == (second.status_code, second.content)
    assert [call["owner_id"] for call in store.calls] == [
        user_id_for(ISSUER, ALICE),
        user_id_for(ISSUER, "bob"),
    ]


async def test_the_answer_cannot_tell_a_deletion_from_nothing_to_delete() -> None:
    deleted = await delete(app_with(Answering(found=True)))
    nothing = await delete(app_with(Answering(found=False)))

    # Even a store that said which happened could not change the response.
    assert deleted.status_code == nothing.status_code == 204
    assert deleted.content == nothing.content == b""
    assert sorted(deleted.headers.items()) == sorted(nothing.headers.items())


# --- Highest-risk edge: who and whether, before which -----------------------------


async def test_a_caller_without_the_role_is_refused_before_the_id_is_read() -> None:
    store = Deleting()

    known = await delete(app_with(store), "manager-token")
    malformed = await delete(app_with(store), "manager-token", "not-an-id")

    # A content manager gets 403 whatever the ID, and the store is never asked.
    assert (known.status_code, malformed.status_code) == (403, 403)
    assert known.json()["code"] == "forbidden"
    assert store.calls == []


@pytest.mark.parametrize("token", [None, "unknown-token"])
async def test_a_request_without_a_valid_token_deletes_nothing(
    token: str | None,
) -> None:
    store = Deleting()

    response = await delete(app_with(store), token, "not-an-id")

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert store.calls == []


@pytest.mark.parametrize(
    "conversation_id",
    [
        "not-an-id",
        "0190a3a0",
        "%00",
        "0190a3a0-0000-7000-8000-00000000001g",
        # `UUID()` reads all of these as the same ID; the API does not.
        "{0190a3a0-0000-7000-8000-000000000016}",
        "urn:uuid:0190a3a0-0000-7000-8000-000000000016",
        "0190a3a0000070008000000000000016",
        "+190a3a0000070008000000000000016",
        "\u0660190a3a0-0000-7000-8000-000000000016",
    ],
)
async def test_a_malformed_id_from_a_permitted_caller_is_a_422(
    conversation_id: str,
) -> None:
    store = Deleting()

    response = await delete(app_with(store), conversation_id=conversation_id)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert store.calls == []


# --- Typed failures: an unusable store refuses, never guesses -----------------------


async def test_an_unreachable_database_is_a_503_with_retry_after() -> None:
    down = OperationalError("connect", {}, ConnectionRefusedError(MARKER))

    response = await delete(app_with(Failing(down)))

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"


async def test_an_app_without_a_store_deletes_nothing_and_says_so() -> None:
    response = await delete(app_with(None))

    assert response.status_code == 503
    assert response.json()["code"] == "conversations_unavailable"


async def test_a_store_error_names_nothing_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    response = await delete(app_with(Failing(RuntimeError(MARKER))))

    assert response.status_code == 500
    assert MARKER not in response.text
    assert all(MARKER not in str(vars(record)) for record in caplog.records)
    # Operators still see that it failed, and of what kind.
    assert "conversation delete failed: RuntimeError" in caplog.messages


async def test_the_store_refuses_a_request_id_that_is_not_a_uuid() -> None:
    # Checked before any SQL, so no connection is needed.
    with pytest.raises(TypeError):
        await delete_conversation(
            cast(Any, None),
            owner_id=user_id_for(ISSUER, ALICE),
            conversation_id=ConversationId.parse(CONVERSATION),
            now=datetime(2026, 11, 20, tzinfo=UTC),
            request_id=cast(Any, REQUEST_ID),
        )
