"""Checking claim support: findings beside the Answer, checked on the server."""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelIdentity,
    ModelReply,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.support import (
    MAX_CLAIMS,
    CheckerIdentity,
    InvalidSupportRequest,
    JudgeFinding,
    JudgeReply,
    LexicalSupportJudge,
    ModelSupportJudge,
    SupportOutcome,
    SupportVerdict,
    check_support,
    judge_prompt,
    split_claims,
)
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelTimeout,
    ChatModelUnavailable,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000047")
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
LEAVE = (
    "Every employee receives 24 days of annual leave. Leave is booked in the portal."
)
CARRY = "Unused annual leave carries over for 5 days."
EVIDENCE = build_evidence(
    [
        candidate(1, text=LEAVE),
        candidate(2, text=CARRY),
        candidate(3, text=f"Salary bands are confidential. {MARKER}"),
    ]
)
FIRST, SECOND, THIRD = (item.evidence_id.value for item in EVIDENCE.items)


def answered(text: str, cited: tuple[str, ...] = (FIRST, SECOND)) -> AnswerResult:
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text=text,
        reason=None,
        citations=resolve_citations(EVIDENCE, list(cited)),
        defect=None,
        usage=USAGE,
        context=allocate_context(
            "Leave?", EVIDENCE, ContextBudget(32_000, 1_000, 0)
        ).report,
    )


async def check(result: AnswerResult, judge: Any = None, **kwargs: Any) -> Any:
    return await check_support(
        result,
        EVIDENCE,
        LexicalSupportJudge() if judge is None else judge,
        request_id=REQUEST_ID,
        timeout_seconds=kwargs.get("timeout_seconds", 5.0),
    )


class ScriptedJudge:
    """Returns whatever reply a test needs, or never answers."""

    identity = CheckerIdentity(name="scripted", version="7")

    def __init__(
        self,
        reply: object = None,
        *,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._reply = reply
        self._error = error
        self._block = block
        self.calls: list[tuple[tuple[str, ...], EvidenceSet]] = []

    async def judge(
        self,
        claims: tuple[str, ...],
        evidence: EvidenceSet,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Any:
        self.calls.append((claims, evidence))
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        return self._reply


def test_claims_are_sentences_and_lines_by_position() -> None:
    text = "Leave is 24 days.  It is 2.5 weeks!\n- Book it early\n\nAsk HR?"

    claims = split_claims(text)

    assert [text[c.start : c.end] for c in claims] == [
        "Leave is 24 days.",
        "It is 2.5 weeks!",
        "- Book it early",
        "Ask HR?",
    ]
    assert [c.index for c in claims] == [0, 1, 2, 3]


@pytest.mark.parametrize("text", ["", "   ", "\u200b\n\u200b", "\n\n"])
def test_text_without_a_visible_character_has_no_claims(text: str) -> None:
    assert split_claims(text) == ()


async def test_a_supported_answer_points_each_claim_at_its_quote() -> None:
    text = "You get 24 days of annual leave. Unused leave carries over for 5 days."
    result = answered(text)

    report = await check(result)

    assert report.outcome is SupportOutcome.CHECKED
    assert report.checker == CheckerIdentity(name="lexical-overlap", version="1")
    assert [f.verdict for f in report.findings] == [SupportVerdict.SUPPORTED] * 2
    first, second = report.findings
    assert first.evidence_id is not None
    assert first.evidence_id.value == FIRST
    assert LEAVE[first.quote_start : first.quote_end] == (
        "Every employee receives 24 days of annual leave."
    )
    assert second.evidence_id is not None
    assert second.evidence_id.value == SECOND
    assert report.unsupported == ()


async def test_a_claim_with_a_wrong_number_is_unsupported() -> None:
    result = answered("You get 26 days of annual leave.")

    report = await check(result)

    (finding,) = report.unsupported
    assert finding.verdict is SupportVerdict.NO_SUPPORT
    assert (finding.evidence_id, finding.quote_start, finding.quote_end) == (
        None,
        None,
        None,
    )


async def test_the_answer_is_never_changed_and_the_report_names_its_text() -> None:
    result = answered("You get 26 days of annual leave. Leave is booked in the portal.")
    before = replace(result)

    report = await check(result)

    assert result == before
    assert len(report.unsupported) == 1
    # The report is bound to the exact text it judged.
    assert report.answer_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    other = await check(replace(result, text=result.text + " "))
    assert other.answer_sha256 != report.answer_sha256


async def test_the_judge_sees_only_claims_and_cited_evidence() -> None:
    judge = ScriptedJudge(
        JudgeReply(
            findings=(JudgeFinding(claim=0, evidence_id=None, quote=None),),
            usage=None,
        )
    )

    await check(answered("You get 24 days.", cited=(SECOND,)), judge)

    ((claims, evidence),) = judge.calls
    assert claims == ("You get 24 days.",)
    assert [i.evidence_id.value for i in evidence.items] == [SECOND]


def proposal(evidence_id: object, quote: object) -> JudgeReply:
    return JudgeReply(
        findings=(JudgeFinding(claim=0, evidence_id=evidence_id, quote=quote),),
        usage=USAGE,
    )


@pytest.mark.parametrize(
    ("reply", "verdict"),
    [
        (proposal(FIRST, "24 days of annual leave"), SupportVerdict.SUPPORTED),
        (proposal(None, None), SupportVerdict.NO_SUPPORT),
        (proposal(THIRD, "Salary bands"), SupportVerdict.NOT_CITED),
        (proposal("E00000000", "24 days"), SupportVerdict.NOT_CITED),
        (proposal(FIRST, "26 days of annual leave"), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, "24 DAYS"), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, CARRY), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, ""), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, " "), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, None), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, "."), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, "4 days of annual leave"), SupportVerdict.QUOTE_NOT_FOUND),
        (proposal(FIRST, "annual leave"), SupportVerdict.NUMBER_MISMATCH),
    ],
    ids=[
        "verbatim-quote",
        "no-evidence",
        "uncited-evidence",
        "unknown-evidence",
        "changed-number",
        "changed-case",
        "quote-from-other-evidence",
        "empty-quote",
        "blank-quote",
        "no-quote",
        "punctuation-quote",
        "quote-inside-a-number",
        "quote-without-the-number",
    ],
)
async def test_the_server_decides_each_verdict_from_the_evidence_text(
    reply: JudgeReply, verdict: SupportVerdict
) -> None:
    report = await check(answered("You get 24 days."), ScriptedJudge(reply))

    (finding,) = report.findings
    assert finding.verdict is verdict
    assert report.usage == USAGE
    if verdict is SupportVerdict.SUPPORTED:
        assert finding.quote_start == LEAVE.index("24 days of annual leave")


def finding(claim: object = 0, evidence_id: object = None, quote: object = None) -> Any:
    return JudgeFinding(claim=claim, evidence_id=evidence_id, quote=quote)


@pytest.mark.parametrize(
    "reply",
    [
        None,
        "not a reply",
        JudgeReply(findings=None, usage=USAGE),
        JudgeReply(findings=[finding()], usage=USAGE),
        JudgeReply(findings=(), usage=USAGE),
        JudgeReply(findings=(finding(), finding(claim=1)), usage=USAGE),
        JudgeReply(findings=(finding(claim=1),), usage=USAGE),
        JudgeReply(findings=(finding(claim=True),), usage=USAGE),
        JudgeReply(findings=(finding(evidence_id=5),), usage=USAGE),
        JudgeReply(findings=(finding(evidence_id=FIRST, quote=[MARKER]),), usage=USAGE),
        JudgeReply(findings=({"claim": 0},), usage=USAGE),
        JudgeReply(findings=(finding(),), usage=MARKER),  # type: ignore[arg-type]
        JudgeReply(
            findings=(finding(),),
            usage=Usage.model_construct(
                provider="p",
                model="m",
                latency_ms=0,
                input_tokens=MARKER,
                output_tokens=-1,
                total_tokens=0,
            ),
        ),
    ],
    ids=[
        "none",
        "not-a-reply",
        "no-findings",
        "a-list",
        "too-few",
        "too-many",
        "wrong-claim",
        "bool-claim",
        "number-id",
        "list-quote",
        "a-dict",
        "text-usage",
        "usage-with-text-counts",
    ],
)
async def test_a_malformed_judge_reply_fails_the_check_and_keeps_its_usage(
    reply: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    report = await check(answered("You get 24 days."), ScriptedJudge(reply))

    assert report.outcome is SupportOutcome.JUDGE_FAILED
    assert report.findings == ()
    if type(reply) is JudgeReply and reply.usage is USAGE:
        assert report.usage == USAGE
    assert MARKER not in repr(report)
    assert MARKER not in caplog.text


async def test_claims_beyond_the_limit_are_reported_as_not_checked() -> None:
    text = " ".join(f"You get {n} days." for n in range(MAX_CLAIMS + 3))

    report = await check(answered(text))

    assert len(report.findings) == MAX_CLAIMS + 3
    assert [f.verdict for f in report.findings[MAX_CLAIMS:]] == [
        SupportVerdict.NOT_CHECKED
    ] * 3


@pytest.mark.parametrize(
    ("result", "evidence"),
    [
        (replace(answered("x."), outcome=AnswerOutcome.ABSTAINED), EVIDENCE),
        (replace(answered("x."), outcome="answered"), EVIDENCE),
        (answered("You get 24 days."), build_evidence([candidate(1)])),
        (answered("You get 24 days."), list(EVIDENCE.items)),
        (replace(answered("x."), text="\u200b"), EVIDENCE),
        (replace(answered("x."), citations=()), EVIDENCE),
        ("You get 24 days.", EVIDENCE),
    ],
    ids=[
        "abstention",
        "text-outcome",
        "other-evidence",
        "not-an-evidence-set",
        "invisible-answer",
        "no-citations",
        "not-a-result",
    ],
)
async def test_a_request_that_cannot_be_checked_is_refused_before_the_judge(
    result: object, evidence: object
) -> None:
    judge = ScriptedJudge()

    with pytest.raises(InvalidSupportRequest):
        await check_support(
            result,  # type: ignore[arg-type]
            evidence,  # type: ignore[arg-type]
            judge,
            request_id=REQUEST_ID,
            timeout_seconds=5,
        )

    assert judge.calls == []


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), True, 10**400])
async def test_a_bad_timeout_is_refused_before_the_judge(seconds: float) -> None:
    judge = ScriptedJudge()

    with pytest.raises(InvalidSupportRequest):
        await check(answered("You get 24 days."), judge, timeout_seconds=seconds)

    assert judge.calls == []


async def test_a_stalled_judge_times_out_at_the_deadline() -> None:
    with pytest.raises(ChatModelTimeout) as raised:
        await check(
            answered("You get 24 days."),
            ScriptedJudge(block=True),
            timeout_seconds=0.05,
        )

    assert raised.value.__context__ is None


async def test_a_provider_failure_propagates() -> None:
    error = ChatModelUnavailable()

    with pytest.raises(ChatModelUnavailable) as raised:
        await check(answered("You get 24 days."), ScriptedJudge(error=error))

    assert raised.value is error


async def test_a_timeout_raised_by_the_judge_itself_is_not_hidden() -> None:
    with pytest.raises(TimeoutError) as raised:
        await check(answered("You get 24 days."), ScriptedJudge(error=TimeoutError()))

    assert type(raised.value) is TimeoutError


async def test_one_log_record_with_counts_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    evidence = build_evidence([candidate(1, text=f"{MARKER} grants 24 days.")])
    result = replace(
        answered("x."),
        text=f"{MARKER} grants 24 days.",
        citations=resolve_citations(evidence, [evidence.items[0].evidence_id.value]),
    )

    report = await check_support(
        result,
        evidence,
        LexicalSupportJudge(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )

    (record,) = [r for r in caplog.records if r.message == "support check finished"]
    assert record.__dict__["support"] == {
        "request_id": str(REQUEST_ID),
        "checker": "lexical-overlap",
        "version": "1",
        "outcome": "checked",
        "claims": 1,
        "unsupported": 0,
    }
    assert MARKER not in caplog.text
    assert MARKER not in repr(report)


@settings(max_examples=300, deadline=None)
@given(
    claim=st.one_of(st.none(), st.integers(-2, 3), st.booleans(), st.text(max_size=3)),
    evidence_id=st.one_of(
        st.none(),
        st.sampled_from([FIRST, SECOND, THIRD, "E00000000"]),
        st.text(max_size=10),
        st.integers(),
    ),
    quote=st.one_of(
        st.none(),
        st.text(max_size=20),
        st.sampled_from([LEAVE[6:20], CARRY[:10], MARKER, "Salary bands"]),
        st.integers(),
    ),
)
def test_any_judge_finding_gives_one_checked_verdict_inside_cited_text(
    claim: object, evidence_id: object, quote: object
) -> None:
    reply = JudgeReply(
        findings=(JudgeFinding(claim=claim, evidence_id=evidence_id, quote=quote),),
        usage=None,
    )

    report = asyncio.run(check(answered("You get 24 days."), ScriptedJudge(reply)))

    for found in report.findings:
        if found.verdict is SupportVerdict.SUPPORTED:
            assert found.evidence_id is not None
            assert found.evidence_id.value in (FIRST, SECOND)
            text = EVIDENCE.get(found.evidence_id).text  # type: ignore[union-attr]
            assert text[found.quote_start : found.quote_end] == quote
        else:
            assert (found.evidence_id, found.quote_start) == (None, None)


class FakeAnswerModel:
    """Answers any prompt with one fixed text; records what it was sent."""

    def __init__(self, text: object, *, refused: bool = False) -> None:
        self._text = text
        self._refused = refused
        self.prompts: list[EvidencePrompt] = []

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(provider="fake", model="judge-1")

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        self.prompts.append(prompt)
        return ModelReply(text=self._text, refused=self._refused, usage=USAGE)


def judge_reply(*findings: dict[str, object]) -> str:
    return json.dumps({"findings": list(findings)})


async def test_a_model_judge_sends_a_versioned_prompt_and_is_checked_like_any() -> None:
    model = FakeAnswerModel(
        judge_reply(
            {"claim": 0, "evidence_id": FIRST, "quote": "24 days of annual leave"},
            {"claim": 1, "evidence_id": SECOND, "quote": "carries over for 7 days"},
        )
    )
    result = answered("You get 24 days. Unused leave carries over for 7 days.")

    report = await check(result, ModelSupportJudge(model))

    assert report.checker == CheckerIdentity(
        name="model-judge:fake/judge-1", version="1"
    )
    assert [f.verdict for f in report.findings] == [
        SupportVerdict.SUPPORTED,
        SupportVerdict.QUOTE_NOT_FOUND,
    ]
    assert report.usage == USAGE
    (sent,) = model.prompts
    assert sent == judge_prompt(
        ("You get 24 days.", "Unused leave carries over for 7 days."),
        EvidenceSet(EVIDENCE.items[:2]),
    )
    assert MARKER not in sent.input
    finding_schema = sent.response_schema()["properties"]["findings"]
    assert finding_schema["items"]["properties"]["evidence_id"]["enum"] == [
        FIRST,
        SECOND,
        None,
    ]
    assert (finding_schema["minItems"], finding_schema["maxItems"]) == (2, 2)


@pytest.mark.parametrize(
    "model",
    [
        FakeAnswerModel(None, refused=True),
        FakeAnswerModel("not json"),
        FakeAnswerModel(None),
        FakeAnswerModel(json.dumps({"findings": [], "extra": 1})),
        FakeAnswerModel(json.dumps({"findings": [{"claim": 0}]})),
        FakeAnswerModel(json.dumps([])),
    ],
    ids=["refused", "not-json", "no-text", "extra-key", "missing-fields", "a-list"],
)
async def test_a_model_judge_that_cannot_answer_fails_the_check(
    model: FakeAnswerModel,
) -> None:
    report = await check(answered("You get 24 days."), ModelSupportJudge(model))

    assert report.outcome is SupportOutcome.JUDGE_FAILED
    assert report.usage == USAGE


def test_a_line_break_of_any_kind_ends_a_claim() -> None:
    for separator in ("\r\n", "\r", "\u2028", "\u2029", "\x85", "\v", "\f"):
        text = f"Leave is 24 days{separator}Pay is 5"

        claims = split_claims(text)

        assert [text[c.start : c.end] for c in claims] == [
            "Leave is 24 days",
            "Pay is 5",
        ], repr(separator)


def test_splitting_a_long_run_of_dots_takes_linear_time() -> None:
    started = time.perf_counter()

    claims = split_claims("." * 15_999 + "x")

    assert len(claims) == 1
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(
    ("claim", "sentence"),
    [
        (
            "Unused leave carries over for 2.5 days.",
            "Unused leave carries over for 5.2 days.",
        ),
        (
            "The limit is 1,000 euros per year.",
            "The limit is 000 euros, once per 1 year.",
        ),
        ("Servers stop at -5 degrees.", "Servers stop at 5 degrees."),
    ],
    ids=["decimal", "thousands", "sign"],
)
async def test_the_lexical_judge_compares_whole_numbers(
    claim: str, sentence: str
) -> None:
    evidence = build_evidence([candidate(1, text=sentence)])
    result = replace(
        answered("x."),
        text=claim,
        citations=resolve_citations(evidence, [evidence.items[0].evidence_id.value]),
    )

    report = await check_support(
        result,
        evidence,
        LexicalSupportJudge(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )

    assert [f.verdict for f in report.findings] == [SupportVerdict.NO_SUPPORT]


async def test_a_quote_must_not_cut_an_accent_off() -> None:
    evidence = build_evidence([candidate(1, text="The cafe\u0301 opens at 9.")])
    result = replace(
        answered("x."),
        text="The cafe opens at 9.",
        citations=resolve_citations(evidence, [evidence.items[0].evidence_id.value]),
    )
    judge = ScriptedJudge(
        JudgeReply(
            findings=(
                JudgeFinding(
                    claim=0,
                    evidence_id=evidence.items[0].evidence_id.value,
                    quote="The cafe",
                ),
            ),
            usage=None,
        )
    )

    report = await check_support(
        result, evidence, judge, request_id=REQUEST_ID, timeout_seconds=5
    )

    assert [f.verdict for f in report.findings] == [SupportVerdict.QUOTE_NOT_FOUND]


async def test_a_citation_listed_twice_reaches_the_judge_once() -> None:
    result = answered("You get 24 days.")
    judge = ScriptedJudge(proposal(FIRST, "24 days of annual leave"))

    report = await check(replace(result, citations=result.citations * 2), judge)

    ((_, evidence),) = judge.calls
    assert [i.evidence_id.value for i in evidence.items] == [FIRST, SECOND]
    assert report.outcome is SupportOutcome.CHECKED


class BusyJudge:
    """Works without ever waiting, past any deadline."""

    identity = CheckerIdentity(name="busy", version="1")

    async def judge(
        self, claims: tuple[str, ...], evidence: EvidenceSet, **_: Any
    ) -> Any:
        time.sleep(0.1)  # noqa: ASYNC251 - a judge that blocks is the point
        return proposal(None, None)


async def test_a_judge_that_never_waits_still_misses_its_deadline() -> None:
    with pytest.raises(ChatModelTimeout):
        await check(answered("You get 24 days."), BusyJudge(), timeout_seconds=0.01)


class BrokenIdentityJudge(ScriptedJudge):
    @property
    def identity(self) -> CheckerIdentity:  # type: ignore[override]
        raise RuntimeError(MARKER)


async def test_a_judge_whose_identity_breaks_is_refused_without_its_message() -> None:
    with pytest.raises(InvalidSupportRequest) as raised:
        await check(answered("You get 24 days."), BrokenIdentityJudge())

    assert MARKER not in str(raised.value)
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "reply",
    [None, "a reply", ModelReply(text="{}", refused="yes", usage=USAGE)],  # type: ignore[arg-type]
    ids=["none", "text", "text-refused-flag"],
)
async def test_a_model_judge_given_a_broken_reply_raises_a_typed_error(
    reply: object,
) -> None:
    class Broken(FakeAnswerModel):
        async def generate(self, prompt: EvidencePrompt, **_: Any) -> Any:
            return reply

    with pytest.raises(ChatModelMalformedResponse):
        await check(answered("You get 24 days."), ModelSupportJudge(Broken(None)))


@pytest.mark.parametrize(
    "text",
    [
        '{"findings": [], "findings": '
        '[{"claim": 0, "evidence_id": null, "quote": null}]}',
        json.dumps(
            {"findings": [{"claim": 0, "evidence_id": None, "quote": "x" * 40_000}]}
        ),
    ],
    ids=["repeated-key", "too-long"],
)
async def test_a_model_judge_reply_with_a_repeated_key_or_too_long_fails(
    text: str,
) -> None:
    report = await check(
        answered("You get 24 days."), ModelSupportJudge(FakeAnswerModel(text))
    )

    assert report.outcome is SupportOutcome.JUDGE_FAILED


@pytest.mark.live
async def test_openai_live_judge_finds_the_wrong_number() -> None:
    from openai import AsyncOpenAI

    from knowledge_service.answering.openai_answering import (
        AnswerSettings,
        OpenAIAnswerModel,
    )

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")
    client = AsyncOpenAI(api_key=api_key)
    try:
        report = await check(
            answered(
                "You get 24 days of annual leave. Unused leave carries over for 9 days."
            ),
            ModelSupportJudge(OpenAIAnswerModel(client, AnswerSettings())),
            timeout_seconds=30.0,
        )
    finally:
        await client.close()

    assert report.outcome is SupportOutcome.CHECKED
    assert [f.verdict is SupportVerdict.SUPPORTED for f in report.findings] == [
        True,
        False,
    ]
