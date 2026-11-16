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
