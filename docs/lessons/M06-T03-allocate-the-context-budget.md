# M06-T03 — Allocate the context budget

Source task: [M06-T03](../curriculum/milestones/M06-answers-and-conversations/M06-T03-allocate-the-context-budget.md)

## What you will do

A chat model can only read and write a fixed amount of text per request. This lesson decides how that space is shared, so a request never overflows it. M06-T02 built the prompt, but nothing limited its size: 20 long Evidence texts went in whole. Now one function splits a fixed number of tokens between the parts of a request, drops the lowest-ranked Evidence when it does not fit, and reports what it dropped.

- `allocate_context(question, evidence, budget)` returns the prompt to send, the Evidence it holds, and a report.
- The policy, the Answer's room, the Question, and the History's room are never cut. Evidence gets what is left, and is cut first.
- Evidence is kept or dropped whole, from the bottom of retrieval's order, so every Citation still points at the exact text the model read.
- The report lists every kept and dropped Evidence ID and the tokens of each part. It holds no Question or Document text, so it is safe to log.

| The task asks for | Where it happens |
|---|---|
| Reserve tokens for policy, History, Question, Evidence, and Answer | Step 2: `ContextBudget` reserves the Answer and History; the policy and Question are counted in every prompt |
| Trim by explicit priority | Step 2: a fixed order of five parts; only Evidence is trimmed, lowest rank first |
| Preserve provenance | Step 2: kept Evidence is the same objects with the same IDs; dropped Evidence leaves the prompt, the schema, and the set |
| Budgets never exceeded, trimming reported | Step 3: an exact-boundary test, a property test over random Evidence, and `ContextReport` checks |
| Deterministic tests: success, highest-risk edge, typed failure | Step 3: all fits; the exact boundary; `ContextOverBudget` and `InvalidContextBudget` |
| No seeded text in telemetry | Step 3: a planted marker never appears in the report, a repr, or an error |

This lesson includes coding: Steps 2 and 3 write code. Steps 1, 4, and 5 do not. Nothing calls a model or a database.

Terms used in this lesson:

- **Token:** the unit a model counts text in, roughly a short word or part of one. Models are priced and limited in tokens.
- **Context window:** the most tokens one request may hold, counting both what is sent and what the model writes back. A model with a 128,000-token window cannot read 130,000.
- **Context budget:** how a request splits its window between parts, like a household budget that pays rent and food first and spends what is left.
- **Reserve:** tokens set aside for something before anything else is spent, such as room for the Answer.
- **Head of a list:** its first items, in order. "The longest head that fits" means: keep items 1, 2, 3, and so on, and stop at the first that does not fit.
- **Lower bound:** a number that is never more than the true value. If even the lower bound is too big, the true value is too.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `answering/evidence.py` gives an `EvidenceSet`: at most 20 Evidence in retrieval's order, each with a random ID. `answering/prompt.py` renders the fixed policy, the Question, the Evidence, and the response schema. Nothing limits that prompt's size yet.
- **How tokens are counted.** The project has no tokenizer download. The OpenAI embedding and reranking adapters already count a request as its UTF-8 bytes: a token is at least one byte, so this count is never too low. `count_prompt_tokens` counts the instructions, the input, and the schema, plus 64 tokens for the request's own framing, the same allowance as the M05-T12 reranker.
- **Count what is sent.** The function counts the prompt it actually renders, then returns that same prompt. M06-T04 sends it as it is, so the number that was checked is the number that is sent.
- **The priority order.** The policy and schema must be there, or the model has no rules. The Answer needs room, or it is cut off. The Question must be whole, or the model answers another question. History is a fixed room that M06-T11 will fill with earlier turns. Evidence is the only part with a natural order to trim by: retrieval already ranked it.
- **Whole items, from the bottom.** Cutting an Evidence's text in half would leave its ID pointing at text the model never read. So an Evidence is kept whole or dropped whole. The kept Evidence is the longest head of retrieval's order that fits. A small Evidence below a dropped big one is dropped too: the order is the priority, and a hole in it would be harder to explain.
- **No room even for the Question.** When the policy, the Question, and both reserves already exceed the window, the request is refused with `ContextOverBudget`, which holds two numbers and no text. When the Question fits but no Evidence does, the prompt has no Evidence; M06-T07 will make the model abstain then.
- **Bounded work.** By this count a text costs at least one token per character. So the function first adds up lengths, which is free, and never reads, copies, or renders Evidence whose length alone cannot fit. Then it renders the whole head that might fit; if that is too big, it halves the search.
- **What the review found.** An adversarial review could not push a prompt over budget, keep the wrong Evidence, or leak a planted marker. It found one broken guarantee: the first version encoded every Evidence text in full to measure it, so a 10 MB text cost 10 MB of memory even with a tiny budget. Now only lengths are added up, and a regression test measures the memory peak.
- **Where the code lives.** `answering/context_budget.py`, next to the prompt it renders.
- **Known limits.** The byte count overestimates: English text is about four bytes per real token, and each Ukrainian letter is two bytes, so the same budget holds about half as much Ukrainian text as English. History is reserved but empty until M06-T11. Rendering escapes one character at a time, about 50 to 70 ms for 20 realistic Chunks, and seconds for budgets in the millions. Calling `count_prompt_tokens` with something that is not a prompt raises Python's own `AttributeError`.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 4 checks the predictions.

Create `docs/evidence/M06-T03-context-budget.md`:

```markdown
# M06-T03 — Context budget evidence

## Invariant

`allocate_context` returns a prompt whose counted tokens, plus the Answer
and History reserves, never exceed the context window, and the prompt
returned is the one that was counted. The policy, schema, and Question are
never trimmed. Evidence is kept whole, in retrieval's order, as the longest
head that fits; the rest is dropped and appears in neither the prompt, the
schema, nor the returned set. The report names every kept and dropped
Evidence ID with token counts and holds no Question or Document text. When
even a prompt without Evidence does not fit, `ContextOverBudget` is raised
with counts only.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.context_budget"`
fails with `ModuleNotFoundError`.

## Predictions

- How many of 20 Chunks that repeat the word "days" 256 times fit in a 16,000-token window with 1,000 for the Answer and 2,000 for History: ...
- The same for the Ukrainian word "днів", also four letters, and why the number differs: ...
- How many tests the deliberate break in Step 4 fails, and which ones: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 4 can prove wrong.

### Step 2 — Coding step: allocate the budget

This step writes the one function that decides what goes into a request before the model sees it.

**In plain words:** The code takes the model's window and takes off the room kept for the Answer and for earlier turns. It renders the prompt with no Evidence, and refuses the request if even that does not fit. Then it adds up the lengths of the Evidence, top-ranked first, to see how many could possibly fit, and renders the prompt with that many. If it is too big, it tries fewer until the most that fit is found. It returns that prompt, the Evidence inside it, and a report of what was kept and dropped.

| Part | What it holds or does |
|---|---|
| `ContextBudget` | The window and the two reserves; refuses numbers that are not whole, a reserve below its minimum, or reserves that fill the window |
| `ContextReport` | Kept and dropped Evidence IDs, the tokens of the prompt without Evidence, and what the Evidence added; only numbers and opaque IDs |
| `AllocatedContext` | The prompt to send, its Evidence as a new `EvidenceSet`, and the report |
| `count_prompt_tokens` | UTF-8 bytes of the instructions, input, and schema, plus 64 |
| `allocate_context` | Checks, renders without Evidence, finds the head that might fit by length, then the head that fits by rendering |
| `ContextOverBudget` | Raised when even a prompt without Evidence does not fit; holds `needed` and `available` |

Create `src/knowledge_service/answering/context_budget.py`:

```python
"""The context budget: what one Answer's prompt may spend, and what it drops.

A chat model reads and writes within one context window, counted in
tokens. `allocate_context` splits a `ContextBudget` between five parts and
renders the prompt (see `answering.prompt`) that fits it.

The parts, by priority:

1. **Policy**: the fixed instructions and the response schema. Never trimmed.
2. **Answer**: `answer_tokens`, reserved for what the model writes. Never trimmed.
3. **Question**: the user's Question, whole. Never trimmed.
4. **History**: `history_tokens`, reserved for earlier turns of the
   conversation. M06-T11 fills it; this module only keeps the room free.
5. **Evidence**: whatever is left. Trimmed first.

What an allocation promises:

- **Never over budget.** The counted tokens of the prompt actually
  returned, plus the Answer and History reserves, never exceed
  `context_tokens`. The returned prompt is the one that was counted.
- **Trimmed by rank, whole items only.** Evidence keeps retrieval's order,
  and the kept Evidence is the longest head of that order that fits. An
  Evidence is kept whole or dropped whole; its text is never cut, so a
  Citation always points at the exact text the model read.
- **Provenance preserved.** Kept Evidence is the same objects, with the
  same opaque IDs and provenance, in a new `EvidenceSet`. Nothing is
  added, and dropped Evidence cannot be cited: it is neither in the
  prompt, nor in the schema, nor in the returned set.
- **Decisions reported.** `ContextReport` names every kept and dropped
  Evidence ID and the tokens of each part. It holds counts and opaque IDs
  only, so it is safe to log.
- **A safe count.** Tokens are counted as UTF-8 bytes plus a fixed
  framing allowance, the project's upper bound for OpenAI tokenizers: a
  token is at least one byte, so a prompt never has more tokens than this
  count says.
- **Bounded work.** By this count a text costs at least one token per
  character, so Evidence whose length alone cannot fit is never read,
  copied, or rendered. The head that fits is found in a few renders, one
  when it all fits, so time and memory grow with the budget, not with the
  Evidence's size.
- **A typed denial.** When the policy, the Question, and the two reserves
  leave no room even for a prompt without Evidence, `ContextOverBudget` is
  raised; it holds token counts only. A bad budget raises
  `InvalidContextBudget`. A bad Question raises the prompt's
  `InvalidPrompt`, and so does Evidence that is not valid Unicode, when it
  is inside the head that might fit; Evidence beyond it is never read.
"""

import re
from dataclasses import dataclass

from knowledge_service.answering.evidence import EvidenceId, EvidenceSet
from knowledge_service.answering.prompt import (
    EvidencePrompt,
    InvalidPrompt,
    render_prompt,
)

# What a chat request adds around the three texts: roles, message
# boundaries, the schema's wrapper. The same allowance as M05-T12's.
FRAMING_TOKENS = 64
# A lone surrogate is not valid Unicode; found without copying the text.
_SURROGATE = re.compile(r"[\ud800-\udfff]")


class InvalidContextBudget(ValueError):
    """A context budget's numbers are not usable."""


class ContextOverBudget(Exception):
    """The policy, Question, and reserves alone exceed the context window.

    Holds token counts only, never the Question.
    """

    def __init__(self, needed: int, available: int) -> None:
        super().__init__(
            f"the prompt needs {needed} tokens without Evidence,"
            f" but only {available} are available"
        )
        self.needed = needed
        self.available = available


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """How many tokens one request may use, and how many are set aside.

    `context_tokens` is the model's whole window. `answer_tokens` is
    reserved for the Answer and `history_tokens` for earlier turns; the
    prompt gets the rest.
    """

    context_tokens: int
    answer_tokens: int
    history_tokens: int

    def __post_init__(self) -> None:
        values = (self.context_tokens, self.answer_tokens, self.history_tokens)
        # bool is an int, so it is refused by name.
        if not all(type(value) is int for value in values):
            raise InvalidContextBudget("a context budget holds whole numbers")
        if self.answer_tokens < 1 or self.history_tokens < 0:
            raise InvalidContextBudget(
                "the Answer needs at least one token and History cannot be negative"
            )
        if self.answer_tokens + self.history_tokens >= self.context_tokens:
            raise InvalidContextBudget("the reserves leave no room for a prompt")

    @property
    def prompt_tokens(self) -> int:
        """What the prompt may spend: the window minus both reserves."""
        return self.context_tokens - self.answer_tokens - self.history_tokens


@dataclass(frozen=True, slots=True)
class ContextReport:
    """How the budget was spent, and which Evidence was kept or dropped.

    `base_tokens` is the prompt without Evidence: policy, schema, and
    Question. `evidence_tokens` is what the kept Evidence added to it.
    """

    context_tokens: int
    answer_tokens: int
    history_tokens: int
    base_tokens: int
    evidence_tokens: int
    kept: tuple[EvidenceId, ...]
    dropped: tuple[EvidenceId, ...]

    @property
    def prompt_tokens(self) -> int:
        """The counted tokens of the prompt that is sent."""
        return self.base_tokens + self.evidence_tokens

    @property
    def unused_tokens(self) -> int:
        """The part of the window nobody uses; never negative."""
        return (
            self.context_tokens
            - self.answer_tokens
            - self.history_tokens
            - self.prompt_tokens
        )


@dataclass(frozen=True, slots=True)
class AllocatedContext:
    """The prompt to send, the Evidence it holds, and the report."""

    prompt: EvidencePrompt
    evidence: EvidenceSet
    report: ContextReport


def count_prompt_tokens(prompt: EvidencePrompt) -> int:
    """An upper bound on a prompt's tokens: its UTF-8 bytes plus framing."""
    parts = (prompt.instructions, prompt.input, prompt.schema)
    return sum(len(part.encode("utf-8")) for part in parts) + FRAMING_TOKENS


def allocate_context(
    question: str, evidence: EvidenceSet, budget: ContextBudget
) -> AllocatedContext:
    """Render the prompt with the longest head of Evidence the budget allows."""
    if type(budget) is not ContextBudget:
        raise InvalidContextBudget("budget must be a ContextBudget")
    if type(evidence) is not EvidenceSet:
        raise InvalidPrompt("evidence must be an EvidenceSet")
    available = budget.prompt_tokens
    base_prompt = render_prompt(question, EvidenceSet(()))
    base = count_prompt_tokens(base_prompt)
    if base > available:
        raise ContextOverBudget(base, available)
    # Rendering never shrinks a text, and every character is at least one
    # byte, so Evidence whose length alone does not fit can never fit.
    limit = 0
    spent = base
    for item in evidence.items:
        texts = (item.title, item.heading_path or "", item.text)
        spent += sum(len(text) for text in texts)
        if spent > available:
            break
        if any(_SURROGATE.search(text) for text in texts):
            raise InvalidPrompt("an Evidence text is not valid Unicode")
        limit += 1
    # Fewer Evidence never costs more, so the longest head that fits is
    # found by halving. The whole head is tried first: it usually fits.
    best, best_tokens, kept = base_prompt, base, 0
    low, high = 1, limit
    middle = limit
    while low <= high:
        prompt = render_prompt(question, EvidenceSet(evidence.items[:middle]))
        tokens = count_prompt_tokens(prompt)
        if tokens <= available:
            best, best_tokens, kept = prompt, tokens, middle
            low = middle + 1
        else:
            high = middle - 1
        middle = (low + high) // 2
    items = evidence.items
    return AllocatedContext(
        prompt=best,
        evidence=EvidenceSet(items[:kept]),
        report=ContextReport(
            context_tokens=budget.context_tokens,
            answer_tokens=budget.answer_tokens,
            history_tokens=budget.history_tokens,
            base_tokens=base,
            evidence_tokens=best_tokens - base,
            kept=tuple(item.evidence_id for item in items[:kept]),
            dropped=tuple(item.evidence_id for item in items[kept:]),
        ),
    )
```

Three details matter here. The length loop also checks each text it passes for a lone surrogate, a broken half of a character, so bad Evidence is refused the same way whichever renders the search happens to try. The search starts with the whole head, because in most requests everything fits and one render is enough. `kept` stays 0 when nothing fits, so the base prompt, the one without Evidence, is returned.

Check:

```bash
uv run python -c "import knowledge_service.answering.context_budget"
uv run pyright src/knowledge_service/answering/context_budget.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 3 — Coding step: boundary, property, and denial tests

This step proves the budget is never exceeded and that every trimming decision shows in the report.

**In plain words:** The tests build Evidence from the shared fake Candidates and give each case a budget measured from a real prompt, so a single token decides. They check that everything is kept when it fits, that one token less drops exactly one Evidence, and that a big Evidence in the middle drops everything below it. A property test tries 150 random sets of Evidence and budgets. The last tests check the refusals, that no planted text reaches the report, and that a huge Evidence is never rendered or copied.

| Test | What it proves |
|---|---|
| `test_evidence_that_fits_is_all_kept` | Success: all kept, prompt equals the M06-T02 rendering, report numbers add up |
| `test_the_budget_boundary_is_exact` | The highest-risk edge: at exactly the cost of two items, two are kept; one token less, one |
| `test_trimming_drops_from_the_bottom_and_keeps_provenance` | The same objects are kept; dropped IDs are in neither the input nor the schema |
| `test_when_no_evidence_fits_the_prompt_has_none` | Nothing fits: the prompt without Evidence, everything reported as dropped |
| `test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest` | Random Evidence and budgets: never over, and one more item would not fit |
| `test_a_question_too_long_for_the_window_is_denied_with_counts_only` | The typed denial, with no Question text in its message |
| `test_an_unusable_budget_is_refused` and the type check | Bad budgets raise `InvalidContextBudget` |
| `test_invalid_evidence_that_might_fit_is_refused` | Bad Evidence the search never renders is still refused |
| `test_evidence_beyond_what_might_fit_is_never_read` | Evidence too long to fit is dropped without being read |
| `test_the_report_and_reprs_hold_no_question_or_document_text` | No planted marker in any repr or report |
| `test_evidence_too_large_to_fit_is_never_rendered` and `..._never_copied` | Work grows with the budget, not with the Evidence (the review's regression) |

Create `tests/test_context_budget.py`:

```python
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
```

`cost` renders a real prompt with the first few items and counts it, so `budget_for(cost(given_evidence, 2))` is a budget with no token to spare for two items. `MARKER` and `candidate` come from `tests/evidence_fakes.py`, which M06-T01 added.

Check:

```bash
uv run pytest tests/test_context_budget.py -q
```

Expected: 18 passed.

### Step 4 — No coding in this step: break it and decide

This step shows which tests guard the reserves, and records what you learned.

**Action 1:** break the budget on purpose: forget the History reserve. In `src/knowledge_service/answering/context_budget.py`, in `ContextBudget.prompt_tokens`, replace:

```python
        return self.context_tokens - self.answer_tokens - self.history_tokens
```

with:

```python
        return self.context_tokens - self.answer_tokens
```

Run:

```bash
uv run pytest tests/test_context_budget.py -q
```

Expected: 4 failed: both cases of `test_the_budget_boundary_is_exact`, `test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest`, and `test_a_question_too_long_for_the_window_is_denied_with_counts_only`. With the History room forgotten, the prompt grows into it, so the window overflows as soon as M06-T11 fills History. Undo the change and run again until all pass.

**Action 2:** check your two size predictions with real numbers. Run:

```bash
uv run python - <<'EOF'
import sys
sys.path.insert(0, "tests")
from evidence_fakes import candidate
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.context_budget import ContextBudget, allocate_context

budget = ContextBudget(16_000, 1_000, 2_000)
for word in ("days", "днів"):
    evidence = build_evidence(
        [candidate(number, text=" ".join([word] * 256)) for number in range(1, 21)]
    )
    report = allocate_context("How many days?", evidence, budget).report
    print(word, len(report.kept), "kept,", len(report.dropped), "dropped,", report.prompt_tokens, "tokens")
EOF
```

Expected: `days 8 kept, 12 dropped, 12856 tokens` and `днів 4 kept, 16 dropped, 11196 tokens`. Both words have four letters, but each Ukrainian letter is two bytes, so half as many Chunks fit.

**Action 3:** under `## Decision`, write:

- why the order is policy, Answer, Question, History, then Evidence, and what would go wrong if History were trimmed before Evidence;
- why Evidence is dropped whole and never cut;
- what the byte count costs Ukrainian users, and what a real tokenizer would change;
- what is left for later lessons: sending the prompt (M06-T04), abstaining when no Evidence fits (M06-T07), filling History (M06-T11);
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 5 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, add the new function to the `answering` row.

Find in `docs/architecture.md`:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), and `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs) |
```

Replace with:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), and `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_context_budget.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 18 passed for the narrow tests and 1807 passed for `make test-unit`. This lesson touches no database code, so `make test-integration` should give the same 329 passed as M06-T02.

**Action 3:** complete the evidence file:

- **Design:** the changed files; the five parts and their order; how tokens are counted and why; whole-item trimming; the report's fields.
- **Verification:** the commands and counts, the deliberate break, the size run from Step 4, and the known limits from "Before you begin".
- **Review:** the adversarial review could not exceed a budget, keep the wrong Evidence, or leak a marker, under a 3,000-case fuzz. It found one broken guarantee, fixed with tests that failed before the fix: every Evidence text was encoded in full to measure it, so time and memory grew with the Evidence, not the budget (1.56 s and 150 MB for 20 texts of 50 million characters, now 0 s and 0 MB).
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T03 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T04, and commit with `m06-t03: allocate the context budget`.

## Reflection questions

1. What does `allocate_context` hide from the code that will send the prompt in M06-T04? List what that code never has to think about.
2. A later change wants to speed things up by counting each Evidence once and adding the numbers, instead of rendering the prompt. Which promise could that break, and which test would catch it?
3. The byte count says a Ukrainian prompt is about three times as large as an English one of the same length. Who pays for that, and how would you check how far it is from the real token count?
4. When no Evidence fits, the prompt has none, and the model should abstain. Is that the right outcome for the user, or should the request be refused like a Question that does not fit? Why?
5. What input would you try next to prove the budget can still be exceeded?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] `ContextBudget` refuses numbers that are not whole, a reserve below its minimum, and reserves that fill the window.
- [ ] The returned prompt's counted tokens plus both reserves never exceed the window, and the returned prompt is the one counted.
- [ ] Evidence is kept whole, in retrieval's order, as the longest head that fits; dropped Evidence is in neither the prompt, the schema, nor the set.
- [ ] The report lists kept and dropped IDs and token counts, and holds no Question or Document text.
- [ ] A Question that does not fit raises `ContextOverBudget` with counts only.
- [ ] Evidence too long to fit is never read, copied, or rendered.
- [ ] You ran the deliberate break, saw 4 tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
