"""The event contract of a streamed Answer: order, terminal events, rebuild."""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionReason,
    Decision,
    abstention_message,
)
from knowledge_service.answering.citations import Citation, resolve_citations
from knowledge_service.answering.events import (
    MAX_EVENT_CHARACTERS,
    MAX_TOKENS,
    AnswerEventReader,
    AnswerEventWriter,
    EventName,
    InvalidEventStream,
    StreamDefect,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind
from knowledge_service.language import Language

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000049")
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
EVIDENCE = build_evidence([candidate(1), candidate(2)])
CITATIONS: tuple[Citation, ...] = resolve_citations(
    EVIDENCE, [item.evidence_id.value for item in EVIDENCE.items]
)
ANSWER = AbstentionDecision(Decision.ANSWER, None, POLICY_VERSION)
UNSUPPORTED = AbstentionDecision(
    Decision.ABSTAIN, AbstentionReason.UNSUPPORTED, POLICY_VERSION
)
NO_EVIDENCE = AbstentionDecision(
    Decision.ABSTAIN, AbstentionReason.NO_EVIDENCE, POLICY_VERSION
)
DELTAS = ("Кожен працівник ", "отримує 24 дні ", "щорічної відпустки.")


def answer_stream(*deltas: str) -> list[bytes]:
    writer = AnswerEventWriter(REQUEST_ID)
    events = [writer.metadata()]
    events += [writer.text_delta(delta) for delta in deltas or DELTAS]
    events += [writer.citation(citation) for citation in CITATIONS]
    events += [writer.usage(USAGE), writer.completion(ANSWER, Language.ENGLISH)]
    return events


def read(*chunks: bytes) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in chunks:
        reader.feed(chunk)
    return reader.close()


def refused(*chunks: bytes) -> StreamDefect:
    with pytest.raises(InvalidEventStream) as caught:
        read(*chunks)
    return caught.value.defect


def event(event_id: int, name: str, data: Mapping[str, object]) -> bytes:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {name}\ndata: {body}\n\n".encode()


def blocks(stream: list[bytes]) -> list[tuple[str, dict[str, object]]]:
    """Each event of a stream as its name and data, to forge a stream."""
    parts: list[tuple[str, dict[str, object]]] = []
    for raw in stream:
        _, name_line, data_line, *_ = raw.decode().split("\n")
        data: dict[str, object] = json.loads(data_line.removeprefix("data: "))
        parts.append((name_line.removeprefix("event: "), data))
    return parts


def renumbered(parts: list[tuple[str, dict[str, object]]]) -> list[bytes]:
    return [event(number, *part) for number, part in enumerate(parts)]


# --- A complete stream rebuilds the Answer -----------------------------------


def test_a_client_rebuilds_the_complete_answer_from_its_events() -> None:
    result = read(*answer_stream())

    assert result.outcome is StreamOutcome.ANSWERED
    assert result.request_id == REQUEST_ID
    assert result.text == "".join(DELTAS)
    assert [c.evidence_id for c in result.citations] == [
        c.evidence_id.value for c in CITATIONS
    ]
    assert result.citations[0].title == "Policy 1"
    assert result.usage is not None
    assert result.usage.total_tokens == 330
    assert result.reason is None
    assert result.message is None


def test_the_stream_is_the_documented_text_format() -> None:
    first = answer_stream()[0].decode()

    assert first == (
        "id: 0\nevent: metadata\n"
        'data: {"version":"1","request_id":"00000000-0000-0000-0000-000000000049"}'
        "\n\n"
    )


def test_bytes_split_anywhere_rebuild_the_same_answer() -> None:
    whole = b"".join(answer_stream())
    # One byte at a time splits every Ukrainian letter's two bytes too.
    one_by_one = read(*(whole[i : i + 1] for i in range(len(whole))))

    assert one_by_one == read(whole)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=2_000), max_size=12))
def test_any_split_of_the_bytes_rebuilds_the_same_answer(cuts: list[int]) -> None:
    whole = b"".join(answer_stream())
    points = sorted({min(cut, len(whole)) for cut in cuts})
    chunks = [
        whole[a:b] for a, b in zip([0, *points], [*points, len(whole)], strict=True)
    ]

    assert read(*chunks) == read(whole)


def test_keep_alive_comments_between_events_are_ignored() -> None:
    stream = answer_stream()
    with_comments = [b": keep-alive\n"]
    with_comments += [part + b": ping\n" for part in stream[:-1]] + stream[-1:]

    assert read(*with_comments) == read(*stream)


def test_the_reader_returns_each_event_as_it_completes() -> None:
    reader = AnswerEventReader()
    stream = answer_stream()

    assert reader.feed(stream[0][:10]) == ()
    (metadata,) = reader.feed(stream[0][10:])

    assert metadata.event_id == 0
    assert metadata.name is EventName.METADATA


# --- Abstention and failure replace the streamed text ------------------------


def test_an_abstention_replaces_streamed_text_with_its_fixed_message() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [
        writer.metadata(),
        writer.text_delta(MARKER),
        writer.usage(USAGE),
        writer.completion(UNSUPPORTED, Language.UKRAINIAN),
    ]

    result = read(*stream)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.text == ""
    assert result.citations == ()
    assert result.reason is AbstentionReason.UNSUPPORTED
    assert result.message == abstention_message(
        AbstentionReason.UNSUPPORTED, Language.UKRAINIAN
    )
    assert result.usage is not None


def test_no_evidence_abstains_without_text_or_usage() -> None:
    writer = AnswerEventWriter(REQUEST_ID)

    result = read(writer.metadata(), writer.completion(NO_EVIDENCE, Language.MIXED))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.usage is None
    assert result.message == abstention_message(
        AbstentionReason.NO_EVIDENCE, Language.ENGLISH
    )


def test_an_error_ends_the_stream_with_its_public_code_and_keeps_the_usage() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [
        writer.metadata(),
        writer.text_delta(MARKER),
        writer.citation(CITATIONS[0]),
        writer.usage(USAGE),
        writer.error(FailureKind.DEPENDENCY),
    ]

    result = read(*stream)

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "dependency_unavailable"
    assert result.message == "A required service is unavailable"
    assert result.text == ""
    assert result.citations == ()
    assert result.usage is not None


def test_a_cancelled_request_has_no_error_event() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()

    with pytest.raises(InvalidEventStream) as caught:
        writer.error(FailureKind.CANCELLATION)

    assert caught.value.defect is StreamDefect.UNKNOWN_ERROR


# --- The writer refuses what the reader would refuse -------------------------


@pytest.mark.parametrize(
    ("before", "attempt", "defect"),
    [
        ((), "text", StreamDefect.OUT_OF_ORDER),
        (("metadata",), "metadata", StreamDefect.OUT_OF_ORDER),
        (("metadata", "citation"), "text", StreamDefect.OUT_OF_ORDER),
        (("metadata", "usage"), "citation", StreamDefect.OUT_OF_ORDER),
        (("metadata", "usage"), "usage", StreamDefect.OUT_OF_ORDER),
        (("metadata", "error"), "text", StreamDefect.AFTER_TERMINAL),
        (("metadata", "error"), "error", StreamDefect.AFTER_TERMINAL),
    ],
)
def test_the_writer_refuses_events_out_of_order(
    before: tuple[str, ...], attempt: str, defect: StreamDefect
) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    calls = {
        "metadata": writer.metadata,
        "text": lambda: writer.text_delta("24 days"),
        "citation": lambda: writer.citation(CITATIONS[0]),
        "usage": lambda: writer.usage(USAGE),
        "error": lambda: writer.error(FailureKind.INTERNAL),
    }
    for name in before:
        calls[name]()

    with pytest.raises(InvalidEventStream) as caught:
        calls[attempt]()

    assert caught.value.defect is defect


@pytest.mark.parametrize("missing", ["text", "citation", "usage"])
def test_an_answer_needs_text_a_citation_and_its_usage(missing: str) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [writer.metadata()]
    if missing != "text":
        stream.append(writer.text_delta("24 days"))
    if missing != "citation":
        stream.append(writer.citation(CITATIONS[0]))
    if missing != "usage":
        stream.append(writer.usage(USAGE))

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(ANSWER, Language.ENGLISH)
    # The refused event left no trace: the stream can still end in an error.
    stream.append(writer.error(FailureKind.INTERNAL))

    assert caught.value.defect is StreamDefect.INCOMPLETE_ANSWER
    assert read(*stream).outcome is StreamOutcome.FAILED


def test_whitespace_alone_is_not_an_answer() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta(" \n\t")
    writer.citation(CITATIONS[0])
    writer.usage(USAGE)

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(ANSWER, Language.ENGLISH)

    assert caught.value.defect is StreamDefect.INCOMPLETE_ANSWER


def test_citations_never_precede_an_abstention() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta("24 days")
    writer.citation(CITATIONS[0])

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(UNSUPPORTED, Language.ENGLISH)

    assert caught.value.defect is StreamDefect.OUT_OF_ORDER


def test_the_same_citation_twice_is_refused() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.citation(CITATIONS[0])

    with pytest.raises(InvalidEventStream) as caught:
        writer.citation(CITATIONS[0])

    assert caught.value.defect is StreamDefect.DUPLICATE_CITATION


def test_the_text_is_bounded_by_the_answer_limit() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta("a" * MAX_ANSWER_CHARACTERS)

    with pytest.raises(InvalidEventStream) as caught:
        writer.text_delta("a")

    assert caught.value.defect is StreamDefect.TOO_LARGE


# Each takes the writer as Any, so it can pass what the types forbid.
BAD_VALUES: dict[str, Callable[[Any], object]] = {
    "empty text": lambda w: w.text_delta(""),
    "number as text": lambda w: w.text_delta(7),
    "lone surrogate": lambda w: w.text_delta("\ud800"),
    "ID as citation": lambda w: w.citation("E00000001"),
    "negative tokens": lambda w: w.usage(Usage.model_construct(input_tokens=-1)),
    "bool tokens": lambda w: w.usage(Usage.model_construct(input_tokens=True)),
    "language as text": lambda w: w.completion(ANSWER, "en"),
    "abstain without reason": lambda w: w.completion(
        AbstentionDecision(Decision.ABSTAIN, None, POLICY_VERSION), Language.ENGLISH
    ),
    "reason as text": lambda w: w.completion(
        AbstentionDecision(Decision.ABSTAIN, cast(Any, "unsupported"), POLICY_VERSION),
        Language.ENGLISH,
    ),
    "kind as text": lambda w: w.error("internal"),
    "Document ID as text": lambda w: w.citation(
        replace(CITATIONS[0], document_id=cast(Any, "doc"))
    ),
    "empty Citation": lambda w: w.citation(object.__new__(Citation)),
    "empty decision": lambda w: w.completion(
        object.__new__(AbstentionDecision), Language.ENGLISH
    ),
    "tokens past 2**53": lambda w: w.usage(
        USAGE.model_copy(update={"input_tokens": MAX_TOKENS + 1})
    ),
    "tokens past JSON's digit limit": lambda w: w.usage(
        USAGE.model_copy(update={"input_tokens": 10**5000})
    ),
}


@pytest.mark.parametrize("name", BAD_VALUES)
def test_the_writer_refuses_bad_values_with_a_typed_error(name: str) -> None:
    call = BAD_VALUES[name]
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()

    with pytest.raises(InvalidEventStream) as caught:
        call(writer)

    assert caught.value.defect is StreamDefect.MALFORMED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# --- The reader refuses broken streams ----------------------------------------


def test_events_in_the_wrong_order_are_refused() -> None:
    parts = blocks(answer_stream())
    # Usage moved before the Citations.
    parts.insert(len(DELTAS) + 1, parts.pop(-2))

    assert refused(*renumbered(parts)) is StreamDefect.OUT_OF_ORDER


def test_text_before_metadata_is_refused() -> None:
    parts = blocks(answer_stream())
    parts[0], parts[1] = parts[1], parts[0]

    assert refused(*renumbered(parts)) is StreamDefect.OUT_OF_ORDER


def test_a_lost_or_repeated_event_is_refused() -> None:
    stream = answer_stream()

    assert refused(*(stream[:2] + stream[3:])) is StreamDefect.GAP
    assert refused(*(stream[:2] + stream[1:])) is StreamDefect.GAP


def test_a_stream_without_a_terminal_event_is_cut_off() -> None:
    stream = answer_stream()

    assert refused(*stream[:-1]) is StreamDefect.TRUNCATED
    assert refused(*stream[:-1], stream[-1][:-1]) is StreamDefect.TRUNCATED
    assert refused(b"".join(stream)[:-3]) is StreamDefect.TRUNCATED
    assert refused() is StreamDefect.TRUNCATED


def test_nothing_may_follow_the_terminal_event() -> None:
    stream = answer_stream()

    assert refused(*stream, b": late\n") is StreamDefect.AFTER_TERMINAL
    assert refused(*stream, b"\n") is StreamDefect.AFTER_TERMINAL
    assert refused(*stream, b"id") is StreamDefect.AFTER_TERMINAL


def test_a_forged_abstention_message_is_refused() -> None:
    forged = event(
        1,
        "completion",
        {
            "decision": "abstain",
            "reason": "no_evidence",
            "message": f"The document {MARKER} exists but is hidden.",
            "policy_version": POLICY_VERSION,
        },
    )

    assert refused(answer_stream()[0], forged) is StreamDefect.MALFORMED


def test_an_unknown_or_forged_error_is_refused() -> None:
    first = answer_stream()[0]
    unknown = {"code": "teapot", "message": "x", "retry": "none"}
    forged = {"code": "internal_error", "message": MARKER, "retry": "none"}

    assert refused(first, event(1, "error", unknown)) is StreamDefect.UNKNOWN_ERROR
    assert refused(first, event(1, "error", forged)) is StreamDefect.MALFORMED


@pytest.mark.parametrize(
    "raw",
    [
        b"id: 0\nevent: metadata\n\n",
        b"id: 0\nevent: shout\ndata: {}\n\n",
        b"id: 00\nevent: metadata\ndata: {}\n\n",
        b"event: metadata\nid: 0\ndata: {}\n\n",
        b"id: 0\nevent: metadata\ndata: []\n\n",
        b"id: 0\nevent: metadata\ndata: {}\nretry: 5\n\n",
        b'id: 0\nevent: metadata\ndata: {"version":"2",'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","request_id":"x"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1",'
        b'"request_id":"{00000000-0000-0000-0000-000000000049}"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","version":"1",'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","extra":1,'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b"\n",
        b"id: 0\r\nevent: metadata\r\n",
        b"id: 0\nevent: metadata\ndata: {\xff}\n\n",
        b"\xef\xbb\xbf" + b"".join(answer_stream()),
    ],
)
def test_a_malformed_event_is_refused(raw: bytes) -> None:
    assert refused(raw) is StreamDefect.MALFORMED


@pytest.mark.parametrize(
    "data",
    [
        b'data: {"text": "ok"}',
        b'data:  {"text":"ok"}',
        b'data: {"text":"ok"} ',
        b'data: {"\\u0074ext":"ok"}',
        b'data: {"text":"\\u006fk"}',
        b'data: {"text":"no","text":"ok"}',
    ],
)
def test_only_the_writers_own_spelling_is_accepted(data: bytes) -> None:
    first = answer_stream()[0]

    assert refused(first, b"id: 1\nevent: text_delta\n" + data + b"\n\n") is (
        StreamDefect.MALFORMED
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_id", MARKER),
        ("document_id", MARKER),
        ("document_id", "{00000000-0000-0000-0000-000000002001}"),
    ],
)
def test_a_citation_must_name_its_evidence_and_document_as_the_server_does(
    field: str, value: str
) -> None:
    stream = answer_stream()
    parts = blocks(stream)
    name, citation = parts[len(DELTAS) + 1]
    parts[len(DELTAS) + 1] = (name, {**citation, field: value})

    assert refused(*renumbered(parts)) is StreamDefect.MALFORMED


def test_token_counts_past_2_to_the_53_are_refused() -> None:
    first = answer_stream()[0]
    usage = {"input_tokens": MAX_TOKENS + 1, "output_tokens": 0, "total_tokens": 0}

    assert refused(first, event(1, "usage", usage)) is StreamDefect.MALFORMED


def test_wrongly_typed_usage_is_refused() -> None:
    first = answer_stream()[0]
    usage = {"input_tokens": "300", "output_tokens": 30, "total_tokens": 330}

    assert refused(first, event(1, "usage", usage)) is StreamDefect.MALFORMED


def test_a_stream_over_the_limits_is_refused() -> None:
    first = answer_stream()[0]
    endless = b"data: " + b"a" * MAX_EVENT_CHARACTERS
    too_long = [event(n, "text_delta", {"text": "a" * 4_000}) for n in range(1, 6)]

    assert refused(first, endless) is StreamDefect.TOO_LARGE
    assert refused(first, *too_long) is StreamDefect.TOO_LARGE


def test_an_event_over_the_limit_is_refused_even_when_each_line_fits() -> None:
    stream = answer_stream()
    name, citation = blocks(stream)[len(DELTAS) + 1]
    data_line = event(0, name, citation).split(b"\n")[2]
    # A title that makes the data line 10 characters short of the limit.
    title = "T" * (MAX_EVENT_CHARACTERS - 10 - len(data_line) + len("Policy 1"))
    block = event(len(DELTAS) + 1, name, {**citation, "title": title})

    assert all(len(line) < MAX_EVENT_CHARACTERS for line in block.split(b"\n"))
    assert len(block) > MAX_EVENT_CHARACTERS
    assert refused(*stream[: len(DELTAS) + 1], block) is StreamDefect.TOO_LARGE


def test_the_defect_does_not_depend_on_how_the_bytes_are_split() -> None:
    stream = answer_stream()
    # The usage repeats an ID, and then a line runs past the limit.
    broken = b"".join(stream[:-1]) + stream[-2] + b"x" * (MAX_EVENT_CHARACTERS + 1)

    assert refused(broken) is refused(broken[:-10], broken[-10:]) is StreamDefect.GAP


def test_a_long_line_sent_byte_by_byte_is_read_in_linear_time() -> None:
    reader = AnswerEventReader()
    reader.feed(answer_stream()[0])
    started = time.perf_counter()

    for _ in range(MAX_EVENT_CHARACTERS):
        reader.feed(b"a")
    with pytest.raises(InvalidEventStream) as caught:
        reader.feed(b"a")

    assert caught.value.defect is StreamDefect.TOO_LARGE
    assert time.perf_counter() - started < 2


def test_a_broken_reader_stays_broken() -> None:
    reader = AnswerEventReader()
    with pytest.raises(InvalidEventStream):
        reader.feed(b"\n")

    with pytest.raises(InvalidEventStream) as again:
        reader.feed(answer_stream()[0])
    with pytest.raises(InvalidEventStream) as closing:
        reader.close()

    assert again.value.defect is StreamDefect.MALFORMED
    assert closing.value.defect is StreamDefect.MALFORMED


def test_the_reader_refuses_a_chunk_that_is_not_bytes() -> None:
    reader = AnswerEventReader()

    with pytest.raises(InvalidEventStream) as caught:
        reader.feed("id: 0\n")  # type: ignore[arg-type]

    assert caught.value.defect is StreamDefect.MALFORMED


# --- Nothing of the stream leaks -----------------------------------------------


def test_errors_and_reprs_never_quote_the_stream() -> None:
    first = answer_stream()[0]
    bad = event(1, "text_delta", {"text": MARKER, "extra": MARKER})
    with pytest.raises(InvalidEventStream) as caught:
        read(first, bad)
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [writer.metadata(), writer.text_delta(MARKER)]
    stream += [writer.citation(CITATIONS[0]), writer.usage(USAGE)]
    stream.append(writer.completion(ANSWER, Language.ENGLISH))
    reader = AnswerEventReader()
    events = [e for chunk in stream for e in reader.feed(chunk)]
    result = reader.close()

    assert MARKER not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert MARKER not in repr(events)
    assert MARKER not in repr(result)


# --- Whatever the writer sends, the reader accepts ----------------------------

_STEPS = st.sampled_from(
    [
        *("metadata", "text", "space", "citation", "citation2"),
        *("usage", "answer", "abstain", "error"),
    ]
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_STEPS, max_size=10))
def test_whatever_the_writer_accepts_the_reader_accepts(steps: list[str]) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    calls = {
        "metadata": writer.metadata,
        "text": lambda: writer.text_delta(MARKER),
        "space": lambda: writer.text_delta(" "),
        "citation": lambda: writer.citation(CITATIONS[0]),
        "citation2": lambda: writer.citation(CITATIONS[1]),
        "usage": lambda: writer.usage(USAGE),
        "answer": lambda: writer.completion(ANSWER, Language.ENGLISH),
        "abstain": lambda: writer.completion(UNSUPPORTED, Language.ENGLISH),
        "error": lambda: writer.error(FailureKind.CAPACITY),
    }
    sent: list[bytes] = []
    for step in steps:
        try:
            sent.append(calls[step]())
        except InvalidEventStream:
            continue
    reader = AnswerEventReader()
    read_back = [e for chunk in sent for e in reader.feed(chunk)]

    assert [e.event_id for e in read_back] == list(range(len(sent)))
    if sent and read_back[-1].name in (EventName.COMPLETION, EventName.ERROR):
        result = reader.close()
        assert (result.text == "") is (result.outcome is not StreamOutcome.ANSWERED)
    else:
        with pytest.raises(InvalidEventStream):
            reader.close()


@settings(max_examples=300, deadline=None)
@given(
    st.integers(min_value=0, max_value=10_000),
    st.integers(min_value=0, max_value=255),
)
def test_a_corrupted_byte_gives_a_typed_refusal_or_a_valid_answer(
    position: int, value: int
) -> None:
    whole = bytearray(b"".join(answer_stream()))
    whole[position % len(whole)] = value

    reader = AnswerEventReader()
    try:
        reader.feed(bytes(whole))
        result = reader.close()
    except InvalidEventStream:
        result = None

    # Any refusal is typed; anything accepted is still a whole Answer.
    assert result is None or result.outcome is StreamOutcome.ANSWERED
    assert result is None or len(result.citations) == len(CITATIONS)
