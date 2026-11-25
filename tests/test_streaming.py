"""Streaming an Answer: deltas, the decision, the deadline, and cancellation."""

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.types import Message

from evidence_fakes import MARKER, candidate
from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
)
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
)
from knowledge_service.answering.openai_answering import AnswerSettings
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    AuditTrail,
    CitationCheck,
    FinishedAnswer,
    FoundEvidence,
    PolicyFinisher,
    _Span,  # pyright: ignore[reportPrivateUsage]
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.audit import AuditEvent
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.identifiers import ChunkId, DocumentId
from knowledge_service.model import ChatModelError, ChatModelRateLimited
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000050")
QUESTION = Question(text="How many days of annual leave do I get?")
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
)
CITED = EVIDENCE.items[0].evidence_id.value
POLICY = AbstentionPolicy(reranker=None, min_top_score=None, min_supported_share=1.0)
SIGNALS = EvidenceSignals(eligible=1, top_score=None, reranker=None)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
ANSWER = "Every employee receives 24 days of leave."


def reply_json(answer: str = ANSWER, **fields: Any) -> str:
    body = {"answer": answer, "citations": [CITED], "abstained": False, "reason": None}
    body.update(fields)
    return json.dumps(body)


def pieces(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class Source:
    """Return fixed Evidence, or fail, or wait."""

    def __init__(
        self,
        found: object = None,
        *,
        error: BaseException | None = None,
        wait: bool = False,
    ) -> None:
        self.found = FoundEvidence(EVIDENCE, SIGNALS) if found is None else found
        self.error = error
        self.wait = wait
        self.cancelled = False
        self.readers: list[AuthorizationContext] = []

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        self.readers.append(reader)
        if self.wait:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error
        return self.found  # type: ignore[return-value]


class Model:
    """Stream `parts`, then a ModelReply of `final`; record how it ended.

    `hold` waits before that part until `release` is set; `error` is
    raised instead of the reply.
    """

    identity = ModelIdentity(provider="fake", model="fake-1")

    def __init__(
        self,
        parts: list[str] | None = None,
        *,
        final: str | None = None,
        hold: int | None = None,
        error: ChatModelError | None = None,
        close_delay: float = 0,
        usage: Usage = USAGE,
    ) -> None:
        self.final = reply_json() if final is None else final
        self.parts = pieces(self.final) if parts is None else parts
        self.hold = hold
        self.error = error
        self.close_delay = close_delay
        self.usage = usage
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()
        self.calls = 0
        self.closed = False
        self.finished = False

    async def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.calls += 1
        try:
            for index, part in enumerate(self.parts):
                if index == self.hold:
                    self.waiting.set()
                    await self.release.wait()
                yield part
            if self.error is not None:
                raise self.error
            yield ModelReply(text=self.final, refused=False, usage=self.usage)
            self.finished = True
        finally:
            # A provider's close awaits too, such as closing its connection.
            if self.close_delay:
                await asyncio.sleep(self.close_delay)
            self.closed = True


class Citations:
    """A `CitationCheck` that sees all Evidence and keeps every Answer.

    `visible` is what `still_visible` answers, as a revoked grant or a
    retrieval bug would make it; `withdraw` withdraws an Answer; `rename`
    gives every Citation a new title, as a renamed Document would; `alter`
    answers with whatever it makes of the result; `error` fails `confirm`
    and `delay` slows it.
    """

    def __init__(
        self,
        *,
        visible: object = True,
        withdraw: bool = False,
        rename: str | None = None,
        alter: Callable[[AnswerResult], object] | None = None,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.visible = visible
        self.withdraw = withdraw
        self.rename = rename
        self.alter = alter
        self.error = error
        self.delay = delay
        self.checked: list[AuthorizationContext] = []
        self.calls: list[tuple[AnswerOutcome, AuthorizationContext]] = []

    async def still_visible(
        self, evidence: Any, *, reader: AuthorizationContext, request_id: UUID
    ) -> bool:
        self.checked.append(reader)
        return self.visible  # type: ignore[return-value]

    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult:
        self.calls.append((result.outcome, reader))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.alter is not None:
            return self.alter(result)  # type: ignore[return-value]
        if result.outcome is not AnswerOutcome.ANSWERED:
            return result
        if self.withdraw:
            return dataclasses.replace(
                result, outcome=AnswerOutcome.WITHDRAWN, text="", citations=()
            )
        if self.rename is not None:
            renamed = tuple(
                dataclasses.replace(citation, title=self.rename)
                for citation in result.citations
            )
            return dataclasses.replace(result, citations=renamed)
        return result


class Trail:
    """An `AuditTrail` that keeps every event it is given.

    `error` fails every write, and `delay` slows it, as a database that is
    down or stuck would; `started` is set when a write begins.
    """

    def __init__(self, *, error: Exception | None = None, delay: float = 0) -> None:
        self.events: list[AuditEvent] = []
        self.error = error
        self.delay = delay
        self.started = asyncio.Event()

    async def record(self, event: AuditEvent) -> None:
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.events.append(event)


class SlowFinisher:
    """Never decides."""

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        await asyncio.Event().wait()
        raise AssertionError


def streamer(
    model: Model,
    *,
    source: Source | None = None,
    finisher: AnswerFinisher | None = None,
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
    citations: CitationCheck | None = None,
    audit: AuditTrail | None = None,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
        model,
        finisher or PolicyFinisher(LexicalSupportJudge(), policy, timeout_seconds=5),
        policy,
        AnswerSettings().budget,
        citations=citations or Citations(),
        audit=audit or Trail(),
        timeout_seconds=timeout_seconds,
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
) -> list[bytes]:
    events = await answering.open(question, reader=READER, request_id=REQUEST_ID)
    return [chunk async for chunk in events]


def rebuilt(sent: list[bytes]) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    return reader.close()


def names(sent: list[bytes]) -> list[str]:
    return [chunk.split(b"\n")[1].decode().removeprefix("event: ") for chunk in sent]


# --- A complete stream ---------------------------------------------------------


async def test_a_client_rebuilds_the_checked_answer_from_the_stream() -> None:
    sent = await chunks(streamer(Model()))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ANSWERED
    assert result.text == ANSWER
    assert [c.evidence_id for c in result.citations] == [CITED]
    assert result.usage is not None
    assert result.usage.total_tokens == 330
    assert names(sent)[:2] == ["metadata", "text_delta"]
    assert names(sent)[-3:] == ["citation", "usage", "completion"]


async def test_text_reaches_the_client_before_the_model_has_finished() -> None:
    model = Model(hold=4)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)

    first = [await anext(events) for _ in range(4)]
    assert names(first) == ["metadata", "text_delta", "text_delta", "text_delta"]
    assert not model.finished
    model.release.set()
    rest = [chunk async for chunk in events]

    assert rebuilt(first + rest).text == ANSWER


async def test_an_unsupported_answer_is_withdrawn_and_sends_no_citation() -> None:
    sent = await chunks(streamer(Model(final=reply_json("You get 99 days and a car."))))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.UNSUPPORTED
    assert result.text == ""
    assert "citation" not in names(sent)
    assert result.usage is not None


async def test_the_models_own_abstention_abstains() -> None:
    final = reply_json("", citations=[], abstained=True, reason="Not in the documents.")

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.reason is AbstentionReason.INSUFFICIENT_EVIDENCE


async def test_no_evidence_abstains_without_asking_the_model() -> None:
    model = Model()
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))

    sent = await chunks(streamer(model, source=Source(empty)))

    assert model.calls == 0
    assert names(sent) == ["metadata", "completion"]
    assert rebuilt(sent).reason is AbstentionReason.NO_EVIDENCE


async def test_weak_evidence_abstains_without_asking_the_model() -> None:
    reranker = RerankerIdentity(provider="local", model="bge-reranker-v2-m3")
    policy = AbstentionPolicy(reranker, min_top_score=0.5, min_supported_share=1.0)
    weak = FoundEvidence(EVIDENCE, EvidenceSignals(1, 0.1, reranker))
    model = Model()

    sent = await chunks(streamer(model, source=Source(weak), policy=policy))

    assert model.calls == 0
    assert rebuilt(sent).reason is AbstentionReason.WEAK_EVIDENCE


async def test_a_ukrainian_question_gets_a_ukrainian_abstention() -> None:
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))
    question = Question(text="Скільки днів відпустки я маю?")

    sent = await chunks(streamer(Model(), source=Source(empty)), question)

    assert rebuilt(sent).message == (
        "Я не знайшов документів, які відповідають на це запитання."
    )


# --- Citations as they are now ------------------------------------------------


async def test_the_citations_are_checked_as_the_reader_before_the_decision() -> None:
    citations = Citations()

    result = rebuilt(await chunks(streamer(Model(), citations=citations)))

    assert citations.checked == [READER]
    assert citations.calls == [(AnswerOutcome.ANSWERED, READER)]
    assert result.outcome is StreamOutcome.ANSWERED


async def test_evidence_the_reader_may_not_see_never_reaches_the_model() -> None:
    model = Model()

    sent = await chunks(streamer(model, citations=Citations(visible=False)))

    assert model.calls == 0
    assert names(sent) == ["metadata", "completion"]
    assert rebuilt(sent).reason is AbstentionReason.NO_EVIDENCE


@pytest.mark.parametrize("visible", [None, 1, "yes"])
async def test_a_visibility_check_that_does_not_say_yes_or_no_fails_first(
    visible: object,
) -> None:
    model = Model()

    with pytest.raises(InvalidAnswerRequest):
        await streamer(model, citations=Citations(visible=visible)).open(
            QUESTION, reader=READER, request_id=REQUEST_ID
        )

    assert model.calls == 0


async def test_a_withdrawn_answer_abstains_like_an_empty_search() -> None:
    sent = await chunks(streamer(Model(), citations=Citations(withdraw=True)))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.NO_EVIDENCE
    assert result.text == ""
    assert "citation" not in names(sent)
    assert result.usage is not None


async def test_citations_carry_the_provenance_the_check_read() -> None:
    sent = await chunks(streamer(Model(), citations=Citations(rename="Leave, 2027")))

    (citation,) = rebuilt(sent).citations
    assert citation.title == "Leave, 2027"


def not_a_result(result: AnswerResult) -> object:
    return "not a result"


def other_text(result: AnswerResult) -> object:
    return dataclasses.replace(result, text="You get 99 days.")


def no_citation(result: AnswerResult) -> object:
    return dataclasses.replace(result, citations=())


def extra_citation(result: AnswerResult) -> object:
    return dataclasses.replace(result, citations=result.citations * 2)


def half_withdrawn(result: AnswerResult) -> object:
    # Withdrawn, but still holding the text and the Citations.
    return dataclasses.replace(result, outcome=AnswerOutcome.WITHDRAWN)


def withdrawn_keeping_citations(result: AnswerResult) -> object:
    return dataclasses.replace(result, outcome=AnswerOutcome.WITHDRAWN, text="")


def withdrawn_keeping_text(result: AnswerResult) -> object:
    return dataclasses.replace(result, outcome=AnswerOutcome.WITHDRAWN, citations=())


def abstained_instead(result: AnswerResult) -> object:
    return dataclasses.replace(result, outcome=AnswerOutcome.ABSTAINED)


def subclassed(result: AnswerResult) -> object:
    subclass = type("Subclassed", (AnswerResult,), {"__slots__": ()})
    fields = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
    return subclass(**fields)


def another_document(result: AnswerResult) -> object:
    (citation,) = result.citations
    moved = dataclasses.replace(citation, document_id=DocumentId(UUID(int=0xD0C)))
    return dataclasses.replace(result, citations=(moved,))


def another_chunk(result: AnswerResult) -> object:
    (citation,) = result.citations
    moved = dataclasses.replace(citation, chunk_id=ChunkId(UUID(int=0xC)))
    return dataclasses.replace(result, citations=(moved,))


def citations_as_list(result: AnswerResult) -> object:
    return dataclasses.replace(result, citations=list(result.citations))


def a_copy(result: AnswerResult) -> object:
    return dataclasses.replace(result)


@pytest.mark.parametrize(
    "alter",
    [
        not_a_result,
        other_text,
        no_citation,
        extra_citation,
        half_withdrawn,
        withdrawn_keeping_citations,
        withdrawn_keeping_text,
        abstained_instead,
        subclassed,
        another_document,
        another_chunk,
        citations_as_list,
    ],
)
async def test_a_check_that_answers_another_result_ends_with_an_error(
    alter: Callable[[AnswerResult], object],
) -> None:
    sent = await chunks(streamer(Model(), citations=Citations(alter=alter)))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.FAILED
    assert "citation" not in names(sent)


async def test_a_check_that_answers_an_abstention_with_a_copy_fails() -> None:
    final = reply_json("", citations=[], abstained=True, reason="Not in the documents.")
    copying = Citations(alter=a_copy)

    result = rebuilt(await chunks(streamer(Model(final=final), citations=copying)))

    assert result.outcome is StreamOutcome.FAILED


async def test_a_check_slower_than_the_deadline_ends_with_an_error() -> None:
    slow = Citations(delay=1.0)

    sent = await chunks(streamer(Model(), citations=slow, timeout_seconds=0.3))

    assert rebuilt(sent).outcome is StreamOutcome.FAILED
    assert "citation" not in names(sent)


async def test_a_failing_check_ends_with_an_error_and_shows_no_citation() -> None:
    sent = await chunks(
        streamer(Model(), citations=Citations(error=RuntimeError(MARKER)))
    )
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.FAILED
    assert "citation" not in names(sent)
    assert MARKER.encode() not in b"".join(sent)


# --- The streamed text is the checked text -------------------------------------


async def test_a_reply_in_another_key_order_sends_the_text_at_the_end() -> None:
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )

    sent = await chunks(streamer(Model(final=final)))

    assert names(sent) == [
        "metadata",
        "checking",
        "text_delta",
        "citation",
        "usage",
        "completion",
    ]
    assert rebuilt(sent).text == ANSWER


async def test_streamed_text_that_is_not_the_checked_answer_abstains() -> None:
    model = Model(parts=pieces(reply_json("You get 99 days.")), final=reply_json())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


@settings(max_examples=200, deadline=None)
@given(
    st.text(min_size=1, max_size=60),
    st.booleans(),
    st.lists(st.integers(min_value=1, max_value=9), min_size=1, max_size=40),
)
def test_the_answer_field_is_decoded_however_the_json_is_cut(
    answer: str, ascii_only: bool, sizes: list[int]
) -> None:
    raw = json.dumps({"answer": answer, "citations": []}, ensure_ascii=ascii_only)
    field = AnswerFieldReader()
    out: list[str] = []
    position = 0
    for size in sizes * (len(raw) // len(sizes) + 1):
        if position >= len(raw):
            break
        out.append(field.feed(raw[position : position + size]))
        position += size

    decoded = "".join(out)
    assert answer.startswith(decoded)
    # Only a lone surrogate, which no valid Answer holds, stops it early.
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in answer):
        assert decoded == answer


# --- Failures after the first byte ---------------------------------------------


async def test_the_deadline_ends_the_stream_with_an_error_and_closes_the_model() -> (
    None
):
    model = Model(hold=3)

    sent = await chunks(streamer(model, timeout_seconds=0.2))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "dependency_unavailable"
    assert result.text == ""
    assert model.closed
    assert not model.finished


async def test_a_slow_policy_check_ends_in_an_error_after_the_usage() -> None:
    sent = await chunks(streamer(Model(), finisher=SlowFinisher(), timeout_seconds=0.2))

    assert names(sent)[-2:] == ["usage", "error"]
    assert rebuilt(sent).usage is not None


async def test_a_provider_failure_mid_stream_is_a_typed_error_event() -> None:
    model = Model(error=ChatModelRateLimited())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "capacity_exceeded"


async def test_a_reply_past_the_limit_abstains_and_keeps_its_usage() -> None:
    final = reply_json("x" * 50_000)
    model = Model(parts=pieces(final, 10_000), final=final)

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED
    assert result.usage is not None


async def test_raw_half_a_character_abstains_instead_of_failing() -> None:
    lone = chr(0xD83D)
    final = '{"answer":"Leave' + lone + '","citations":[],"abstained":false}'

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


class Deciding:
    """Return a fixed decision about whatever result it gets."""

    def __init__(self, decision: AbstentionDecision) -> None:
        self.decision = decision

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        return FinishedAnswer(result, self.decision)


@pytest.mark.parametrize(
    "decision",
    [
        AbstentionDecision(Decision.ABSTAIN, None, POLICY_VERSION),
        AbstentionDecision(
            Decision.ANSWER, AbstentionReason.ANSWER_FAILED, POLICY_VERSION
        ),
        AbstentionDecision(Decision.ANSWER, None, ""),
    ],
)
async def test_an_event_the_writer_refuses_still_ends_in_an_error(
    decision: AbstentionDecision,
) -> None:
    sent = await chunks(streamer(Model(), finisher=Deciding(decision)))

    assert names(sent)[-1] == "error"
    assert rebuilt(sent).outcome is StreamOutcome.FAILED


async def test_usage_a_client_cannot_read_ends_in_an_error() -> None:
    huge = USAGE.model_copy(update={"input_tokens": 2**53})

    sent = await chunks(streamer(Model(usage=huge)))

    assert names(sent)[-1] == "error"
    assert "usage" not in names(sent)


# --- Failures before the first byte --------------------------------------------


async def test_a_source_failure_is_raised_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(error=AuthorizationDenied()))

    with pytest.raises(AuthorizationDenied):
        await answering.open(QUESTION, reader=READER, request_id=REQUEST_ID)


async def test_a_slow_source_runs_out_of_the_deadline_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(wait=True), timeout_seconds=0.1)

    with pytest.raises(AnswerDeadlineExceeded):
        await answering.open(QUESTION, reader=READER, request_id=REQUEST_ID)


# --- Cancellation ---------------------------------------------------------------


async def test_closing_the_stream_closes_the_model_and_records_a_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await events.aclose()

    assert model.closed
    assert not model.finished
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stream_closed_before_its_first_event_ends_its_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)

    await events.aclose()

    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_model_whose_close_never_ends_cannot_hold_the_stream() -> None:
    model = Model(hold=2, close_delay=60)
    events = await streamer(model).open(QUESTION, reader=READER, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await asyncio.wait_for(events.aclose(), timeout=CLOSE_SECONDS + 1)

    assert not model.closed


async def test_cancelling_the_task_closes_the_model() -> None:
    model = Model(hold=2)

    async def consume() -> None:
        await chunks(streamer(model))

    task = asyncio.create_task(consume())
    await model.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.closed
    assert not model.finished


async def test_the_stream_record_holds_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = f"Every employee receives 24 days of leave. {MARKER}"

    await chunks(streamer(Model(final=reply_json(answer))))

    assert caplog.records
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)


def test_the_stream_record_hides_the_text_it_counts() -> None:
    span = _Span(REQUEST_ID, Trail())  # pyright: ignore[reportPrivateUsage]
    span.sent(MARKER)

    assert MARKER not in repr(span)


# --- Over HTTP -------------------------------------------------------------------


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
        headers=AUTHORIZATION,
    )


async def test_the_endpoint_streams_events_a_client_can_rebuild() -> None:
    app = create_app(authenticator=TRUSTED, streamer=streamer(Model()))
    async with client(app) as http:
        response = await http.post(
            "/v1/questions",
            json={"text": QUESTION.text},
            headers={"X-Request-ID": str(REQUEST_ID)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert rebuilt([response.content]).text == ANSWER


@pytest.mark.parametrize(
    ("answering", "status", "code"),
    [
        (None, 503, "answering_unavailable"),
        ("denied", 403, "authorization_denied"),
        ("slow", 503, "dependency_unavailable"),
    ],
)
async def test_a_failure_before_the_first_byte_is_a_plain_http_error(
    answering: str | None, status: int, code: str
) -> None:
    sources = {
        "denied": Source(error=AuthorizationDenied()),
        "slow": Source(wait=True),
    }
    app = create_app(
        authenticator=TRUSTED,
        streamer=None
        if answering is None
        else streamer(Model(), source=sources[answering], timeout_seconds=0.1),
    )
    async with client(app) as http:
        response = await http.post("/v1/questions", json={"text": "Leave?"})

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_an_empty_question_is_refused_before_any_work() -> None:
    model = Model()
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))
    async with client(app) as http:
        response = await http.post("/v1/questions", json={"text": ""})

    assert response.status_code == 422
    assert model.calls == 0


class Connection:
    """An ASGI client that leaves after it has read `leave_after` bodies.

    With `stall`, it stops reading after the first body, so the server
    waits to write, then leaves.
    """

    def __init__(self, *, leave_after: int, stall: bool = False) -> None:
        self.leave_after = leave_after
        self.stall = stall
        self.bodies: list[bytes] = []
        self.left = asyncio.Event()
        self._asked = False

    async def receive(self) -> Message:
        if not self._asked:
            self._asked = True
            body = json.dumps({"text": QUESTION.text}).encode()
            return {"type": "http.request", "body": body, "more_body": False}
        await self.left.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] != "http.response.body":
            return
        if self.stall and self.bodies:
            self.left.set()
            await asyncio.Event().wait()
        self.bodies.append(message["body"])
        if len(self.bodies) >= self.leave_after:
            self.left.set()


def scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/questions",
        "raw_path": b"/v1/questions",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", AUTHORIZATION["Authorization"].encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_midstream_stops_the_model_and_the_stream(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The model goes quiet after two pieces and would never finish alone.
    model = Model(hold=2)
    connection = Connection(leave_after=2)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
    assert all(b"completion" not in body for body in connection.bodies)
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_model_that_awaits_while_closing_is_closed_when_the_client_leaves(
    spec_version: str,
) -> None:
    model = Model(hold=2, close_delay=0.05)
    connection = Connection(leave_after=2)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_during_the_search_cancels_it(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    model = Model()
    connection = Connection(leave_after=0)
    connection.left.set()
    app = create_app(authenticator=TRUSTED, streamer=streamer(model, source=source))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert source.cancelled
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stuck_close_cannot_hold_the_response_open() -> None:
    model = Model(hold=4, close_delay=60)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send),
        timeout=2 * CLOSE_SECONDS + 1,
    )


async def test_a_client_that_stops_reading_and_leaves_closes_the_model() -> None:
    # The server is stuck writing the second event when the client leaves.
    model = Model(hold=4)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(authenticator=TRUSTED, streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
