"""The M06 gate: a fake Citation is stopped and recorded; the `checking` event."""

import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import UUID

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionReason,
    Decision,
)
from knowledge_service.answering.events import (
    EVENT_PROTOCOL_VERSION,
    AnswerEventWriter,
    InvalidEventStream,
    StreamDefect,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import EvidenceId, build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.errors import FailureKind
from knowledge_service.language import Language
from knowledge_service.model import ChatModelUnavailable
from test_answer_events import (
    ANSWER,
    CITATIONS,
    REQUEST_ID,
    USAGE,
    blocks,
    read,
    refused,
    renumbered,
)
from test_streaming import (
    CITED,
    SIGNALS,
    Deciding,
    Model,
    Source,
    chunks,
    names,
    pieces,
    rebuilt,
    reply_json,
    streamer,
)

UNSUPPORTED = AbstentionDecision(
    Decision.ABSTAIN, AbstentionReason.UNSUPPORTED, POLICY_VERSION
)


def ended(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    (record,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    return record.answer_stream  # type: ignore[attr-defined, no-any-return]


# --- Diagnose a fake Citation ---------------------------------------------------------


def another_requests_id() -> str:
    """A well-formed Evidence ID that belongs to another request's Evidence."""
    return build_evidence([candidate(1)]).items[0].evidence_id.value


@pytest.mark.parametrize(
    "fake",
    [
        lambda: EvidenceId.new().value,
        another_requests_id,
        lambda: "ev_000000000000",
        lambda: MARKER,
    ],
    ids=["invented", "other-request", "malformed", "planted"],
)
async def test_a_fake_citation_withdraws_the_streamed_answer_and_is_recorded(
    fake: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = f"Every employee receives 24 days of leave. {MARKER}"
    model = Model(final=reply_json(answer, citations=[fake()]))

    sent = await chunks(streamer(model))

    # The text went out as it arrived; no Citation ever did.
    assert "text_delta" in names(sent)
    assert "citation" not in names(sent)
    result = rebuilt(sent)
    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED
    assert result.text == ""
    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == (
        "abstained",
        "replaced",
    )
    assert (record["reason"], record["defect"]) == ("answer_failed", "invalid_citation")
    assert all(MARKER not in repr(r.__dict__) for r in caplog.records)


async def test_a_real_citation_is_recorded_without_a_reason_or_defect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    sent = await chunks(streamer(Model(final=reply_json(citations=[CITED]))))

    assert rebuilt(sent).outcome is StreamOutcome.ANSWERED
    record = ended(caplog)
    assert (record["reason"], record["reply"], record["defect"]) == (
        None,
        "answered",
        None,
    )


# --- The `checking` event from the server ---------------------------------------------


async def test_checking_comes_once_after_the_live_text_and_before_the_decision() -> (
    None
):
    sent = await chunks(streamer(Model()))

    order = names(sent)
    assert order.count("checking") == 1
    at = order.index("checking")
    assert set(order[1:at]) == {"text_delta"}
    assert order[at + 1 :] == ["citation", "usage", "completion"]


@pytest.mark.parametrize(
    "answering",
    ["no-evidence", "model-failed"],
)
async def test_no_checking_is_sent_when_no_reply_arrived(answering: str) -> None:
    if answering == "no-evidence":
        empty = Source(found=FoundEvidence(build_evidence([]), SIGNALS))
        sent = await chunks(streamer(Model(), source=empty))
    else:
        sent = await chunks(streamer(Model(error=ChatModelUnavailable())))

    assert "checking" not in names(sent)


# --- The `checking` event in the contract ---------------------------------------------


def with_checking(
    at: int, *deltas: str, decision: AbstentionDecision = ANSWER
) -> list[bytes]:
    """A stream with `checking` after the first `at` deltas, if `at` is not -1."""
    writer = AnswerEventWriter(REQUEST_ID)
    events = [writer.metadata()]
    for index, delta in enumerate(deltas):
        if index == at:
            events.append(writer.checking())
        events.append(writer.text_delta(delta))
    if at >= len(deltas):
        events.append(writer.checking())
    if decision.decision is Decision.ANSWER:
        events += [writer.citation(citation) for citation in CITATIONS]
    events += [writer.usage(USAGE), writer.completion(decision, Language.ENGLISH)]
    return events


def outcome(write: Callable[[], list[bytes]]) -> StreamedAnswer | StreamDefect:
    """The Answer a client rebuilds, or why the writer or the reader refused it."""
    try:
        return read(*write())
    except InvalidEventStream as error:
        return error.defect


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=6),
    st.integers(min_value=0, max_value=6),
    st.booleans(),
)
# Text with nothing visible: the writer refuses that Answer either way.
@example(deltas=[" "], at=0, answers=True)
def test_checking_changes_nothing_a_client_rebuilds(
    deltas: list[str], at: int, answers: bool
) -> None:
    decision = ANSWER if answers else UNSUPPORTED

    with_it = outcome(lambda: with_checking(at, *deltas, decision=decision))
    without = outcome(lambda: with_checking(-1, *deltas, decision=decision))

    assert with_it == without


def forged(names_: list[str]) -> list[bytes]:
    """A stream whose events come in the order `names_`, from a valid one."""
    parts = dict(blocks(with_checking(1, "Every employee ", "gets 24 days.")))
    # An abstention ends a stream whatever came before it but a Citation.
    parts["completion"] = blocks(with_checking(0, "x", decision=UNSUPPORTED))[-1][1]
    return renumbered([(name, parts[name]) for name in names_])


@pytest.mark.parametrize(
    ("order", "defect"),
    [
        (["checking"], StreamDefect.OUT_OF_ORDER),
        (["metadata", "checking", "checking"], StreamDefect.OUT_OF_ORDER),
        (["metadata", "checking", "text_delta", "checking"], StreamDefect.OUT_OF_ORDER),
        (["metadata", "citation", "checking"], StreamDefect.OUT_OF_ORDER),
        (["metadata", "usage", "checking"], StreamDefect.OUT_OF_ORDER),
        (["metadata", "completion", "checking"], StreamDefect.AFTER_TERMINAL),
    ],
    ids=[
        "before-metadata",
        "twice",
        "twice-around-text",
        "after-citation",
        "after-usage",
        "after-terminal",
    ],
)
def test_checking_out_of_place_is_refused(
    order: list[str], defect: StreamDefect
) -> None:
    assert refused(*forged(order)) is defect


def test_checking_with_data_is_refused() -> None:
    stream = renumbered(
        [
            ("metadata", blocks(with_checking(0, "x"))[0][1]),
            ("checking", {"stage": MARKER}),
        ]
    )

    assert refused(*stream) is StreamDefect.MALFORMED


# --- Review regressions ---------------------------------------------------------------


def first_version(stream: list[bytes]) -> list[bytes]:
    """The same events as a version 1 server wrote them."""
    parts = blocks(stream)
    parts[0][1]["version"] = "1"
    return renumbered(parts)


def test_the_writer_says_version_2_and_the_reader_still_reads_version_1() -> None:
    stream = with_checking(-1, "Every employee ", "gets 24 days.")

    assert EVENT_PROTOCOL_VERSION == "2"
    assert blocks(stream)[0][1]["version"] == "2"
    assert read(*first_version(stream)) == read(*stream)


def test_a_version_1_error_keeps_the_retry_owner_it_had_then() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    parts = blocks([writer.metadata(), writer.error(FailureKind.DEPENDENCY)])
    assert parts[1][1]["retry"] == "caller"
    parts[1][1]["retry"] = "service"

    assert read(*first_version(renumbered(parts))).error_code == (
        "dependency_unavailable"
    )
    assert refused(*renumbered(parts)) is StreamDefect.MALFORMED


def test_a_version_1_stream_with_checking_is_refused() -> None:
    stream = with_checking(1, "Every employee ", "gets 24 days.")

    assert refused(*first_version(stream)) is StreamDefect.MALFORMED


class Refusing(Model):
    """A model that declines to answer, billed."""

    async def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        yield ModelReply(text="", refused=True, usage=USAGE)


@pytest.mark.parametrize(
    ("model", "reply", "defect"),
    [
        (
            lambda: Model(final=reply_json(citations=["ev_bad"])),
            "invalid",
            "invalid_citation",
        ),
        (lambda: Refusing(), "refused", None),
        (
            lambda: Model(
                parts=pieces(reply_json("You get 99 days.")), final=reply_json()
            ),
            "answered",
            None,
        ),
    ],
    ids=["fake-citation", "refused", "streamed-text-differs"],
)
async def test_the_record_tells_apart_why_the_answer_failed(
    model: Any, reply: str, defect: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    sent = await chunks(streamer(model()))

    assert rebuilt(sent).reason is AbstentionReason.ANSWER_FAILED
    record = ended(caplog)
    assert (record["reply"], record["defect"]) == (reply, defect)


async def test_a_stream_that_fails_keeps_no_reason_of_a_refused_decision(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    wrong = AbstentionDecision(
        Decision.ANSWER, AbstentionReason.ANSWER_FAILED, POLICY_VERSION
    )

    sent = await chunks(streamer(Model(), finisher=Deciding(wrong)))

    assert names(sent)[-1] == "error"
    record = ended(caplog)
    assert (record["outcome"], record["reason"]) == ("failed", None)
