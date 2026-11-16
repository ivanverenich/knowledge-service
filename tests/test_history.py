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
