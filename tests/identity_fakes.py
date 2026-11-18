"""A stand-in authenticator for endpoint tests that are not about identity.

It accepts one fixed bearer token and gives one fixed reader, so tests of
streaming and failures can call the protected endpoints. The identity tests
use the real `Authenticator`.
"""

from uuid import UUID

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.identity.context import AuthenticationError, Unauthenticated

READER = AuthorizationContext(
    user_id=UserId(UUID(int=0xA11CE)), group_ids=frozenset({GroupId(UUID(int=0xE))})
)
AUTHORIZATION = {"Authorization": "Bearer reader-token"}


class StaticAuthenticator:
    """Gives `READER` for `AUTHORIZATION`, and refuses anything else."""

    async def authenticate(self, headers: list[str]) -> AuthorizationContext:
        if headers == [AUTHORIZATION["Authorization"]]:
            return READER
        error = (
            AuthenticationError.INVALID_TOKEN if headers else AuthenticationError.NONE
        )
        raise Unauthenticated("test", error=error)


TRUSTED = StaticAuthenticator()
