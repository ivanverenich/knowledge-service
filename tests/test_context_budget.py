"""The context budget: never exceeded, trimmed by rank, decisions reported."""

import tracemalloc
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering import context_budget
from knowledge_service.answering.context_budget import (
    ContextBudget,
    ContextOverBudget,
    InvalidContextBudget,
    allocate_context,
    count_prompt_tokens,
)
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.prompt import (
    EvidencePrompt,
    InvalidPrompt,
    render_prompt,
)

QUESTION = "How many days of annual leave do I get?"
ANSWER, HISTORY = 500, 300


def evidence(*texts: str) -> EvidenceSet:
    return build_evidence(
        [candidate(number, text=text) for number, text in enumerate(texts, 1)]
    )


def cost(items: EvidenceSet, count: int) -> int:
    """The counted tokens of the prompt holding the first `count` items."""
    return count_prompt_tokens(
        render_prompt(QUESTION, EvidenceSet(items.items[:count]))
    )


def budget_for(prompt_tokens: int) -> ContextBudget:
    """A budget that leaves exactly `prompt_tokens` for the prompt."""
    return ContextBudget(
        context_tokens=prompt_tokens + ANSWER + HISTORY,
        answer_tokens=ANSWER,
        history_tokens=HISTORY,
    )


def test_evidence_that_fits_is_all_kept() -> None:
    given_evidence = evidence("Leave is 24 days.", "Carry-over is 5 days.")

    allocated = allocate_context(QUESTION, given_evidence, budget_for(100_000))

    assert allocated.evidence == given_evidence
    assert allocated.prompt.input == render_prompt(QUESTION, given_evidence).input
    report = allocated.report
    assert report.kept == tuple(item.evidence_id for item in given_evidence.items)
    assert report.dropped == ()
    assert report.prompt_tokens == count_prompt_tokens(allocated.prompt)
    assert report.base_tokens == cost(given_evidence, 0)
    assert report.unused_tokens == 100_000 - report.prompt_tokens


@pytest.mark.parametrize(("spare", "kept"), [(0, 2), (-1, 1)])
def test_the_budget_boundary_is_exact(spare: int, kept: int) -> None:
    given_evidence = evidence("Leave is 24 days.", "Carry-over is 5 days.", "x" * 50)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 2) + spare)
    )

    assert allocated.evidence.items == given_evidence.items[:kept]
    assert allocated.report.dropped == tuple(
        item.evidence_id for item in given_evidence.items[kept:]
    )


def test_trimming_drops_from_the_bottom_and_keeps_provenance() -> None:
    given_evidence = evidence("Leave is 24 days.", "y" * 5_000, "Carry-over is 5 days.")
    first, large, small = given_evidence.items

    # Room for the first and the small one, but not for the large second.
    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 200)
    )

    assert allocated.evidence.items == (first,)
    assert allocated.evidence.items[0] is first
    assert allocated.report.dropped == (large.evidence_id, small.evidence_id)
    for dropped in (large, small):
        assert dropped.evidence_id.value not in allocated.prompt.input
        assert dropped.evidence_id.value not in allocated.prompt.schema


def test_when_no_evidence_fits_the_prompt_has_none() -> None:
    given_evidence = evidence("z" * 2_000)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 0) + 10)
    )

    assert allocated.evidence.items == ()
    assert allocated.prompt == render_prompt(QUESTION, EvidenceSet(()))
    assert allocated.report.kept == ()
    assert allocated.report.dropped == (given_evidence.items[0].evidence_id,)
    assert allocated.report.evidence_tokens == 0


@given(
    texts=st.lists(st.text(min_size=1, max_size=400), max_size=20),
    room=st.integers(min_value=0, max_value=12_000),
)
@settings(derandomize=True, max_examples=150, deadline=None)
def test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest(
    texts: list[str], room: int
) -> None:
    texts = [f"{text}." for text in texts]  # an Evidence text is never blank
    given_evidence = evidence(*texts)
    budget = budget_for(cost(given_evidence, 0) + room)

    allocated = allocate_context(QUESTION, given_evidence, budget)

    tokens = count_prompt_tokens(allocated.prompt)
    assert (
        tokens + budget.answer_tokens + budget.history_tokens <= budget.context_tokens
    )
    assert tokens == allocated.report.prompt_tokens
    assert allocated.report.unused_tokens >= 0
    kept = len(allocated.evidence.items)
    assert allocated.evidence.items == given_evidence.items[:kept]
    assert allocated.report.kept + allocated.report.dropped == tuple(
        item.evidence_id for item in given_evidence.items
    )
    assert allocated.prompt == render_prompt(QUESTION, allocated.evidence)
    if kept < len(given_evidence.items):
        assert cost(given_evidence, kept + 1) > budget.prompt_tokens


def test_a_question_too_long_for_the_window_is_denied_with_counts_only() -> None:
    question = MARKER + " " + "leave " * 600
    needed = count_prompt_tokens(render_prompt(question, EvidenceSet(())))

    with pytest.raises(ContextOverBudget) as raised:
        allocate_context(
            question, evidence("Leave is 24 days."), budget_for(needed - 1)
        )

    assert raised.value.needed == needed
    assert raised.value.available == needed - 1
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "numbers",
    [
        (1_000, 0, 0),
        (1_000, 500, -1),
        (1_000, 600, 400),
        (1_000.0, 500, 100),
        (1_000, True, 100),
    ],
)
def test_an_unusable_budget_is_refused(numbers: tuple[Any, Any, Any]) -> None:
    with pytest.raises(InvalidContextBudget):
        ContextBudget(*numbers)


def test_a_budget_that_is_not_a_context_budget_is_refused() -> None:
    with pytest.raises(InvalidContextBudget):
        allocate_context(QUESTION, evidence("Leave."), object())  # type: ignore[arg-type]


def test_invalid_evidence_that_might_fit_is_refused() -> None:
    # The second is short enough to be read but too long once each NUL is
    # escaped, so the third is never rendered; it is still checked.
    given_evidence = evidence("Leave is 24 days.", chr(0) * 40, "ok" + chr(0xD800))

    with pytest.raises(InvalidPrompt):
        allocate_context(
            QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 120)
        )


def test_evidence_beyond_what_might_fit_is_never_read() -> None:
    given_evidence = evidence("Leave is 24 days.", "broken " + chr(0xD800) * 3_000)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 10)
    )

    assert allocated.report.dropped == (given_evidence.items[1].evidence_id,)


def test_the_report_and_reprs_hold_no_question_or_document_text() -> None:
    given_evidence = evidence(f"{MARKER} leave", f"{MARKER} " + "w" * 3_000)

    allocated = allocate_context(
        f"{MARKER}?", given_evidence, budget_for(cost(given_evidence, 0) + 400)
    )

    assert allocated.report.dropped  # the large one was trimmed
    for shown in (repr(allocated), repr(allocated.report), str(allocated.report)):
        assert MARKER not in shown


def test_evidence_too_large_to_fit_is_never_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge = "\u200b" * 2_000_000  # escaping makes every character six bytes
    given_evidence = evidence(*["Leave is 24 days."] * 19, huge)
    rendered: list[int] = []

    def counting(question: str, items: EvidenceSet) -> EvidencePrompt:
        rendered.append(sum(len(item.text) for item in items.items))
        return render_prompt(question, items)

    monkeypatch.setattr(context_budget, "render_prompt", counting)

    allocated = allocate_context(QUESTION, given_evidence, budget_for(20_000))

    assert len(allocated.evidence.items) == 19
    assert len(rendered) == 2  # no Evidence, then the head that might fit
    assert max(rendered) < 1_000


def test_evidence_too_large_to_fit_is_never_copied() -> None:
    huge = "Я" * 5_000_000  # 10 MB as UTF-8
    given_evidence = evidence("Leave is 24 days.", huge)

    tracemalloc.start()
    try:
        allocated = allocate_context(QUESTION, given_evidence, budget_for(20_000))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(allocated.evidence.items) == 1
    assert peak < 1_000_000
