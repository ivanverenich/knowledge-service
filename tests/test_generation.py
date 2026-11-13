"""Generating a structured Answer: one checked outcome per reply, or a typed error."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.context_budget import (
    ContextBudget,
    allocate_context,
    count_prompt_tokens,
)
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerDefect,
    AnswerOutcome,
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
    generate_answer,
)
from knowledge_service.answering.prompt import EvidencePrompt, render_prompt
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)

QUESTION = "How many days of annual leave do I get?"
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000042")
BUDGET = ContextBudget(context_tokens=32_000, answer_tokens=1_000, history_tokens=0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=900,
    output_tokens=40,
    total_tokens=940,
)
EVIDENCE = build_evidence(
    [
        candidate(1, text="Every employee receives 24 days of annual leave."),
        candidate(2, text="Unused leave carries over for 5 days."),
    ]
)
FIRST, SECOND = (item.evidence_id.value for item in EVIDENCE.items)


def body(**fields: Any) -> str:
    """A reply in the schema; any field can be replaced."""
    values: dict[str, Any] = {
        "answer": "You get 24 days of annual leave.",
        "citations": [FIRST],
        "abstained": False,
        "reason": None,
    }
    values.update(fields)
    return json.dumps(values)


class FakeAnswerModel:
    """Returns one reply, raises one error, or never answers; records prompts."""

    def __init__(
        self,
        text: object = None,
        *,
        refused: bool = False,
        reply: object = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._text = text
        self._refused = refused
        self._reply = reply
        self._error = error
        self._block = block
        self.prompts: list[EvidencePrompt] = []

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(provider="fake", model="fake-1")

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        self.prompts.append(prompt)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._reply is not None:
            return self._reply  # type: ignore[return-value]
        return ModelReply(text=self._text, refused=self._refused, usage=USAGE)


async def generate(
    model: FakeAnswerModel,
    evidence: EvidenceSet = EVIDENCE,
    *,
    question: str = QUESTION,
    budget: ContextBudget = BUDGET,
    timeout_seconds: float = 5.0,
) -> Any:
    return await generate_answer(
        question,
        evidence,
        budget,
        model,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_a_supported_answer_cites_evidence_with_server_provenance() -> None:
    model = FakeAnswerModel(body(citations=[SECOND, FIRST]))

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.text == "You get 24 days of annual leave."
    assert result.reason is None
    assert [c.evidence_id.value for c in result.citations] == [SECOND, FIRST]
    first = EVIDENCE.items[0]
    assert result.citations[1].chunk_id == first.chunk_id
    assert result.citations[1].title == first.title
    assert result.usage == USAGE
    assert result.defect is None
    # The model got exactly the prompt the budget counted.
    (sent,) = model.prompts
    assert sent == allocate_context(QUESTION, EVIDENCE, BUDGET).prompt
    assert result.context.kept == tuple(item.evidence_id for item in EVIDENCE.items)


async def test_an_abstention_gives_its_reason_and_no_citations() -> None:
    model = FakeAnswerModel(
        body(
            answer="I cannot answer that from the documents.",
            citations=[],
            abstained=True,
            reason="The evidence does not mention parental leave.",
        )
    )

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ABSTAINED
    assert result.reason == "The evidence does not mention parental leave."
    assert result.citations == ()
    assert result.usage == USAGE


async def test_a_refusal_keeps_its_usage_and_has_no_text() -> None:
    result = await generate(FakeAnswerModel(None, refused=True))

    assert result.outcome is AnswerOutcome.REFUSED
    assert (result.text, result.reason, result.citations) == ("", None, ())
    assert result.usage == USAGE


@pytest.mark.parametrize(
    ("text", "defect"),
    [
        (None, AnswerDefect.UNREADABLE),
        (5, AnswerDefect.UNREADABLE),
        ("not json", AnswerDefect.UNREADABLE),
        ("[]", AnswerDefect.UNREADABLE),
        (json.dumps({"answer": "x", "citations": []}), AnswerDefect.UNREADABLE),
        (body(extra=1), AnswerDefect.UNREADABLE),
        (
            '{"answer": "a", "answer": "b", "citations": [], "abstained": true,'
            ' "reason": "r"}',
            AnswerDefect.UNREADABLE,
        ),
        (body(abstained="false"), AnswerDefect.UNREADABLE),
        (body(citations=FIRST), AnswerDefect.UNREADABLE),
        (body(reason=5), AnswerDefect.UNREADABLE),
        ("[" * 100_000 + "]" * 100_000, AnswerDefect.TOO_LONG),
        ("[" * 10_000 + "]" * 10_000, AnswerDefect.UNREADABLE),
        (body(answer="x" * 16_001), AnswerDefect.TOO_LONG),
        (body(answer="Leave is \ud800 days."), AnswerDefect.INVALID_TEXT),
        (body(answer="   "), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u200b\u200b"), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u0301\u0301"), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u202e\x00"), AnswerDefect.EMPTY_ANSWER),
        (body(citations=[]), AnswerDefect.UNCITED),
        (body(citations=["E00000099"]), AnswerDefect.INVALID_CITATION),
        (body(citations=[FIRST, FIRST]), AnswerDefect.INVALID_CITATION),
        (body(citations=["cite E1"]), AnswerDefect.INVALID_CITATION),
        (
            body(abstained=True, citations=[FIRST], reason="Not enough."),
            AnswerDefect.CITED_ABSTENTION,
        ),
        (body(abstained=True, citations=[], reason=None), AnswerDefect.NO_REASON),
        (body(abstained=True, citations=[], reason=" "), AnswerDefect.NO_REASON),
        (
            body(abstained=True, citations=[], reason="\u2800\u3164"),
            AnswerDefect.NO_REASON,
        ),
    ],
    ids=[
        "cut-off",
        "not-text",
        "not-json",
        "not-an-object",
        "missing-fields",
        "extra-field",
        "repeated-field",
        "abstained-not-bool",
        "citations-not-list",
        "reason-not-text",
        "too-long",
        "deep",
        "answer-too-long",
        "lone-surrogate",
        "blank-answer",
        "invisible-answer",
        "combining-only-answer",
        "control-only-answer",
        "uncited",
        "unknown-id",
        "repeated-id",
        "malformed-id",
        "cited-abstention",
        "no-reason",
        "blank-reason",
        "invisible-reason",
    ],
)
async def test_a_reply_that_breaks_a_rule_is_invalid_and_keeps_its_usage(
    text: object, defect: AnswerDefect
) -> None:
    result = await generate(FakeAnswerModel(text))

    assert result.outcome is AnswerOutcome.INVALID
    assert result.defect is defect
    assert (result.text, result.reason, result.citations) == ("", None, ())
    assert result.usage == USAGE


async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
    evidence = build_evidence(
        [candidate(1, text="Leave is 24 days."), candidate(2, text="x" * 5_000)]
    )
    kept_only = count_prompt_tokens(
        render_prompt(QUESTION, EvidenceSet(evidence.items[:1]))
    )
    budget = ContextBudget(kept_only + 1_000, 1_000, 0)
    dropped = evidence.items[1].evidence_id.value
    model = FakeAnswerModel(body(citations=[dropped]))

    result = await generate(model, evidence, budget=budget)

    assert result.context.dropped == (evidence.items[1].evidence_id,)
    assert dropped not in model.prompts[0].input
    assert result.outcome is AnswerOutcome.INVALID
    assert result.defect is AnswerDefect.INVALID_CITATION


@pytest.mark.parametrize(
    "error",
    [
        ChatModelTimeout(),
        ChatModelRateLimited(),
        ChatModelUnavailable(),
        ChatModelRequestRefused("the provider refused the request with status 401"),
        ChatModelMalformedResponse("the provider response was not JSON"),
    ],
)
async def test_a_provider_failure_propagates_as_its_port_error(
    error: ChatModelError,
) -> None:
    with pytest.raises(ChatModelError) as raised:
        await generate(FakeAnswerModel(error=error))

    assert raised.value is error


async def test_a_stalled_model_times_out_at_the_deadline() -> None:
    with pytest.raises(ChatModelTimeout) as raised:
        await generate(FakeAnswerModel(block=True), timeout_seconds=0.05)

    assert raised.value.__context__ is None


async def test_a_timeout_raised_by_the_model_itself_is_not_hidden() -> None:
    model = FakeAnswerModel(error=TimeoutError(MARKER))

    with pytest.raises(TimeoutError) as raised:
        await generate(model, timeout_seconds=5)

    assert type(raised.value) is TimeoutError


async def test_a_reply_with_unchecked_usage_is_malformed() -> None:
    usage = Usage.model_construct(
        provider="fake",
        model="fake-1",
        latency_ms=5,
        input_tokens=MARKER,
        output_tokens=40,
        total_tokens=940,
    )
    reply = ModelReply(text=body(), refused=False, usage=usage)

    with pytest.raises(ChatModelMalformedResponse) as raised:
        await generate(FakeAnswerModel(reply=reply))

    assert MARKER not in str(raised.value)


class _Shown:
    def __str__(self) -> str:
        return MARKER


async def test_a_hostile_model_identity_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeAnswerModel(body())
    hostile = ModelIdentity(provider=_Shown(), model=_Shown())  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FakeAnswerModel, "identity", property(lambda self: hostile))
        result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    (record,) = [r for r in caplog.records if r.name.endswith("generation")]
    assert record.__dict__["answer"]["provider"] == "unknown"
    assert MARKER not in caplog.text


async def test_a_reply_of_the_wrong_shape_is_malformed() -> None:
    with pytest.raises(ChatModelMalformedResponse):
        await generate(FakeAnswerModel(reply={"text": body()}))


async def test_a_bug_in_the_model_is_not_hidden() -> None:
    with pytest.raises(RuntimeError):
        await generate(FakeAnswerModel(error=RuntimeError("bug")))


@pytest.mark.parametrize("room", [0, 10], ids=["no-evidence", "none-fits"])
async def test_with_no_evidence_that_fits_the_model_is_not_called(room: int) -> None:
    evidence = EVIDENCE if room else EvidenceSet(())
    base = count_prompt_tokens(render_prompt(QUESTION, EvidenceSet(())))
    model = FakeAnswerModel(body())

    result = await generate(
        model, evidence, budget=ContextBudget(base + room + 1_000, 1_000, 0)
    )

    assert model.prompts == []
    assert result.outcome is AnswerOutcome.NO_EVIDENCE
    assert result.usage is None
    assert len(result.context.dropped) == len(evidence.items)


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1, float("nan"), float("inf"), True, 10**400],
    ids=["zero", "negative", "nan", "inf", "bool", "huge"],
)
async def test_a_bad_timeout_is_refused_before_sending(timeout_seconds: float) -> None:
    model = FakeAnswerModel(body())

    with pytest.raises(InvalidAnswerRequest):
        await generate(model, timeout_seconds=timeout_seconds)

    assert model.prompts == []


async def test_no_question_document_or_answer_text_reaches_logs_or_reprs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    evidence = build_evidence([candidate(1, text=f"{MARKER} leave is 24 days.")])
    first = evidence.items[0].evidence_id.value
    answered = FakeAnswerModel(body(answer=MARKER, citations=[first]))
    invalid = FakeAnswerModel(body(answer=MARKER, citations=["E00000099"]))

    results = [
        await generate(answered, evidence, question=f"{MARKER}?"),
        await generate(invalid, evidence, question=f"{MARKER}?"),
    ]
    with pytest.raises(ChatModelUnavailable):
        await generate(FakeAnswerModel(error=ChatModelUnavailable()), evidence)

    assert [r.outcome for r in results] == [
        AnswerOutcome.ANSWERED,
        AnswerOutcome.INVALID,
    ]
    records = [r for r in caplog.records if r.name.endswith("generation")]
    assert [r.__dict__["answer"]["outcome"] for r in records] == [
        "answered",
        "invalid",
        "ChatModelUnavailable",
    ]
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in records)
    assert all(MARKER not in repr(result) for result in results)


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=12,
)


@given(
    answer=json_values | st.text(max_size=40),
    citations=json_values | st.lists(st.sampled_from([FIRST, SECOND, "E1"])),
    abstained=json_values,
    reason=json_values,
)
@settings(derandomize=True, max_examples=300, deadline=None)
def test_any_reply_becomes_one_outcome_never_an_untyped_crash(
    answer: object, citations: object, abstained: object, reason: object
) -> None:
    text = json.dumps(
        {
            "answer": answer,
            "citations": citations,
            "abstained": abstained,
            "reason": reason,
        }
    )

    result = asyncio.run(generate(FakeAnswerModel(text)))

    assert result.outcome in {
        AnswerOutcome.ANSWERED,
        AnswerOutcome.ABSTAINED,
        AnswerOutcome.INVALID,
    }
    known = {FIRST, SECOND}
    assert all(c.evidence_id.value in known for c in result.citations)
    if result.outcome is AnswerOutcome.ANSWERED:
        assert result.citations
