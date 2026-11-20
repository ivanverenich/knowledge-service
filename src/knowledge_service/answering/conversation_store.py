"""The caller's own Conversations, as an HTTP request reaches them.

`DatabaseConversationStore` is the `ConversationStore` an entry point gives
the app. It runs each operation in one transaction of its own, with the
owner from the caller's verified token, and keeps the store's promise at the
boundary:

- **Owner in the SQL.** The owner is the caller's `UserId`, never a value
  from the request, and `delete_conversation` puts it in the statement.
- **Nothing to tell apart.** `delete` returns nothing, whether it deleted
  the caller's Conversation or found nothing of theirs: missing, another
  User's, and already deleted all end the same way, so the response cannot
  reveal which.
- **Audited with the change.** The deletion, its purge Job, and its audit
  event commit together, or none of them does.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import ConversationId, UserId
from knowledge_service.persistence.conversations import delete_conversation


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DatabaseConversationStore:
    """The caller's own Conversations in PostgreSQL."""

    def __init__(
        self, runtime: DatabaseRuntime, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._runtime = runtime
        self._clock = clock

    async def delete(
        self, *, owner_id: UserId, conversation_id: ConversationId, request_id: UUID
    ) -> None:
        """Delete the owner's Conversation, if it is theirs and live."""
        async with self._runtime.transaction() as connection:
            await delete_conversation(
                connection,
                owner_id=owner_id,
                conversation_id=conversation_id,
                now=self._clock(),
                request_id=request_id,
            )
