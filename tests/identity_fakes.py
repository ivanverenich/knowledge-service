"""A stand-in authenticator for endpoint tests that are not about identity.

It accepts one fixed bearer token and gives one fixed caller, a reader with
the `user` role, so tests of streaming and failures can call the protected
endpoints. The identity tests
use the real `Authenticator`.
"""

from uuid import UUID

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.roles import ApplicationRole

READER = AuthorizationContext(
    user_id=UserId(UUID(int=0xA11CE)), group_ids=frozenset({GroupId(UUID(int=0xE))})
)
CALLER = Caller(reader=READER, roles=frozenset({ApplicationRole.USER}))
AUTHORIZATION = {"Authorization": "Bearer reader-token"}


class StaticAuthenticator:
    """Gives `CALLER` for `AUTHORIZATION`, and refuses anything else."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if headers == [AUTHORIZATION["Authorization"]]:
            return CALLER
        error = (
            AuthenticationError.INVALID_TOKEN if headers else AuthenticationError.NONE
        )
        raise Unauthenticated("test", error=error)


TRUSTED = StaticAuthenticator()
