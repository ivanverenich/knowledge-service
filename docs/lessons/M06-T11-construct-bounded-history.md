# M06-T11 — Construct bounded history

Source task: [M06-T11](../curriculum/milestones/M06-answers-and-conversations/M06-T11-construct-bounded-history.md)

## What you will do

This lesson builds the History a follow-up question needs: the earlier turns of the reader's own Conversation that fit a token budget. "And for contractors?" means nothing on its own; with the turn before it, it does. History must also be safe: an old Answer is text written from Documents, so if the reader has since lost access to one of them, that Answer must not reach the model again.

- A migration lets each assistant Message remember how it ended and which Chunks it cited.
- `add_turn` stores a Question and its reply together.
- `build_history` fits turns into the budget: recent ones whole, older ones as their Questions.
- `load_history` reads the reader's Conversation and keeps only the Answers whose Chunks they may still see.

| The task asks for | Where it happens |
|---|---|
| Recent and summarized turns under a token budget | Step 5: `build_history` keeps recent turns whole, then older Questions, within `history_tokens` |
| Exclude failed content | Step 5: only `answered` replies are shown; failed, abstained, and legacy ones are withheld |
| Exclude unauthorized content | Step 5: `load_history` checks every cited Chunk with `eligible_chunks_predicate` |
| Retain references needed for follow-ups | Steps 2 to 4: an Answer's cited Chunks are stored; each kept turn carries them |
| Tests: long conversations, model changes, deletion, permission revocation | Step 6: `tests/test_history.py` and `tests/test_history_integration.py` |
| Authorization before data leaves storage | Step 5: the Conversation is read owner-scoped, and Chunk visibility is decided in SQL |
| No seeded text in telemetry | Step 6: a planted marker never reaches a log record or a repr |

This lesson includes coding: Steps 2 to 6 write code, and Step 2 adds a database migration. Steps 1, 7, and 8 do not. The integration tests need the test database.

Terms used in this lesson:

- **History:** the earlier turns of a Conversation that are sent with a new Question, so the model can follow a conversation.
- **Turn:** one Question and the reply to it.
- **Summarized turn:** here, an older turn represented by its Question alone. It is a deliberate, cheap summary: a follow-up mostly needs to know what was asked before, not every word of every Answer.
- **Withheld:** an Answer whose text History leaves out, while its Question stays.
- **Reference:** one Chunk an Answer cited, with its Document and content version, stored so History can check later whether that Chunk is still current and visible.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** M06-T10 stores owner-scoped Conversations and Messages with `read_conversation`. M06-T03's `ContextBudget` reserves `history_tokens` and says "M06-T11 fills it". M06-T05's `confirm_citations` already checks cited Chunks against the reader's permissions in one SQL statement.
- **A Message must remember more.** To leave out a failed Answer, History must know it failed; to leave out an Answer from a revoked Document, it must know what that Answer cited. So `messages` gets an `outcome` column, and a new table `message_references` stores each cited Chunk with its Document and version. There is no foreign key to `chunks` on purpose: when a Chunk is deleted, its reference must stay, so History can tell it is gone.
- **Older assistant Messages.** Messages stored before this migration have no outcome. History cannot check what they were written from, so it withholds them.
- **Whose content is checked.** The Questions are the User's own words and always stay. An Answer is shown only when it was `answered` and every Chunk it cited is still current, in the same Document and version, and visible to this reader now. The check is the one retrieval uses, run in SQL.
- **An Answer can repeat an earlier one.** Each Answer was written with the earlier turns in its History, so it may repeat what an earlier Answer said. If an earlier Answer is withheld because a Document became hidden, every Answer after it is withheld too. That is cautious: one revoked Document early in a Conversation hides the Answers after it, but never shows hidden content.
- **Recent whole, older summarized.** History is filled from the newest turn back: whole turns while they fit, then Questions alone while they fit, then nothing. What is kept is always the most recent stretch, with no gaps.
- **Tokens.** Counted as in the context budget: UTF-8 bytes plus a small framing per text, an upper bound for byte-level tokenizers such as OpenAI's. A model with a smaller budget gets a shorter stretch of the same Conversation; a History built for one budget is never over it, whichever model wrote the old Answers.
- **A turn is stored together.** `add_turn` stores a Question and its reply in one transaction, after the reply is known. The row lock from M06-T10 is held until it commits, so another tab's turn cannot come between them, and History can pair each reply with the Question right before it. A savepoint takes the Question back if the reply cannot be stored.
- **What the review found.** An adversarial review attacked History with forged and stale references, revoked Groups, legacy rows, migrations with existing rows, budgets, and hostile input, on real PostgreSQL. Forged references, wrong versions, purged Chunks, and partly hidden Answers were all withheld, and History was never over budget. It found these problems, each fixed with a test that fails without the fix:
  - A later Answer that repeated a revoked one still reached History, because only its own Chunks were checked. Now an Answer is withheld when any earlier Answer is.
  - Two tabs could store their Questions and replies interleaved, and History paired a reply with the wrong Question. Now `add_turn` stores a turn at once, and a reply pairs only with the Question right before it.
  - A content version above PostgreSQL's integer range reached the database and failed there untyped. Now `MessageReference` refuses it.
  - A row written past the store, such as an `answered` reply without references, makes its Conversation unreadable. This is documented: the store is the only writer.
- **Where the code lives.** A new migration; `conversations.py`, `persistence/schema.py`, and `persistence/conversations.py`; the new `answering/history.py`.
- **Known limits.** Withholding later Answers after a revoked one is cautious rather than exact; storing which earlier Answers each Answer actually saw would be exact. The rules a Message checks live in Python, so a row written past the store can make its Conversation unreadable. `load_history` reads the owner's withheld Answers into memory before leaving them out; they are the owner's own Messages. Nothing calls `add_turn` or `load_history` yet; the follow-up flow of M06-T12 will.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 7 checks the predictions.

Create `docs/evidence/M06-T11-construct-bounded-history.md`:

```markdown
# M06-T11 — Construct bounded History evidence

## Invariant

`load_history` returns None for a Conversation that is not the reader's
live Conversation. Otherwise it returns the most recent stretch of turns
that fits `budget_tokens`: whole turns first, then older turns as their
Questions alone, with no gaps and never over the budget. An Answer's text
is included only when it was `answered` and every Chunk it and every
earlier Answer cited is current, in the same Document and version, and
visible to the reader now under `eligible_chunks_predicate`; otherwise it
is withheld and its Question stays. Each kept Answer carries the Chunks
it cited. `add_turn` stores a Question and its reply together, so a reply
always follows its own Question. Records and reprs hold no Question or
Answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.history"`
fails with `ModuleNotFoundError`.

## Predictions

- What History holds for a Conversation of 50 long turns with a budget for only a few: ...
- What happens to an earlier Answer, and to the Answers after it, when the reader loses access to a Document the earlier Answer cited: ...
- How many integration tests fail if `load_history` stops checking whether the reader may see each cited Chunk, and why not all of the "changed Document" cases: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 7 can prove wrong.

### Step 2 — Coding step: store outcomes and references

This step adds the two pieces of data History needs to the database.

**In plain words:** Every assistant Message gets a field that says whether it answered, abstained, or failed. A new table lists, for each Answer, the Chunks it cited, with the Document and the version of each. The migration adds both, and the table definitions in Python say the same, so a test can check they agree.

| Part | What it holds |
|---|---|
| `messages.outcome` | `answered`, `abstained`, `failed`, or nothing; only an assistant Message may have one |
| `message_references` | One row per cited Chunk: its position, Chunk, Document, and version; removed with its Message |

Create `migrations/versions/a9b0c1d2e3f4_message_outcomes_and_references.py`:

```python
"""message outcomes and references

Revision ID: a9b0c1d2e3f4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"


def upgrade() -> None:
    """Remember how each Answer ended and which Chunks it cited.

    Existing assistant Messages keep no outcome; History treats them as
    unverified and leaves their text out.
    """
    op.add_column("messages", sa.Column("outcome", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_messages_outcome",
        "messages",
        "outcome is null or (role = 'assistant'"
        " and outcome in ('answered', 'abstained', 'failed'))",
    )
    op.create_table(
        "message_references",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "message_id", "chunk_id", name="uq_message_references_chunk"
        ),
        sa.CheckConstraint("position >= 0", name="ck_message_references_position"),
        sa.CheckConstraint(
            "content_version >= 1", name="ck_message_references_content_version"
        ),
    )


def downgrade() -> None:
    """Forget Message outcomes and references."""
    op.drop_table("message_references")
    op.drop_constraint("ck_messages_outcome", "messages", type_="check")
    op.drop_column("messages", "outcome")
```

In `src/knowledge_service/persistence/schema.py`:

Find:

```python
        nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
    ),
    sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
    sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
)

message_feedback = sa.Table(
    "message_feedback",
    metadata,
    sa.Column(
```

Replace with:

```python
        nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=16), nullable=True),
    sa.UniqueConstraint(
        "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
    ),
    sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
    sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
    sa.CheckConstraint(
        "outcome is null or (role = 'assistant'"
        " and outcome in ('answered', 'abstained', 'failed'))",
        name="ck_messages_outcome",
    ),
)

message_references = sa.Table(
    "message_references",
    metadata,
    sa.Column(
        "message_id",
        sa.Uuid(),
        sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("position", sa.Integer(), primary_key=True),
    sa.Column("chunk_id", sa.Uuid(), nullable=False),
    sa.Column("document_id", sa.Uuid(), nullable=False),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("message_id", "chunk_id", name="uq_message_references_chunk"),
    sa.CheckConstraint("position >= 0", name="ck_message_references_position"),
    sa.CheckConstraint(
        "content_version >= 1", name="ck_message_references_content_version"
    ),
)

message_feedback = sa.Table(
    "message_feedback",
    metadata,
    sa.Column(
```

Check:

```bash
uv run alembic heads
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_migrations_integration.py -m integration -q
```

Expected: `a9b0c1d2e3f4 (head)`, and the migration tests pass: the migrated schema matches the declared tables, and the migrations go down and up again.

### Step 3 — Coding step: outcomes and references on a Message

This step teaches the domain what a reply may carry.

**In plain words:** A Message can now say how it ended and which Chunks it cited. The rules are strict: only an assistant Message has an outcome, only an Answer cites Chunks, it cites at least one and at most twenty, each once, and every reference names a real Chunk, its Document, and a version the database can store.

| Part | What it holds or checks |
|---|---|
| `MessageOutcome` | `ANSWERED`, `ABSTAINED`, `FAILED` |
| `MessageReference` | A Chunk, its Document, and the content version it read; at most `MAX_STORED_VERSION` |
| `Message.outcome`, `Message.references` | Checked together: an Answer, and only an Answer, cites 1 to 20 distinct Chunks |

In `src/knowledge_service/conversations.py`:

**1.** Find:

```python
"""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import ConversationId, MessageId, UserId


class InvalidConversation(ValueError):
    """A Conversation, Message, or feedback value violates a domain invariant."""


```

Replace with:

```python
"""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    MessageId,
    UserId,
)


class InvalidConversation(ValueError):
    """A Conversation, Message, or feedback value violates a domain invariant."""


```

**2.** Find:

```python
    """Who produced a Message."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageRating(StrEnum):
    """The thumbs signal a User attaches to one Message."""

    UP = "up"
    DOWN = "down"

```

Replace with:

```python
    """Who produced a Message."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageOutcome(StrEnum):
    """How an assistant Message ended; only an Answer may feed History."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    FAILED = "failed"


# As many as an Answer can cite (`answering.evidence.MAX_EVIDENCE`).
MAX_MESSAGE_REFERENCES = 20
MAX_STORED_VERSION = 2**31 - 1


@dataclass(frozen=True, slots=True)
class MessageReference:
    """A Chunk an Answer cited, in the version it read."""

    chunk_id: ChunkId
    document_id: DocumentId
    content_version: ContentVersion

    def __post_init__(self) -> None:
        identifiers = ((self.chunk_id, ChunkId), (self.document_id, DocumentId))
        if (
            not all(
                type(value) is kind and type(value.value) is UUID
                for value, kind in identifiers
            )
            or type(self.content_version) is not ContentVersion
            # The column is a PostgreSQL integer.
            or self.content_version.number > MAX_STORED_VERSION
        ):
            raise InvalidConversation(
                "a reference names a Chunk, its Document, and version"
            )


class MessageRating(StrEnum):
    """The thumbs signal a User attaches to one Message."""

    UP = "up"
    DOWN = "down"

```

**3.** Find:

```python
    message_id: MessageId
    conversation_id: ConversationId
    ordinal: int
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidConversation("message ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidConversation("message text must not be empty")
        _require_utc(self.created_at, "message created_at")


@dataclass(frozen=True, slots=True)
class MessageFeedback:
    """One User's current rating of one Message.

```

Replace with:

```python
    message_id: MessageId
    conversation_id: ConversationId
    ordinal: int
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime
    # An assistant Message stored before outcomes existed has none.
    outcome: MessageOutcome | None = None
    references: tuple[MessageReference, ...] = ()

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidConversation("message ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidConversation("message text must not be empty")
        _require_utc(self.created_at, "message created_at")
        if self.outcome is not None and (
            type(self.outcome) is not MessageOutcome
            or self.role is not MessageRole.ASSISTANT
        ):
            raise InvalidConversation("only an assistant Message has an outcome")
        references = self.references
        if type(references) is not tuple or not all(
            type(reference) is MessageReference for reference in references
        ):
            raise InvalidConversation("references must be a tuple of MessageReference")
        answered = self.outcome is MessageOutcome.ANSWERED
        if answered != bool(references):
            raise InvalidConversation("an Answer, and only an Answer, cites Chunks")
        if len(references) > MAX_MESSAGE_REFERENCES:
            raise InvalidConversation("an Answer cites too many Chunks")
        if len({reference.chunk_id for reference in references}) != len(references):
            raise InvalidConversation("an Answer cites a Chunk once")


@dataclass(frozen=True, slots=True)
class MessageFeedback:
    """One User's current rating of one Message.

```

Check:

```bash
uv run pytest tests/test_conversations.py tests/test_conversation_limits.py -q
```

Expected: the existing tests pass, because both new fields default to "nothing".

### Step 4 — Coding step: store and read them, and store a turn at once

This step writes outcomes and references with a Message, reads them back, and adds `add_turn`.

**In plain words:** Adding a reply now also stores how it ended and the Chunks it cited, in the same transaction. Reading a Conversation brings them back with each Message. A new function stores a Question and its reply together, after the reply is known, so no other Message can land between them; if the reply cannot be stored, the Question is taken back too.

| Function | What changes |
|---|---|
| `add_message` | Takes `outcome` and `references`, checks them before any SQL, and stores the references with the Message |
| `load_messages` | Reads each Message's outcome and references |
| `add_turn` | Stores a Question and its reply in one savepoint, under the Conversation's lock |

In `src/knowledge_service/persistence/conversations.py`:

**1.** Find:

```python
"""Store Conversations, their Messages, and feedback on them.

The answering flow uses only the owner-scoped functions: `start_conversation`,
`add_message`, `read_conversation`, and `delete_conversation`. Each takes the
caller's `UserId` and puts it in the SQL itself, so another User's
Conversation never leaves the database. They promise:

- **One answer for "not yours".** A Conversation that is missing, another
  User's, deleted, or past its retention deadline reads as None and refuses
  a Message with `ConversationNotFound`, the same in every case.
- **Order from the database.** `add_message` locks the Conversation's row,
  so concurrent Messages take turns; each gets the next ordinal, 0, 1, 2,
```

Replace with:

```python
"""Store Conversations, their Messages, and feedback on them.

The answering flow uses only the owner-scoped functions: `start_conversation`,
`add_message`, `add_turn`, `read_conversation`, and `delete_conversation`.
Each takes the caller's `UserId` and puts it in the SQL itself, so another
User's Conversation never leaves the database. They promise:

- **One answer for "not yours".** A Conversation that is missing, another
  User's, deleted, or past its retention deadline reads as None and refuses
  a Message with `ConversationNotFound`, the same in every case.
- **Order from the database.** `add_message` locks the Conversation's row,
  so concurrent Messages take turns; each gets the next ordinal, 0, 1, 2,
```

**2.** Find:

```python
  deletion time is never earlier than the last Message.
- **Any clock, one zone.** Every `now` must carry a time zone and is turned
  into UTC first, so a deadline is always the same span of real time.
- **Bounded.** `ConversationLimits` bounds the retention, the number of
  Messages, and each Message's length, and refuses text PostgreSQL cannot
  store, before any SQL runs.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
"""

from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
```

Replace with:

```python
  deletion time is never earlier than the last Message.
- **Any clock, one zone.** Every `now` must carry a time zone and is turned
  into UTC first, so a deadline is always the same span of real time.
- **Bounded.** `ConversationLimits` bounds the retention, the number of
  Messages, and each Message's length, and refuses text PostgreSQL cannot
  store, before any SQL runs.
- **A turn stays together.** `add_turn` stores a Question and its reply in
  one transaction, with how the reply ended and the Chunks an Answer
  cited, so History can trust that a reply follows its own Question.
- **Only through the store.** The rules a Message checks, such as "an
  Answer cites Chunks", live in Python, not in the database; a row written
  past these functions can make its Conversation unreadable.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
"""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
```

**3.** Find:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, JobId, MessageId, UserId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
    message_feedback,
    messages,
)


def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
```

Replace with:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageOutcome,
    MessageRating,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    JobId,
    MessageId,
    UserId,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
    message_feedback,
    message_references,
    messages,
)


def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
```

**4.** Find:

```python
        "message_id": message.message_id.value,
        "conversation_id": message.conversation_id.value,
        "ordinal": message.ordinal,
        "role": message.role.value,
        "text": message.text,
        "created_at": message.created_at,
    }


def _message_from(record: Mapping[str, object]) -> Message:
    try:
        role = MessageRole(cast(str, record["role"]))
    except ValueError:
        raise InvalidConversation("stored message role is not a known value") from None

    return Message(
        message_id=MessageId(cast(UUID, record["message_id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        ordinal=cast(int, record["ordinal"]),
        role=role,
        text=cast(str, record["text"]),
        created_at=cast(datetime, record["created_at"]),
    )


def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
```

Replace with:

```python
        "message_id": message.message_id.value,
        "conversation_id": message.conversation_id.value,
        "ordinal": message.ordinal,
        "role": message.role.value,
        "text": message.text,
        "created_at": message.created_at,
        "outcome": None if message.outcome is None else message.outcome.value,
    }


def _reference_rows(message: Message) -> list[dict[str, object]]:
    return [
        {
            "message_id": message.message_id.value,
            "position": position,
            "chunk_id": reference.chunk_id.value,
            "document_id": reference.document_id.value,
            "content_version": reference.content_version.number,
        }
        for position, reference in enumerate(message.references)
    ]


def _message_from(
    record: Mapping[str, object],
    references: tuple[MessageReference, ...] = (),
) -> Message:
    try:
        role = MessageRole(cast(str, record["role"]))
        stored = record.get("outcome")
        outcome = None if stored is None else MessageOutcome(cast(str, stored))
    except ValueError:
        raise InvalidConversation("a stored role or outcome is not known") from None

    return Message(
        message_id=MessageId(cast(UUID, record["message_id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        ordinal=cast(int, record["ordinal"]),
        role=role,
        text=cast(str, record["text"]),
        created_at=cast(datetime, record["created_at"]),
        outcome=outcome,
        references=references,
    )


def _reference_from(record: Mapping[str, object]) -> MessageReference:
    return MessageReference(
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
    )


def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
```

**5.** Find:

```python

async def load_messages(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> tuple[Message, ...]:
    """Read a Conversation's Messages in ordinal order."""
    statement = (
        sa.select(messages)
        .where(messages.c.conversation_id == conversation_id.value)
        .order_by(messages.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_message_from(dict(record)) for record in records)


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
```

Replace with:

```python

async def load_messages(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> tuple[Message, ...]:
    """Read a Conversation's Messages in ordinal order, with their references."""
    statement = (
        sa.select(messages)
        .where(messages.c.conversation_id == conversation_id.value)
        .order_by(messages.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    cited = (
        (
            await connection.execute(
                sa.select(message_references)
                .join(
                    messages, messages.c.message_id == message_references.c.message_id
                )
                .where(messages.c.conversation_id == conversation_id.value)
                .order_by(
                    message_references.c.message_id, message_references.c.position
                )
            )
        )
        .mappings()
        .all()
    )
    references: dict[UUID, list[MessageReference]] = {}
    for record in cited:
        references.setdefault(cast(UUID, record["message_id"]), []).append(
            _reference_from(dict(record))
        )
    return tuple(
        _message_from(
            dict(record),
            tuple(references.get(cast(UUID, record["message_id"]), ())),
        )
        for record in records
    )


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
```

**6.** Find:

```python
    owner_id: UserId,
    conversation_id: ConversationId,
    role: MessageRole,
    text: str,
    now: datetime,
    limits: ConversationLimits,
) -> Message:
    """Append a Message to the owner's live Conversation, at the next ordinal.

    Raises `InvalidConversation` for bad text or role before any SQL runs,
    `ConversationNotFound` when no live Conversation of this owner has this
    ID, and `ConversationFull` at the limit.
    """
    checked = _limits(limits).checked_text(text)
    if type(role) is not MessageRole:
        raise InvalidConversation("role must be a MessageRole")
    now = _utc(now)
    # Locked, so concurrent Messages take turns for the next ordinal.
    record = (
        (
            await connection.execute(
                sa.select(conversations)
                .where(_live(owner_id, conversation_id, now))
```

Replace with:

```python
    owner_id: UserId,
    conversation_id: ConversationId,
    role: MessageRole,
    text: str,
    now: datetime,
    limits: ConversationLimits,
    outcome: MessageOutcome | None = None,
    references: tuple[MessageReference, ...] = (),
) -> Message:
    """Append a Message to the owner's live Conversation, at the next ordinal.

    An assistant Message carries how it ended; an Answer also carries the
    Chunks it cited, stored with it in the same transaction.

    Raises `InvalidConversation` for bad text or role before any SQL runs,
    `ConversationNotFound` when no live Conversation of this owner has this
    ID, and `ConversationFull` at the limit.
    """
    checked = _limits(limits).checked_text(text)
    if type(role) is not MessageRole:
        raise InvalidConversation("role must be a MessageRole")
    now = _utc(now)
    # Built once before any SQL, so a bad outcome or reference never waits
    # for the lock; its ordinal and time are set under the lock.
    draft = Message(
        message_id=MessageId.new(),
        conversation_id=ConversationId(_id(conversation_id)),
        ordinal=0,
        role=role,
        text=checked,
        created_at=now,
        outcome=outcome,
        references=references,
    )
    # Locked, so concurrent Messages take turns for the next ordinal.
    record = (
        (
            await connection.execute(
                sa.select(conversations)
                .where(_live(owner_id, conversation_id, now))
```

**7.** Find:

```python
        )
    ).one()
    if count >= limits.max_messages:
        raise ConversationFull("the conversation holds as many messages as allowed")
    # Clocks of two servers can disagree; time never runs backwards here.
    created_at = max(now, conversation.last_message_at)
    message = Message(
        message_id=MessageId.new(),
        conversation_id=conversation_id,
        ordinal=0 if last is None else last + 1,
        role=role,
        text=checked,
        created_at=created_at,
    )
    await connection.execute(sa.insert(messages).values(_message_row(message)))
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation_id.value)
        .values(last_message_at=created_at)
    )
    return message


async def read_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
```

Replace with:

```python
        )
    ).one()
    if count >= limits.max_messages:
        raise ConversationFull("the conversation holds as many messages as allowed")
    # Clocks of two servers can disagree; time never runs backwards here.
    created_at = max(now, conversation.last_message_at)
    message = replace(
        draft, ordinal=0 if last is None else last + 1, created_at=created_at
    )
    await connection.execute(sa.insert(messages).values(_message_row(message)))
    if message.references:
        await connection.execute(
            sa.insert(message_references), _reference_rows(message)
        )
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation_id.value)
        .values(last_message_at=created_at)
    )
    return message


async def add_turn(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    conversation_id: ConversationId,
    question: str,
    reply: str,
    outcome: MessageOutcome,
    references: tuple[MessageReference, ...] = (),
    now: datetime,
    limits: ConversationLimits,
) -> tuple[Message, Message]:
    """Store a Question and its reply together, once the reply is known.

    The first Message's row lock is held to the end of the transaction, so
    no other Message comes between the two, and History pairs a reply with
    the Question right before it. Both are checked before any SQL; if the
    second cannot be stored, a savepoint takes back the first as well.
    """
    if type(outcome) is not MessageOutcome:
        raise InvalidConversation("a reply must say how it ended")
    _limits(limits).checked_text(question)
    Message(
        message_id=MessageId.new(),
        conversation_id=ConversationId(_id(conversation_id)),
        ordinal=0,
        role=MessageRole.ASSISTANT,
        text=_limits(limits).checked_text(reply),
        created_at=_utc(now),
        outcome=outcome,
        references=references,
    )
    async with connection.begin_nested():
        asked = await add_message(
            connection,
            owner_id=owner_id,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            text=question,
            now=now,
            limits=limits,
        )
        replied = await add_message(
            connection,
            owner_id=owner_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            text=reply,
            now=now,
            limits=limits,
            outcome=outcome,
            references=references,
        )
    return asked, replied


async def read_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
```

Check:

```bash
uv run pyright src/knowledge_service/persistence/conversations.py
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Expected: 0 errors, and the M06-T10 store tests still pass.

### Step 5 — Coding step: History

This step writes the module that builds History.

**In plain words:** It reads the reader's own Conversation, asks the database which of the cited Chunks this reader may still see as they are now, and pairs each Question with its reply. Then it walks back from the newest turn, adding whole turns while they fit the budget and then older Questions alone. An Answer's text goes in only if it succeeded and nothing it or any earlier Answer cited has become hidden; otherwise just its Question goes in.

| Part | What it holds or does |
|---|---|
| `HistoryTurn` | A Question, its Answer or None, and the Answer's references |
| `History` | `earlier_questions`, the whole `turns`, `tokens`, `left_out`, `withheld` |
| `build_history` | The pure fitting: no database, so every rule is easy to test |
| `load_history` | Owner-scoped read, one SQL check of cited Chunks, then `build_history`; None when it is not the reader's |
| `current_references_statement` | The cited Chunks that are current and visible, under `eligible_chunks_predicate` |
| `_turns`, `_shown` | Pairing a reply with the Question right before it; which Answers may be shown |

Create `src/knowledge_service/answering/history.py`:

```python
"""History: the earlier turns of a Conversation that fit the History budget.

A follow-up such as "and for contractors?" means nothing without the turns
before it. `load_history` reads the reader's own Conversation, keeps the
Answers whose cited Chunks the reader may still see, and `build_history`
fits the rest into `history_tokens` (see `answering.context_budget`).

What History promises:

- **Owner first.** `load_history` reads through `read_conversation`, so a
  Conversation that is missing, another User's, deleted, or expired gives
  None, the same in every case.
- **Only Answers the reader may still see.** An Answer counts only when it
  was `ANSWERED` and every Chunk it cited is still current, in the same
  Document and version, and passes `eligible_chunks_predicate` for this
  reader now. Otherwise its text is withheld: a failed or abstained turn
  holds no knowledge, and a revoked, deleted, or rewritten Document must
  not reach the model again through an old Answer. The Question stays,
  because the User wrote it.
- **Nor what it may have repeated.** An Answer was written with the
  earlier Answers in its History, so it may repeat them. It is shown only
  when the Chunks of every earlier Answer are current too; one revoked
  Document early in a Conversation withholds every later Answer.
- **Recent turns whole, older ones as their Questions.** Turns are taken
  from the newest back, each with its Question and its Answer, while they
  fit. From the first that does not fit, older turns are summarized by
  their Questions alone, which is what a follow-up most needs to name its
  subject, while those fit. Everything older is left out. So what is kept
  is always the most recent stretch of the Conversation.
- **Never over budget.** Tokens are counted as in the context budget, UTF-8
  bytes plus a framing allowance per text, an upper bound for byte-level
  tokenizers such as OpenAI's, so a History that fits one model's budget
  fits it whatever model wrote the earlier Answers. A smaller budget, for a model with a
  smaller window, keeps a shorter stretch.
- **References kept.** Each whole turn keeps the Chunks its Answer cited,
  so a follow-up can be traced to the Documents it continues from.
- **Quiet.** Questions and Answers stay out of every repr; one log record
  holds counts only.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.conversations import (
    InvalidConversation,
    Message,
    MessageOutcome,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.conversations import read_conversation
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)

# Role markers and separators around each text in the prompt.
TEXT_FRAMING_TOKENS = 8


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One earlier Question and, if it may be shown, its Answer."""

    question: str = field(repr=False)
    answer: str | None = field(repr=False)
    references: tuple[MessageReference, ...]


@dataclass(frozen=True, slots=True)
class History:
    """The earlier turns that fit, oldest first.

    `earlier_questions` summarizes the turns before `turns` by their
    Questions alone. `left_out` counts turns not even summarized;
    `withheld` counts Answers whose text may not be shown.
    """

    earlier_questions: tuple[str, ...] = field(repr=False)
    turns: tuple[HistoryTurn, ...]
    tokens: int
    left_out: int
    withheld: int


@dataclass(frozen=True, slots=True)
class _Turn:
    question: Message
    answer: Message | None


def build_history(
    transcript: Transcript,
    current: frozenset[MessageReference],
    *,
    budget_tokens: int,
) -> History:
    """Fit the Conversation's turns into `budget_tokens`, newest first.

    `current` holds the cited Chunks the reader may still see, as they are
    now; an Answer is shown only when every Chunk it cited is in it.
    """
    if type(transcript) is not Transcript:
        raise InvalidConversation("transcript must be a Transcript")
    if type(current) is not frozenset:
        raise InvalidConversation("current must be a frozenset of references")
    if type(budget_tokens) is not int or budget_tokens < 0:
        raise InvalidConversation("budget_tokens must be a whole number, 0 or more")
    turns = _turns(transcript.messages)
    shown = _shown(turns, current)
    withheld = sum(
        turn.answer is not None and answer is None
        for turn, answer in zip(turns, shown, strict=True)
    )
    used = 0
    index = len(turns) - 1
    whole: list[HistoryTurn] = []
    while index >= 0:
        answer = shown[index]
        cost = _tokens(turns[index].question.text) + (
            0 if answer is None else _tokens(answer.text)
        )
        if used + cost > budget_tokens:
            break
        used += cost
        whole.append(
            HistoryTurn(
                question=turns[index].question.text,
                answer=None if answer is None else answer.text,
                references=() if answer is None else answer.references,
            )
        )
        index -= 1
    summarized: list[str] = []
    while index >= 0:
        cost = _tokens(turns[index].question.text)
        if used + cost > budget_tokens:
            break
        used += cost
        summarized.append(turns[index].question.text)
        index -= 1
    return History(
        earlier_questions=tuple(reversed(summarized)),
        turns=tuple(reversed(whole)),
        tokens=used,
        left_out=index + 1,
        withheld=withheld,
    )


async def load_history(
    connection: AsyncConnection,
    context: AuthorizationContext,
    *,
    conversation_id: ConversationId,
    now: datetime,
    budget_tokens: int,
) -> History | None:
    """The reader's History of this Conversation, or None if it is not theirs.

    Build it before the new Question is stored, so it holds only earlier
    turns.
    """
    if type(context) is not AuthorizationContext:
        raise InvalidConversation("context must be an AuthorizationContext")
    transcript = await read_conversation(
        connection,
        owner_id=context.user_id,
        conversation_id=conversation_id,
        now=now,
    )
    if transcript is None:
        return None
    cited = {
        reference for message in transcript.messages for reference in message.references
    }
    current: frozenset[MessageReference] = frozenset()
    if cited:
        statement = current_references_statement(
            [reference.chunk_id.value for reference in cited],
            eligibility=eligible_chunks_predicate(context),
        )
        records = (await connection.execute(statement)).mappings().all()
        found = {
            (record["chunk_id"], record["document_id"], record["content_version"])
            for record in records
        }
        current = frozenset(
            reference
            for reference in cited
            if (
                reference.chunk_id.value,
                reference.document_id.value,
                reference.content_version.number,
            )
            in found
        )
    history = build_history(transcript, current, budget_tokens=budget_tokens)
    _log(context, history, cited=len(cited), current=len(current))
    return history


def current_references_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.

    A Chunk is current when its version is its Document's; replacing a
    version gives its Chunks new IDs, so an old ID finds nothing.
    """
    return (
        sa.select(chunks.c.chunk_id, chunks.c.document_id, chunks.c.content_version)
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunks.c.chunk_id
            == sa.any_(
                sa.bindparam(
                    "chunk_ids", list(chunk_ids), type_=ARRAY(sa.Uuid()), unique=True
                )
            ),
            chunks.c.content_version == documents.c.content_version,
            eligibility,
        )
    )


def _turns(messages: tuple[Message, ...]) -> list[_Turn]:
    """Pair each Question with the assistant Message right after it.

    `add_turn` stores a Question and its reply together, so a reply always
    follows its own Question. A Question with no reply after it is a turn
    with no Answer; a reply after anything but a Question is no turn.
    """
    turns: list[_Turn] = []
    previous: Message | None = None
    for message in messages:
        if message.role is MessageRole.USER:
            turns.append(_Turn(question=message, answer=None))
        elif previous is not None and previous.role is MessageRole.USER:
            turns[-1] = _Turn(question=turns[-1].question, answer=message)
        previous = message
    return turns


def _shown(
    turns: list[_Turn], current: frozenset[MessageReference]
) -> list[Message | None]:
    """Each Answer, if it and every Answer before it cite only current Chunks."""
    shown: list[Message | None] = []
    earlier_current = True
    for turn in turns:
        answer = turn.answer
        if answer is None or answer.outcome is not MessageOutcome.ANSWERED:
            shown.append(None)
            continue
        own_current = all(r in current for r in answer.references)
        shown.append(answer if own_current and earlier_current else None)
        earlier_current = earlier_current and own_current
    return shown


def _tokens(text: str) -> int:
    """An upper bound: at least one byte per token, plus the framing."""
    return len(text.encode("utf-8")) + TEXT_FRAMING_TOKENS


def _log(
    context: AuthorizationContext, history: History, *, cited: int, current: int
) -> None:
    fields = {
        "user_id": str(context.user_id.value),
        "turns": len(history.turns),
        "earlier_questions": len(history.earlier_questions),
        "left_out": history.left_out,
        "withheld": history.withheld,
        "tokens": history.tokens,
        "cited_chunks": cited,
        "current_chunks": current,
    }
    logger.info("history built", extra={"history": fields})
```

Two details matter here. `build_history` takes the set of current references as plain data, so its rules are tested without a database, and `load_history` is only the part that asks PostgreSQL. And a reference counts as current only when the Chunk, its Document, and its version all match: a rewritten Document gives its Chunks new IDs, so an old reference finds nothing.

Check:

```bash
uv run python -c "import knowledge_service.answering.history"
uv run pyright src/knowledge_service/answering/history.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 6 — Coding step: tests for the budget, withholding, and revocation

This step proves each promise, first as pure rules and then on real PostgreSQL.

**In plain words:** The unit tests build Conversations in memory: long ones that must be cut, ones with failed replies, ones where the reader lost a Chunk, and random ones for a property test that History is never over budget and always the most recent stretch. The integration tests publish real Documents, store turns with their citations, then revoke access, publish a new version, or delete the Document, and check what History still shows.

| Test | What it proves |
|---|---|
| `test_a_short_conversation_is_kept_whole_and_in_order` | Success, with references |
| `test_a_long_conversation_keeps_recent_turns_and_summarizes_older_ones` | Long conversations: recent whole, older as Questions |
| `test_a_smaller_budget_for_another_model_keeps_a_shorter_recent_stretch` | Model changes: a smaller budget keeps a shorter recent stretch |
| `test_an_answer_whose_document_changed_is_withheld` | Permission revocation, a new version, a tombstone: the highest-risk edge |
| `test_a_later_answer_that_may_repeat_a_withheld_one_is_withheld_too` | Hidden content cannot return through a later Answer |
| `test_a_deleted_conversation_has_no_history` | Deletion |
| `test_another_users_conversation_has_no_history_for_the_reader` | Ownership |
| `test_turns_stored_at_once_never_interleave` | Concurrent turns keep each reply with its Question |
| `test_history_is_never_over_budget_and_always_the_most_recent_stretch` | Property test over random Conversations and budgets |
| `test_bad_input_is_a_typed_refusal` | Typed failure: `InvalidConversation` |
| `test_answers_the_reader_may_still_see_are_kept_with_their_references` | No planted marker in log records |

Create `tests/test_history.py`:

```python
"""Bounded History: recent turns whole, older ones as Questions, never over."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.answering.history import (
    TEXT_FRAMING_TOKENS,
    History,
    build_history,
)
from knowledge_service.conversations import (
    Conversation,
    InvalidConversation,
    Message,
    MessageOutcome,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    MessageId,
    UserId,
)

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
OWNER = UserId.parse("00000000-0000-0000-0000-000000000070")
CONVERSATION = Conversation.open(
    conversation_id=ConversationId.new(),
    owner_id=OWNER,
    opened_at=NOW,
    retention=timedelta(days=30),
)
MARKER = "PLANTED-MARKER-7719"
ANSWERED = MessageOutcome.ANSWERED
NOTHING_CURRENT: frozenset[MessageReference] = frozenset()


def reference(number: int) -> MessageReference:
    return MessageReference(
        chunk_id=ChunkId(UUID(int=1000 + number)),
        document_id=DocumentId(UUID(int=2000 + number)),
        content_version=ContentVersion(1),
    )


Turn = tuple[str, str | None, MessageOutcome | None, tuple[MessageReference, ...]]


def transcript(*turns: Turn) -> Transcript:
    messages: list[Message] = []
    for question, answer, outcome, references in turns:
        messages.append(message(len(messages), MessageRole.USER, question))
        if answer is not None:
            messages.append(
                message(
                    len(messages),
                    MessageRole.ASSISTANT,
                    answer,
                    outcome=outcome,
                    references=references,
                )
            )
    return Transcript(conversation=CONVERSATION, messages=tuple(messages))


def message(ordinal: int, role: MessageRole, text: str, **fields: Any) -> Message:
    return Message(
        message_id=MessageId.new(),
        conversation_id=CONVERSATION.conversation_id,
        ordinal=ordinal,
        role=role,
        text=text,
        created_at=NOW + timedelta(seconds=ordinal),
        **fields,
    )


def answered(question: str, answer: str, *cited: int) -> Turn:
    return (question, answer, ANSWERED, tuple(reference(n) for n in cited))


def everything(conversation: Transcript) -> frozenset[MessageReference]:
    return frozenset(r for m in conversation.messages for r in m.references)


def cost(*texts: str) -> int:
    return sum(len(text.encode()) + TEXT_FRAMING_TOKENS for text in texts)


# --- What is kept ------------------------------------------------------------------


def test_a_short_conversation_is_kept_whole_and_in_order() -> None:
    conversation = transcript(
        answered("How many days of leave?", "24 days.", 1),
        answered("Do they carry over?", "Up to 5 days.", 2),
    )

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [t.question for t in history.turns] == [
        "How many days of leave?",
        "Do they carry over?",
    ]
    assert [t.answer for t in history.turns] == ["24 days.", "Up to 5 days."]
    assert history.turns[1].references == (reference(2),)
    assert history.earlier_questions == ()
    assert history.left_out == 0
    assert history.tokens == cost(
        "How many days of leave?", "24 days.", "Do they carry over?", "Up to 5 days."
    )


def test_a_long_conversation_keeps_recent_turns_and_summarizes_older_ones() -> None:
    turns = [answered(f"Question {n}?", "x" * 400, n) for n in range(50)]
    conversation = transcript(*turns)
    budget = 3 * cost("Question 49?", "x" * 400) + 5 * cost("Question 40?")

    history = build_history(
        conversation, everything(conversation), budget_tokens=budget
    )

    assert [t.question for t in history.turns] == [
        "Question 47?",
        "Question 48?",
        "Question 49?",
    ]
    assert history.earlier_questions == tuple(f"Question {n}?" for n in range(42, 47))
    assert history.left_out == 42
    assert history.tokens <= budget


def test_a_smaller_budget_for_another_model_keeps_a_shorter_recent_stretch() -> None:
    conversation = transcript(
        *(answered(f"Question {n}?", "y" * 200, n) for n in range(10))
    )
    current = everything(conversation)

    large = build_history(conversation, current, budget_tokens=4_000)
    small = build_history(conversation, current, budget_tokens=600)

    assert len(small.turns) < len(large.turns)
    assert small.turns == large.turns[len(large.turns) - len(small.turns) :]


def test_no_budget_means_no_history() -> None:
    conversation = transcript(answered("How many days?", "24 days.", 1))

    history = build_history(conversation, everything(conversation), budget_tokens=0)

    assert history.turns == ()
    assert history.earlier_questions == ()
    assert history.left_out == 1
    assert history.tokens == 0


# --- What is withheld ----------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", [MessageOutcome.ABSTAINED, MessageOutcome.FAILED, None]
)
def test_an_answer_that_did_not_succeed_is_withheld_but_its_question_stays(
    outcome: MessageOutcome | None,
) -> None:
    conversation = transcript(("How many days?", "I could not answer.", outcome, ()))

    history = build_history(conversation, frozenset(), budget_tokens=1_000)

    assert history.turns[0].question == "How many days?"
    assert history.turns[0].answer is None
    assert history.withheld == 1


def test_an_answer_citing_a_chunk_the_reader_may_no_longer_see_is_withheld() -> None:
    conversation = transcript(
        answered("And leave?", "24 days.", 3),
        answered("Secret project budget?", f"It is 2M. {MARKER}", 1, 2),
    )
    # The reader lost one of the two Chunks of the second Answer.
    current = frozenset({reference(2), reference(3)})

    history = build_history(conversation, current, budget_tokens=1_000)

    assert history.turns[0].answer == "24 days."
    assert history.turns[1].answer is None
    assert history.turns[1].references == ()
    assert history.withheld == 1
    assert all(MARKER not in (t.answer or "") for t in history.turns)


def test_a_reference_of_another_version_is_not_current() -> None:
    conversation = transcript(answered("How many days?", "24 days.", 1))
    newer = MessageReference(
        chunk_id=reference(1).chunk_id,
        document_id=reference(1).document_id,
        content_version=ContentVersion(2),
    )

    history = build_history(conversation, frozenset({newer}), budget_tokens=1_000)

    assert history.turns[0].answer is None


def test_questions_without_replies_and_replies_without_questions() -> None:
    messages = (
        message(0, MessageRole.ASSISTANT, "Welcome.", outcome=MessageOutcome.FAILED),
        message(1, MessageRole.USER, "First?"),
        message(2, MessageRole.USER, "Second?"),
        message(
            3,
            MessageRole.ASSISTANT,
            "24.",
            outcome=ANSWERED,
            references=(reference(1),),
        ),
    )
    conversation = Transcript(conversation=CONVERSATION, messages=messages)

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [(t.question, t.answer) for t in history.turns] == [
        ("First?", None),
        ("Second?", "24."),
    ]


# --- Properties --------------------------------------------------------------------

_TURN = st.tuples(
    st.text(min_size=1, max_size=60).filter(str.strip),
    st.one_of(st.none(), st.text(min_size=1, max_size=200).filter(str.strip)),
    st.booleans(),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_TURN, max_size=30), st.integers(min_value=0, max_value=3_000))
def test_history_is_never_over_budget_and_always_the_most_recent_stretch(
    raw: list[tuple[str, str | None, bool]], budget: int
) -> None:
    turns: list[Turn] = [
        (
            question,
            answer,
            None if answer is None else (ANSWERED if ok else MessageOutcome.FAILED),
            (reference(n),) if answer is not None and ok else (),
        )
        for n, (question, answer, ok) in enumerate(raw)
    ]
    conversation = transcript(*turns)

    history = build_history(
        conversation, everything(conversation), budget_tokens=budget
    )

    kept = len(history.turns) + len(history.earlier_questions)
    assert history.tokens <= budget
    assert history.left_out + kept == len(turns)
    expected = [question for question, *_ in turns][history.left_out :]
    assert [
        *history.earlier_questions,
        *(t.question for t in history.turns),
    ] == expected


# --- Typed failures and quiet reprs ---------------------------------------------------


@pytest.mark.parametrize(
    ("conversation", "current", "budget"),
    [
        ("not a transcript", NOTHING_CURRENT, 10),
        (transcript(), {reference(1)}, 10),
        (transcript(), NOTHING_CURRENT, -1),
        (transcript(), NOTHING_CURRENT, True),
    ],
)
def test_bad_input_is_a_typed_refusal(
    conversation: Any, current: Any, budget: Any
) -> None:
    with pytest.raises(InvalidConversation):
        build_history(conversation, current, budget_tokens=budget)


def test_history_holds_no_text_in_its_repr() -> None:
    conversation = transcript(answered(f"Q {MARKER}?", f"A {MARKER}.", 1))
    history = build_history(conversation, everything(conversation), budget_tokens=1)
    summary = build_history(conversation, frozenset(), budget_tokens=1_000)

    assert isinstance(history, History)
    assert MARKER not in repr(history)
    assert MARKER not in repr(summary)


@pytest.mark.parametrize(
    "fields",
    [
        {"role": MessageRole.USER, "outcome": ANSWERED, "references": (reference(1),)},
        {"role": MessageRole.ASSISTANT, "outcome": ANSWERED, "references": ()},
        {
            "role": MessageRole.ASSISTANT,
            "outcome": MessageOutcome.FAILED,
            "references": (reference(1),),
        },
        {
            "role": MessageRole.ASSISTANT,
            "outcome": ANSWERED,
            "references": (reference(1), reference(1)),
        },
        {
            "role": MessageRole.ASSISTANT,
            "outcome": ANSWERED,
            "references": tuple(reference(n) for n in range(21)),
        },
        {"role": MessageRole.ASSISTANT, "outcome": "answered", "references": ()},
    ],
)
def test_a_message_and_its_references_must_agree(fields: dict[str, Any]) -> None:
    role = fields.pop("role")

    with pytest.raises(InvalidConversation):
        message(0, role, "24 days.", **fields)


# --- Review regressions -------------------------------------------------------------


def test_a_later_answer_that_may_repeat_a_withheld_one_is_withheld_too() -> None:
    conversation = transcript(
        answered("Secret project budget?", f"It is 2M {MARKER}.", 1),
        answered("And leave?", f"Budget {MARKER} as said; leave 24 days.", 2),
    )

    history = build_history(
        conversation, frozenset({reference(2)}), budget_tokens=1_000
    )

    assert [t.answer for t in history.turns] == [None, None]
    assert history.withheld == 2


def test_a_failed_answer_does_not_withhold_the_answers_after_it() -> None:
    conversation = transcript(
        ("Secret project budget?", "I could not answer.", MessageOutcome.FAILED, ()),
        answered("And leave?", "24 days.", 2),
    )

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [t.answer for t in history.turns] == [None, "24 days."]


def test_a_version_too_large_to_store_is_refused() -> None:
    with pytest.raises(InvalidConversation):
        MessageReference(
            chunk_id=ChunkId(UUID(int=1)),
            document_id=DocumentId(UUID(int=2)),
            content_version=ContentVersion(2**31),
        )
```

Create `tests/test_history_integration.py`:

```python
"""History in PostgreSQL: stored references, revocation, deletion, ownership."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.history import History, load_history
from knowledge_service.conversations import (
    ConversationFull,
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.conversations import (
    add_turn,
    delete_conversation,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000057")
OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e4")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e5")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f2")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
LIMITS = ConversationLimits()
MARKER = "PLANTED-MARKER-7719"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    text: str,
    *,
    readers: tuple[Subject, ...] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> MessageReference:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=name, modified_at=observed_at, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=observed_at
    )
    document = reconciled.document
    async with runtime.transaction() as connection:
        chunk_id = await connection.scalar(
            sa.select(chunks.c.chunk_id).where(
                chunks.c.document_id == document.document_id.value
            )
        )
    return MessageReference(
        chunk_id=ChunkId(cast(UUID, chunk_id)),
        document_id=DocumentId(document.document_id.value),
        content_version=ContentVersion(document.content_version.number),
    )


async def conversation(
    runtime: DatabaseRuntime,
    *turns: tuple[str, str, MessageOutcome, tuple[MessageReference, ...]],
    owner: UserId = READER,
) -> ConversationId:
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=owner, now=OBSERVED_AT, limits=LIMITS
        )
        for question, answer, outcome, references in turns:
            await add_turn(
                connection,
                owner_id=owner,
                conversation_id=started.conversation_id,
                question=question,
                reply=answer,
                outcome=outcome,
                references=references,
                now=OBSERVED_AT,
                limits=LIMITS,
            )
    return started.conversation_id


async def history(
    runtime: DatabaseRuntime,
    conversation_id: ConversationId,
    context: AuthorizationContext = CONTEXT,
) -> History | None:
    async with runtime.transaction() as connection:
        return await load_history(
            connection,
            context,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )


async def leave_and_carry(
    runtime: DatabaseRuntime,
) -> tuple[ConversationId, MessageReference, MessageReference]:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    carry = await publish(runtime, "carry.txt", "Unused leave carries over 5 days.")
    conversation_id = await conversation(
        runtime,
        ("How many days?", f"24 days. {MARKER}", MessageOutcome.ANSWERED, (leave,)),
        ("Do they carry over?", "Up to 5 days.", MessageOutcome.ANSWERED, (carry,)),
        ("And next year?", "I could not answer.", MessageOutcome.FAILED, ()),
    )
    return conversation_id, leave, carry


async def test_answers_the_reader_may_still_see_are_kept_with_their_references(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    conversation_id, leave, carry = await leave_and_carry(runtime)

    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.answer for t in built.turns] == [
        f"24 days. {MARKER}",
        "Up to 5 days.",
        None,
    ]
    assert [t.references for t in built.turns] == [(leave,), (carry,), ()]
    assert built.withheld == 1
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)


async def test_stored_outcomes_and_references_read_back_as_written(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id, leave, _ = await leave_and_carry(runtime)

    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert [m.outcome for m in transcript.messages] == [
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.FAILED,
    ]
    assert transcript.messages[1].references == (leave,)


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 24 days.",
        readers=(Subject.user(OTHER),),
        observed_at=LATER,
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 26 days.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone],
    ids=["access-revoked", "new-version", "tombstoned"],
)
async def test_an_answer_whose_document_changed_is_withheld(
    runtime: DatabaseRuntime,
    change: Callable[[DatabaseRuntime], Awaitable[None]],
) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)

    await change(runtime)
    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.question for t in built.turns] == [
        "How many days?",
        "Do they carry over?",
        "And next year?",
    ]
    # The second Answer was written with the first in its History.
    assert [t.answer for t in built.turns] == [None, None, None]
    assert [t.references for t in built.turns] == [(), (), ()]
    assert built.withheld == 3


async def test_a_deleted_conversation_has_no_history(runtime: DatabaseRuntime) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)
    async with runtime.transaction() as connection:
        await delete_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert await history(runtime, conversation_id) is None


async def test_another_users_conversation_has_no_history_for_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    foreign = await conversation(
        runtime,
        ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,)),
        owner=OTHER,
    )

    assert await history(runtime, foreign) is None
    other = AuthorizationContext(user_id=OTHER, group_ids=frozenset({TEAM}))
    assert await history(runtime, foreign, other) is not None


# --- Review regressions -------------------------------------------------------------


async def test_turns_stored_at_once_never_interleave(runtime: DatabaseRuntime) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(runtime)

    async def ask(question: str, reply: str) -> None:
        async with runtime.transaction() as connection:
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question=question,
                reply=reply,
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=LIMITS,
            )

    await asyncio.gather(*(ask(f"Q{n}?", f"A{n}.") for n in range(6)))
    built = await history(runtime, conversation_id)

    assert built is not None
    assert len(built.turns) == 6
    assert all(t.answer == f"A{t.question[1:-1]}." for t in built.turns)


async def test_a_turn_that_does_not_fit_leaves_no_half_behind(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(
        runtime, ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,))
    )
    three = ConversationLimits(max_messages=3)

    async with runtime.transaction() as connection:
        with pytest.raises(ConversationFull):
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question="And next year?",
                reply="26 days.",
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=three,
            )
    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert len(transcript.messages) == 2
```

Check:

```bash
uv run pytest tests/test_history.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_history_integration.py -m integration -q
```

Expected: 25 passed, then 9 passed.

### Step 7 — No coding in this step: break it and decide

This step shows what the permission check in History protects, and records what you learned.

**Action 1:** break History on purpose. In `src/knowledge_service/answering/history.py`, in `load_history`, replace:

```python
            eligibility=eligible_chunks_predicate(context),
```

with:

```python
            eligibility=sa.true(),
```

Run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_history_integration.py -m integration -q
```

Expected: 2 failed, 7 passed: the access-revoked and tombstoned cases show the old Answer again. The new-version case still passes, because a rewritten Document gives its Chunks new IDs and the version check alone withholds the Answer; only the permission check stops an Answer from a Document the reader may no longer see. Put the line back and run again until all pass.

**Action 2:** under `## Decision`, write:

- why an Answer is checked against the reader's permissions now, not when it was written;
- why a later Answer is withheld when an earlier one is;
- why older turns are summarized by their Questions;
- why a reference has no foreign key to `chunks`;
- what is left for later: the follow-up flow (M06-T12), and exact tracking of which earlier Answers each Answer saw;
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 8 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, add History to the `answering` row, the new table to the diagram, and its rules to the table of rules:

**1.** Find:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Replace with:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Find:

````markdown
    chunks ||--o{ chunk_embeddings : "is embedded as"
    embedding_spaces ||--o{ chunk_embeddings : "holds"
    embedding_spaces ||--o{ embedding_migrations : "is moved from or to"
    index_configurations ||--o{ embedding_migrations : "describes the target of"
    conversations ||--o{ messages : "holds"
    messages ||--o{ message_feedback : "is rated by"
```

The rules each table enforces:

| Table | Rule |
|---|---|
````

Replace with:

````markdown
    chunks ||--o{ chunk_embeddings : "is embedded as"
    embedding_spaces ||--o{ chunk_embeddings : "holds"
    embedding_spaces ||--o{ embedding_migrations : "is moved from or to"
    index_configurations ||--o{ embedding_migrations : "describes the target of"
    conversations ||--o{ messages : "holds"
    messages ||--o{ message_feedback : "is rated by"
    messages ||--o{ message_references : "cites"
```

The rules each table enforces:

| Table | Rule |
|---|---|
````

**3.** Find:

```markdown
| `index_configurations` | One row per settings `fingerprint` (64 lowercase hex digits); rows are only inserted, so a changed setting is a new row. |
| `embedding_spaces` | One space per `(provider, model, dimensions)`; dimensions from 1 to 16000; at most one space is current. |
| `embedding_migrations` | A state of `dual_writing`, `switched`, or `cleaned`; `updated_at >= started_at`; a deleted space leaves the row with that space set to null. |
| `chunk_embeddings` | One vector per `(chunk_id, space_id)`; the vector's length must equal its space's dimensions; removed with its Chunk or its space. |
| `access_grants` | One Grant per `(document_id, subject_kind, subject_id)`; a `public` Grant carries no subject and every other kind carries one. |
| `conversations` | `retention_deadline > created_at`; `last_message_at >= created_at`. |
| `messages` | One Message per `(conversation_id, ordinal)`. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | No raw content, and a metadata bag capped at 1024 bytes. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

Replace with:

```markdown
| `index_configurations` | One row per settings `fingerprint` (64 lowercase hex digits); rows are only inserted, so a changed setting is a new row. |
| `embedding_spaces` | One space per `(provider, model, dimensions)`; dimensions from 1 to 16000; at most one space is current. |
| `embedding_migrations` | A state of `dual_writing`, `switched`, or `cleaned`; `updated_at >= started_at`; a deleted space leaves the row with that space set to null. |
| `chunk_embeddings` | One vector per `(chunk_id, space_id)`; the vector's length must equal its space's dimensions; removed with its Chunk or its space. |
| `access_grants` | One Grant per `(document_id, subject_kind, subject_id)`; a `public` Grant carries no subject and every other kind carries one. |
| `conversations` | `retention_deadline > created_at`; `last_message_at >= created_at`. |
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | No raw content, and a metadata bag capped at 1024 bytes. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_history.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2221 passed for `make test-unit`, 25 more than M06-T10, and 370 passed for `make test-integration`, 9 more. If it says there is no test database, start it with `docker start knowledge-service-postgres`.

**Action 3:** complete the evidence file:

- **Design:** the migration; outcomes and references on a Message; `add_turn`; how History pairs, checks, and fits turns; the token count.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review found that forged references, wrong versions, purged Chunks, revoked Groups, and legacy rows were all withheld, and History was never over budget; it found four problems, three fixed with tests that failed before the fix and one documented: a later Answer repeating a revoked one still reached History; interleaved turns paired a reply with the wrong Question; a content version past the integer range failed untyped; a row written past the store makes its Conversation unreadable.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T11 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T12, and commit with `m06-t11: construct bounded history`.

## Reflection questions

1. What does `load_history` hide from the code that will answer a follow-up? List what that code never has to check about old Answers.
2. Someone suggests caching each Conversation's History to save a database query per question. What could a cached History show that a fresh one would withhold, and which test would catch it?
3. Withholding every Answer after a revoked one is cautious. What would you have to store with each Answer to withhold only the Answers that really saw the revoked text?
4. A User asks "And for contractors?" after five turns about leave. With a small budget, which parts of the Conversation does History keep, and is that enough for the follow-up to make sense?
5. Which Conversation would you build next to try to get hidden content into History, and what do you expect History to do with it?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] The migration adds `messages.outcome` and `message_references`, and the schema matches it.
- [ ] A Message's outcome and references are checked together: only an Answer cites Chunks.
- [ ] `add_message` stores references with the Message; `load_messages` reads them back.
- [ ] `add_turn` stores a Question and its reply together, or neither.
- [ ] History keeps recent turns whole, then older Questions, never over budget, always the most recent stretch.
- [ ] Failed, abstained, and legacy replies are withheld; their Questions stay.
- [ ] An Answer whose Chunks, or an earlier Answer's Chunks, are no longer current and visible is withheld.
- [ ] A Conversation that is not the reader's has no History.
- [ ] No Question or Answer text reaches a log record or a repr.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
