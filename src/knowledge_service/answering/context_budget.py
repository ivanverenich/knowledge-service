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
