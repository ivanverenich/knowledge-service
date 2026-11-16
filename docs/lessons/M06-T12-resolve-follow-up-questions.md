# M06-T12 — Resolve follow-up Questions

Source task: [M06-T12](../curriculum/milestones/M06-answers-and-conversations/M06-T12-resolve-follow-up-questions.md)

## What you will do

This lesson turns a follow-up such as "And abroad?" into a search text that names its subject, such as "What is the daily allowance for business trips abroad?". Search reads one text at a time, so a follow-up alone finds the wrong Chunks or none. The rewrite must be safe: it may only add what the conversation already said, in the same language, without losing a code such as `EXP-2041`.

- `resolve_question` takes the reader's History from M06-T11 and asks the rewrite model of M05-T15 to resolve the Question, but only when it is a follow-up.
- It checks the rewrite once more for follow-ups: the language and the entities of the earlier Question must be kept.
- `compare_resolution` stores the bilingual test conversations, reads them back as History, and measures search with and without resolution.

| The task asks for | Where it happens |
|---|---|
| Structured rewriting only for classified follow-ups | Step 2: only the `follow_up` class is ever sent, through the Structured Outputs rewriter of M05-T15 |
| Retain the original language | Step 2: every new word of the rewrite must be in the stated language's alphabet |
| Retain entities | Step 2: the Question's words to keep and the identifiers and acronyms of the newest earlier Question |
| A traceable rewritten query | Step 2: `ResolvedQuestion` holds both texts and how the search text was chosen; one log record of names and counts |
| Bilingual conversation cases measure retrieval with and without resolution | Steps 3 and 4: `compare_resolution` on the English and Ukrainian follow-up cases |
| Tests: success, highest-risk edge, typed failure | Step 4: `tests/test_follow_up.py` and `tests/test_follow_up_integration.py` |
| Authorization before data leaves storage | Step 2: only the reader's own Questions, read owner-scoped by `load_history`, reach the model; never an Answer or a Chunk |
| No seeded text in telemetry | Step 4: a planted marker never reaches a log record, a repr, an error, or the report |

This lesson includes coding: Steps 2, 3, and 4 write code. Steps 1, 5, and 6 do not. The integration tests need the test database. No step calls OpenAI: the tests use the fake rewriter from M05-T15.

Terms used in this lesson:

- **Follow-up:** a Question that only makes sense with the turns before it, such as "And abroad?" or "How long does it take?".
- **Resolution:** rewriting a follow-up into one standalone search text, using the earlier Questions. Here it is a narrow kind of query rewriting.
- **Rewrite class:** the label `classify_question` (M05-T14) gives each Question, such as `follow_up`, `acronym`, or `exact_identifier`; it decides whether a rewrite may be tried.
- **Entity:** here, a word that names one exact thing and must not change, such as the error code `EXP-2041` or the acronym `VPN`.
- **Fallback:** searching with the Question as typed because the rewrite failed or broke a rule.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** M05-T14 classifies a Question; M05-T15 sends a rewritable one to a `RewriteModel` with Structured Outputs and checks the answer with `checked_rewrite`, falling back to the Question as typed. M06-T11 gives the reader's History. Nothing connects them yet.
- **Only follow-ups.** The deployment flag `QUERY_REWRITE_CLASSES` may switch on several classes, but the M05-T15 run allowed only `follow_up`, and acronym rewriting harmed a case. So the resolver intersects any policy with `follow_up` alone: an acronym Question is never sent, even if the flag says so.
- **Only Questions are sent.** History holds Answers too, but an Answer is text written from Chunks. The rewriter needs the subject, which the User's own Questions name, so it never sees an Answer. The Questions come from `load_history`, which reads only the reader's own live Conversation.
- **The newest stretch only.** At most ten earlier Questions are sent, taken from the newest back. If one cannot be searched, for example because it is longer than a search text may be, the stretch stops there. Otherwise an older topic could take the place of the one the reader just asked about.
- **Language.** M05-T15 lets a rewrite reuse any word the User typed, in any alphabet. In a bilingual Conversation that is a hole: an English earlier Question could turn a Ukrainian follow-up into an English search. So for a follow-up the rewrite is checked again as if only the Question and the carried entities were typed. A Question of mixed or unknown language is never sent, because its alphabet cannot be checked.
- **Entities.** A follow-up usually continues the newest earlier Question. Its identifiers and acronyms must appear in the rewrite: "How do I fix it?" after "What does error EXP-2041 mean?" must keep `EXP-2041`. A rewrite that drops one is not used.
- **Traceable.** A `ResolvedQuestion` holds the Question as typed, the search text, the class, the outcome, the fallback reason, the rewriter, the billed usage, and how many earlier Questions were sent. Texts stay out of its repr, and the one log record holds names and counts.
- **Measuring.** `compare_resolution` replays the bilingual rewrite cases of M05-T15 as stored Conversations: each earlier Question is stored with `add_turn` and a fixed failed reply, History is read back, and the Question is resolved. Then each case is searched as typed and, if resolved, again with its search text, by dense and lexical search fused. The result has the M05-T15 shape, so `score_rewriting` gives gain, harm, and the rollout decision per class.
- **What the review found.** An independent adversarial review fuzzed the resolver with random Questions, Histories, languages, policies, and model answers, with a marker planted in every text. Only `follow_up` in English or Ukrainian was ever sent, at most ten earlier Questions, never an Answer, and the marker never reached a repr, a log record, or an error. It found these problems, each fixed with a test that fails without the fix:
  - A Ukrainian follow-up rewritten with the English words of an earlier English Question was used, because M05-T15 lets any typed word keep its alphabet. Now a follow-up's rewrite is checked again as if only the Question and the carried entities were typed.
  - A History holding something other than text, such as `None` or one string instead of a tuple, crashed with an untyped error or was split into letters. Now it is refused with `InvalidRewriteRequest` before anything is sent.
  - An unsearchable newest Question, such as one too long, was skipped, so an older topic took its place. Now the stretch sent stops there.
  - `compare_resolution` paired cases by position without checking the dataset came from its loader. Now a mismatched dataset is refused with `DatasetRunRefused`.
  - A legal case ID ending in `-resolved` could collide with a resolved copy and take its ranks. Now copies end in `+resolved`, which no case ID may contain.
  - One limit stays: an entity search syntax would misread, such as `x_or_y`, can never be kept, so its follow-ups are always searched as typed.
- **Where the code lives.** Two new modules, `answering/follow_up.py` and `answering/follow_up_evaluation.py`, and their tests. No existing code changes; `docs/architecture.md` gets two edits.
- **Known limits.** No live OpenAI run is possible here, so the measured numbers come from the fake rewriter, which only joins the newest earlier Question to the follow-up. For these three follow-ups the resolver sends exactly what M05-T15 sent, so its live gain still applies. An entity that search syntax would misread, such as `x_or_y`, can never be kept, so its follow-ups always fall back. Nothing in the answer flow calls `resolve_question` yet, and the search text is not stored with the turn.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 5 checks the predictions.

Create `docs/evidence/M06-T12-resolve-follow-up-questions.md`:

```markdown
# M06-T12 — Resolve follow-up Questions evidence

## Invariant

`resolve_question` sends a Question to the rewrite model only when
`classify_question` calls it `follow_up`, the policy switches `follow_up`
on, and its stated language is English or Ukrainian. It sends the Question
and at most the ten newest earlier Questions of the reader's History, never
an Answer or a Chunk. It uses a rewrite only when every new word is in the
stated language and the rewrite keeps the Question's words to keep and the
identifiers and acronyms of the newest earlier Question; otherwise the
search text is the Question as typed. The `ResolvedQuestion` records both
texts and how the search text was chosen. Reprs, log records, errors, and
reports hold no Question or rewrite text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.follow_up"`
fails with `ModuleNotFoundError`.

## Predictions

- Which rewrite classes reach the model when the policy switches on every rewritable class: ...
- What happens when a Ukrainian follow-up comes back rewritten with the English words of an earlier English Question: ...
- With a rewriter that only joins the newest earlier Question to the follow-up, whether every follow-up case ranks better, and what the rollout decision is: ...
- How many unit tests fail if the resolver passes the policy on without narrowing it to `follow_up`: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 5 can prove wrong.

### Step 2 — Coding step: the resolver

This step writes the module that decides whether a Question is resolved and what search text to use.

**In plain words:** Given a Question, its stated language, and the reader's History, it collects the newest earlier Questions and lets the existing rewriter try a rewrite, but only for a follow-up in English or Ukrainian. If a rewrite comes back, it checks it again: no words from another language, and the codes and acronyms of the earlier Question still there. It returns the text to search with and a record of how it was chosen.

| Part | What it holds or does |
|---|---|
| `FOLLOW_UP_ONLY` | The policy with only `follow_up` switched on |
| `ResolvedQuestion` | Both texts, the class, the outcome, the fallback, the rewriter, the usage, the count of earlier Questions, the carried entities |
| `earlier_questions` | The newest stretch of the History's Questions, at most `MAX_HISTORY`, stopping at one that cannot be searched |
| `resolve_question` | Narrows the policy, calls `rewrite_question`, re-checks a rewritten follow-up with `checked_rewrite`, logs once |
| `_entities` | The identifiers and acronyms of a Question, found by classifying it as of no language |

Create `src/knowledge_service/answering/follow_up.py`:

```python
"""Follow-up resolution: one standalone search text for a follow-up Question.

"And abroad?" names its subject only through the turns before it, and
search reads one text. `resolve_question` takes the reader's History
(`answering.history`) and, only for a Question that `classify_question`
calls a follow-up, asks a `RewriteModel` for a standalone search text such
as "What is the daily allowance abroad?". `rewrite_question` (M05-T15)
sends the request and checks the rewrite; this module decides what may be
sent, adds the follow-up's own check, and records how the text was chosen.

What resolution promises:

- **Only follow-ups.** Whatever classes a `RewritePolicy` switches on, only
  `follow_up` is sent. A Question of another class, one with no earlier
  Question in its History, or one whose policy leaves `follow_up` off is
  searched as typed, and nothing is sent.
- **Only the reader's Questions.** The model sees the Question and the
  earlier Questions of the History, oldest first: never an Answer, a
  Chunk, or a Document. History comes from `load_history`, so they are
  the reader's own. At most `MAX_HISTORY` of the newest are sent, and
  never past one that could not be searched, such as one longer than a
  search text may be: the stretch sent always ends at the newest Question.
- **The language kept.** A Question is sent only in a language whose
  alphabet can be checked, English or Ukrainian. Every word of the
  rewrite that is not a word of the Question or an entity carried over
  must be in that alphabet, so an English earlier Question cannot turn a
  Ukrainian follow-up into an English search. A Question stated as mixed
  or unknown is searched as typed.
- **The entities kept.** A rewrite must keep the Question's words to keep
  (identifiers, acronyms, words in the other alphabet) and the
  identifiers and acronyms of the newest earlier Question, the one a
  follow-up most often continues: "How do I fix it?" after "What does
  error EXP-2041 mean?" must keep `EXP-2041`. A rewrite that drops one is
  not used. An entity search syntax would misread, such as `x_or_y`, can
  never be kept, so its follow-ups are always searched as typed.
- **Traceable.** A `ResolvedQuestion` holds the Question as typed, the
  search text, the class, the outcome, the fallback, the rewriter, the
  usage, and how many earlier Questions were sent. Texts stay out of its
  repr; one log record holds counts and names only.
- **Never half a rewrite.** On any failure the search text is the
  Question as typed, and the usage of a billed answer is kept.
"""

import logging
from dataclasses import dataclass, field, replace
from uuid import UUID

from knowledge_service.answering.history import History, HistoryTurn
from knowledge_service.contracts import Usage
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewrite,
    InvalidRewriteRequest,
    RewriteModel,
    RewriteOutcome,
    RewritePolicy,
    RewriterIdentity,
    checked_rewrite,
    rewrite_question,
)
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    InvalidQuestion,
    RewriteClass,
    classify_question,
)

logger = logging.getLogger(__name__)

# The only class resolution may send.
FOLLOW_UP_ONLY = RewritePolicy(frozenset({RewriteClass.FOLLOW_UP}))
# The languages whose alphabet `checked_rewrite` can check.
_CHECKED_LANGUAGES = frozenset({Language.ENGLISH, Language.UKRAINIAN})


@dataclass(frozen=True, slots=True)
class ResolvedQuestion:
    """The text to search with, and how it was chosen.

    `search_text` is the checked rewrite when `outcome` is `rewritten`, and
    the Question as typed otherwise. `carried` holds the entities of the
    newest earlier Question that the rewrite had to keep.
    """

    question: str = field(repr=False)
    search_text: str = field(repr=False)
    language: Language
    rewrite_class: RewriteClass
    outcome: RewriteOutcome
    fallback: str | None
    rewriter: RewriterIdentity
    usage: Usage | None
    earlier_questions: int
    carried: tuple[str, ...] = field(repr=False)

    @property
    def resolved(self) -> bool:
        """True when the search text is a rewrite, not the Question as typed."""
        return self.outcome is RewriteOutcome.REWRITTEN


def earlier_questions(history: History) -> tuple[str, ...]:
    """The newest stretch of the History's Questions a rewrite may use.

    Summarized Questions come before whole turns, oldest first. Taken from
    the newest back, at most `MAX_HISTORY`, and stopping at the first that
    could not be searched, so no older topic takes the newest one's place.
    Raises `InvalidRewriteRequest` for a History that is not one.
    """
    if (
        type(history) is not History
        or type(history.earlier_questions) is not tuple
        or type(history.turns) is not tuple
        or not all(type(turn) is HistoryTurn for turn in history.turns)
    ):
        raise InvalidRewriteRequest("history must be a History")
    questions = [
        *history.earlier_questions,
        *(turn.question for turn in history.turns),
    ]
    if not all(type(question) is str for question in questions):
        raise InvalidRewriteRequest("history must be a History")
    newest: list[str] = []
    for question in reversed(questions):
        if len(newest) == MAX_HISTORY or not _searchable(question):
            break
        newest.append(question)
    return tuple(reversed(newest))


async def resolve_question(
    model: RewriteModel,
    question: str,
    *,
    language: Language,
    history: History,
    policy: RewritePolicy,
    request_id: UUID,
    timeout_seconds: float,
) -> ResolvedQuestion:
    """Resolve a follow-up into a standalone search text, or keep the Question.

    Raises `InvalidQuestion` for a Question or language `classify_question`
    refuses, and `InvalidRewriteRequest` for a History or policy of the
    wrong type or a bad timeout; both before anything is sent.
    """
    if type(language) is not Language:
        raise InvalidQuestion("language must be a Language")
    if type(policy) is not RewritePolicy:
        raise InvalidRewriteRequest("policy must be a RewritePolicy")
    earlier = earlier_questions(history)
    switched_on = (
        policy.classes & FOLLOW_UP_ONLY.classes
        if language in _CHECKED_LANGUAGES
        else frozenset[RewriteClass]()
    )
    result = await rewrite_question(
        model,
        question,
        language=language,
        history=earlier,
        policy=RewritePolicy(switched_on),
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    carried: tuple[str, ...] = ()
    if result.plan.rewrite_class is RewriteClass.FOLLOW_UP:
        carried = _entities(earlier[-1])
    search_text, outcome, fallback = result.search_text, result.outcome, result.fallback
    if outcome is RewriteOutcome.REWRITTEN:
        try:
            # Checked again as if only the Question and the carried
            # entities were typed: earlier words in another alphabet, or
            # identifiers of older Questions, may not come along.
            checked_rewrite(
                search_text,
                replace(result.plan, preserve=(*result.plan.preserve, *carried)),
                question=question,
                history=carried,
            )
        except InvalidRewrite:
            search_text, outcome = question, RewriteOutcome.FALLBACK
            fallback = InvalidRewrite.__name__
    resolved = ResolvedQuestion(
        question=question,
        search_text=search_text,
        language=language,
        rewrite_class=result.plan.rewrite_class,
        outcome=outcome,
        fallback=fallback,
        rewriter=result.identity,
        usage=result.usage,
        earlier_questions=len(earlier),
        carried=carried,
    )
    _log(request_id, resolved)
    return resolved


def _searchable(question: str) -> bool:
    """True for text `classify_question` accepts as an earlier Question."""
    if not question.strip() or len(question) > MAX_QUERY_CHARACTERS:
        return False
    try:
        question.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _entities(question: str) -> tuple[str, ...]:
    """The identifiers and acronyms of a Question, as typed.

    Classified as of no language, no alphabet counts as foreign, so only
    identifiers and acronyms are words to keep.
    """
    return classify_question(question, language=Language.UNKNOWN).preserve


def _log(request_id: UUID, resolved: ResolvedQuestion) -> None:
    """Record how the search text was chosen: names and counts, no text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": resolved.rewriter.provider,
        "model": resolved.rewriter.model,
        "rewrite_class": str(resolved.rewrite_class),
        "outcome": str(resolved.outcome),
        "fallback": resolved.fallback,
        "earlier_questions": resolved.earlier_questions,
        "carried": len(resolved.carried),
    }
    if resolved.usage is not None:
        fields["input_tokens"] = resolved.usage.input_tokens
        fields["output_tokens"] = resolved.usage.output_tokens
    logger.info("follow-up resolved", extra={"follow_up": fields})
```

The second check reuses `checked_rewrite` from M05-T15 instead of new rules. It passes the carried entities as the only "earlier" text, so only they may keep their own alphabet, and adds them to the words a rewrite must keep.

Check:

```bash
uv run python -c "import knowledge_service.answering.follow_up"
uv run pyright src/knowledge_service/answering/follow_up.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 3 — Coding step: the measurement

This step writes the comparison of search with and without resolution.

**In plain words:** For each test case it creates a Conversation for the evaluation reader, stores the case's earlier Questions as turns, reads them back as History, and resolves the case's Question. Then it loads the test documents once and searches every case as typed and, when it was resolved, again with the resolved text. It returns the ranks of both searches in the format M05-T15 already scores and prints.

| Part | What it does |
|---|---|
| `compare_resolution` | Refuses a dataset not from its loader, resolves every case, runs one fused search of both versions, pairs the results |
| `render_resolution` | The M05-T15 report, saying that only `follow_up` was switched on |
| `_resolve` | One case: store the turns, `load_history`, `resolve_question`; a bug becomes `CaseFailed` naming only the error type |
| `RESOLVED_SUFFIX` | `+resolved`, which no dataset case ID can contain, so a resolved copy never takes another case's place |

Create `src/knowledge_service/answering/follow_up_evaluation.py`:

```python
"""Retrieval with and without follow-up resolution, on stored Conversations.

`compare_resolution` replays each case of the bilingual rewrite dataset
(M05-T15) as a Conversation of the evaluation reader: the case's earlier
Questions are stored as turns, `load_history` reads them back, and
`resolve_question` resolves the case's Question with only `follow_up`
switched on. Then the corpus is ingested and embedded once, and every case
is searched as typed and, when it was resolved, again with its search
text, both by dense and lexical search fused.

The result is a `RewriteComparison`, so `score_rewriting`
(`retrieval.rewrite_evaluation`) gives the gain, the harm, and the rollout
decision per class, and `render_resolution` reports them by case ID, with
no Question or rewrite in them.
The earlier turns are stored with a fixed failed reply, because only their
Questions reach the rewriter.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    resolve_question,
)
from knowledge_service.answering.history import load_history
from knowledge_service.contracts import Usage
from knowledge_service.conversations import ConversationLimits, MessageOutcome
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.pricing import ModelPrice, estimate_cost
from knowledge_service.retrieval.evaluation_dataset import (
    DEFAULT_LIMIT,
    READER,
    CaseFailed,
    DatasetRunRefused,
    RewriteCaseOutcome,
    RewriteComparison,
    RewriteDataset,
    compare_branches,
)
from knowledge_service.retrieval.query_rewriting import RewriteModel
from knowledge_service.retrieval.rewrite_cases import RewriteCase
from knowledge_service.retrieval.rewrite_evaluation import (
    render_rewrite_comparison,
    score_rewriting,
)

# The reply stored after each earlier Question; History withholds it.
EARLIER_REPLY = "No answer was kept for this evaluation turn."
HISTORY_TOKENS = 4_000
RESOLVE_TIMEOUT_SECONDS = 30.0
# A dataset case ID is lowercase words and hyphens, so none ends like this.
RESOLVED_SUFFIX = "+resolved"

_SWITCHED_ON = "- Every rewritable class was switched on for this run."
_RESOLVED_ON = (
    "- Only follow_up was switched on; each case was resolved through its"
    " stored History."
)
_REQUEST_IDS = UUID("6f0c2b4e-31d5-4a8e-9c71-5e2d0b7a9f12")


async def compare_resolution(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    rewriter: RewriteModel,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> RewriteComparison:
    """Resolve every case through its stored History, then search both ways.

    Raises like `compare_branches`; `DatasetRunRefused` before anything
    is written when `rewrite` did not come from its loader; `CaseFailed`
    when resolving a case raises, naming only error types, or when the
    rewriter changes its identity during the run.
    """
    # Each case is searched with one Question and resolved with the same.
    if type(rewrite) is not RewriteDataset or [
        (case.case_id, case.question, case.question_language)
        for case in rewrite.dataset.cases
    ] != [(case.case_id, case.question, case.language) for case in rewrite.labelled]:
        raise DatasetRunRefused("the rewrite dataset must come from its loader")
    identity = rewriter.identity
    resolved: list[ResolvedQuestion] = []
    for label in rewrite.labelled:
        result = await _resolve(runtime, label, rewriter, observed_at)
        if result.rewriter != identity:
            raise CaseFailed(f"case {label.case_id}: the rewriter changed its identity")
        resolved.append(result)
    typed = rewrite.dataset.cases
    again = tuple(
        replace(
            case,
            case_id=f"{case.case_id}{RESOLVED_SUFFIX}",
            question=result.search_text,
        )
        for case, result in zip(typed, resolved, strict=True)
        if result.resolved
    )
    combined = replace(rewrite.dataset, cases=(*typed, *again))
    reports = await compare_branches(
        runtime, combined, corpus, observed_at=observed_at, model=model, limit=limit
    )
    fused = {outcome.case_id: outcome for outcome in reports[-1].outcomes}
    cases: list[RewriteCaseOutcome] = []
    for case, result in zip(typed, resolved, strict=True):
        before = fused[case.case_id]
        after = before
        if result.resolved:
            after = replace(
                fused[f"{case.case_id}{RESOLVED_SUFFIX}"], case_id=case.case_id
            )
        cases.append(
            RewriteCaseOutcome(
                case_id=case.case_id,
                rewrite_class=result.rewrite_class,
                outcome=result.outcome,
                fallback=result.fallback,
                before=before,
                after=after,
            )
        )
    usages = tuple(result.usage for result in resolved if result.usage is not None)
    return RewriteComparison(
        name=rewrite.dataset.name,
        version=rewrite.dataset.version,
        limit=limit,
        embedding=model.identity,
        rewriter=identity,
        cases=tuple(cases),
        usages=usages,
        cost_usd=_cost(usages, price),
    )


def render_resolution(comparison: RewriteComparison) -> str:
    """The comparison as Markdown: gain per class, every case, cost, rollout."""
    return render_rewrite_comparison(comparison, score_rewriting(comparison)).replace(
        _SWITCHED_ON, _RESOLVED_ON
    )


async def _resolve(
    runtime: DatabaseRuntime,
    label: RewriteCase,
    rewriter: RewriteModel,
    observed_at: datetime,
) -> ResolvedQuestion:
    """Store the case's earlier turns, read its History, resolve its Question."""
    limits = ConversationLimits()
    resolved: ResolvedQuestion | None = None
    failure = "no result"
    try:
        async with runtime.transaction() as connection:
            started = await start_conversation(
                connection, owner_id=READER.user_id, now=observed_at, limits=limits
            )
            for question in label.history:
                await add_turn(
                    connection,
                    owner_id=READER.user_id,
                    conversation_id=started.conversation_id,
                    question=question,
                    reply=EARLIER_REPLY,
                    outcome=MessageOutcome.FAILED,
                    references=(),
                    now=observed_at,
                    limits=limits,
                )
            history = await load_history(
                connection,
                READER,
                conversation_id=started.conversation_id,
                now=observed_at,
                budget_tokens=HISTORY_TOKENS,
            )
        if history is not None:
            resolved = await resolve_question(
                rewriter,
                label.question,
                language=label.language,
                history=history,
                policy=FOLLOW_UP_ONLY,
                request_id=uuid5(_REQUEST_IDS, f"resolve {label.case_id}"),
                timeout_seconds=RESOLVE_TIMEOUT_SECONDS,
            )
    except Exception as error:
        # A bug or a broken request; its message may quote a Question.
        failure = type(error).__name__
    # Raised outside the `except`, so no linked error carries a text.
    if resolved is None:
        raise CaseFailed(f"case {label.case_id}: resolving raised {failure}")
    return resolved


def _cost(usages: tuple[Usage, ...], price: ModelPrice | None) -> Decimal | None:
    """The estimated cost of every billed answer; None if any is unknown."""
    if price is None:
        return None
    total = Decimal(0)
    for usage in usages:
        cost = estimate_cost(usage, {price.price_id: price})
        if cost is None:
            return None
        total += cost
    return total
```

Check:

```bash
uv run pyright src/knowledge_service/answering/follow_up_evaluation.py
```

Expected: 0 errors.

### Step 4 — Coding step: tests

This step proves each promise, first with a fake rewriter in memory and then on real PostgreSQL.

**In plain words:** The unit tests give the resolver Questions of every class and fake rewrites, good and bad, and check what was sent and what is searched. A property test feeds it random rewrites and checks that a used rewrite always keeps the entities. The integration tests store the bilingual cases as Conversations, run the comparison, and check that only follow-ups were sent, that resolution finds a Chunk the follow-up alone misses, and that the rollout guard refuses a resolution that harms a case.

| Test | What it proves |
|---|---|
| `test_a_follow_up_is_resolved_with_the_earlier_questions` | Success, with the earlier Questions sent |
| `test_a_ukrainian_follow_up_is_resolved_in_ukrainian` | Success in Ukrainian |
| `test_only_a_follow_up_is_ever_sent` | Structured rewriting only for classified follow-ups |
| `test_a_rewrite_that_drops_an_entity_of_the_earlier_question_is_not_used` | Entities kept: the highest-risk edge |
| `test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used` | Language kept in a bilingual Conversation |
| `test_any_text_the_model_returns_is_used_only_when_it_keeps_the_entities` | Property test over random rewrites |
| `test_only_questions_are_sent_never_an_answer` | No Answer text leaves for the rewriter |
| `test_a_bad_request_is_refused_before_anything_is_sent` | Typed failure: `InvalidQuestion` or `InvalidRewriteRequest` |
| `test_no_question_or_rewrite_reaches_a_repr_or_a_log_record` | No planted marker in telemetry |
| `test_only_follow_ups_are_resolved_through_their_stored_history` | The real path: stored turns, History, resolver |
| `test_resolution_is_measured_case_by_case_and_guards_the_rollout` | With and without resolution, bilingual cases |

Create `tests/test_follow_up.py`:

```python
"""Follow-up resolution: only follow-ups, language and entities kept, traced."""
# ruff: noqa: RUF001

import logging
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    earlier_questions,
    resolve_question,
)
from knowledge_service.answering.history import History, HistoryTurn
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewriteRequest,
    RewriteOutcome,
    RewritePolicy,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    REWRITABLE,
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    question_words,
)
from rewriting_fakes import FakeRewriteModel

REQUEST = UUID("00000000-0000-0000-0000-0000000000a1")
EN = Language.ENGLISH
UK = Language.UKRAINIAN
MARKER = "PLANTED-MARKER-7719"
EVERYTHING = RewritePolicy(REWRITABLE)


def history(*questions: str, answers: tuple[str | None, ...] = ()) -> History:
    """Every Question as a whole turn, with its Answer if one is given."""
    turns = tuple(
        HistoryTurn(
            question=question,
            answer=answers[n] if n < len(answers) else None,
            references=(),
        )
        for n, question in enumerate(questions)
    )
    return History(earlier_questions=(), turns=turns, tokens=0, left_out=0, withheld=0)


def says(text: object) -> FakeRewriteModel:
    def answer(question: str, plan: RewritePlan, history: tuple[str, ...]) -> object:
        return text

    return FakeRewriteModel(answer=answer)


async def resolve(
    model: FakeRewriteModel,
    question: str,
    earlier: History,
    *,
    language: Language = EN,
    policy: RewritePolicy = FOLLOW_UP_ONLY,
) -> ResolvedQuestion:
    return await resolve_question(
        model,
        question,
        language=language,
        history=earlier,
        policy=policy,
        request_id=REQUEST,
        timeout_seconds=5.0,
    )


ALLOWANCE = "How much is the daily allowance for business trips?"
ABROAD = "What is the daily allowance for business trips abroad?"


# --- What is resolved -----------------------------------------------------------------


async def test_a_follow_up_is_resolved_with_the_earlier_questions() -> None:
    model = says(ABROAD)

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == ABROAD
    assert resolved.question == "And abroad?"
    assert resolved.resolved
    assert resolved.rewrite_class is RewriteClass.FOLLOW_UP
    assert resolved.outcome is RewriteOutcome.REWRITTEN
    assert resolved.earlier_questions == 1
    assert resolved.usage is not None
    assert [(q, h) for q, _, h in model.calls] == [("And abroad?", (ALLOWANCE,))]


async def test_a_ukrainian_follow_up_is_resolved_in_ukrainian() -> None:
    model = says("Які добові у відрядженні за кордоном?")

    resolved = await resolve(
        model, "А за кордоном?", history("Які добові у відрядженні?"), language=UK
    )

    assert resolved.search_text == "Які добові у відрядженні за кордоном?"
    assert resolved.language is UK


@pytest.mark.parametrize(
    ("question", "rewrite_class"),
    [
        ("What is the VPN used for in the office?", RewriteClass.ACRONYM),
        ("Як налаштувати Outlook на ноутбуці вдома?", RewriteClass.CROSS_LANGUAGE),
        ("What does error EXP-2041 mean for my report?", RewriteClass.EXACT_IDENTIFIER),
        ("Who approves expense reports in the company?", RewriteClass.SELF_CONTAINED),
    ],
    ids=["acronym", "cross-language", "identifier", "self-contained"],
)
async def test_only_a_follow_up_is_ever_sent(
    question: str, rewrite_class: RewriteClass
) -> None:
    model = FakeRewriteModel()
    language = UK if question.startswith("Як") else EN

    resolved = await resolve(
        model, question, history(ALLOWANCE), language=language, policy=EVERYTHING
    )

    assert model.calls == []
    assert resolved.rewrite_class is rewrite_class
    assert resolved.search_text == question
    assert not resolved.resolved


async def test_a_short_question_with_no_history_is_not_a_follow_up() -> None:
    model = FakeRewriteModel()

    resolved = await resolve(model, "And abroad?", history())

    assert model.calls == []
    assert resolved.rewrite_class is RewriteClass.SELF_CONTAINED


async def test_a_follow_up_is_not_sent_when_the_policy_leaves_it_off() -> None:
    model = FakeRewriteModel()
    acronyms = RewritePolicy(frozenset({RewriteClass.ACRONYM}))

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE), policy=acronyms)

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


@pytest.mark.parametrize("language", [Language.MIXED, Language.UNKNOWN])
async def test_a_follow_up_in_a_language_that_cannot_be_checked_is_not_sent(
    language: Language,
) -> None:
    model = FakeRewriteModel()

    resolved = await resolve(
        model, "And abroad?", history(ALLOWANCE), language=language
    )

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


# --- What is sent ---------------------------------------------------------------------


async def test_only_questions_are_sent_never_an_answer() -> None:
    model = says(ABROAD)
    earlier = history(ALLOWANCE, answers=(f"It is 30 EUR. {MARKER}",))

    await resolve(model, "And abroad?", earlier)

    assert all(MARKER not in repr(call) for call in model.calls)


async def test_summarized_questions_come_first_and_only_the_newest_are_sent() -> None:
    model = says(ABROAD)
    questions = [f"Question number {n} about leave?" for n in range(14)]
    earlier = History(
        earlier_questions=tuple(questions[:7]),
        turns=history(*questions[7:]).turns,
        tokens=0,
        left_out=0,
        withheld=0,
    )

    await resolve(model, "And abroad?", earlier)

    assert model.calls[0][2] == tuple(questions[-MAX_HISTORY:])


async def test_an_earlier_question_too_long_to_search_is_left_out() -> None:
    long_question = "leave " * (MAX_QUERY_CHARACTERS // 6 + 1)

    assert earlier_questions(history(long_question, ALLOWANCE)) == (ALLOWANCE,)


# --- What a rewrite must keep ---------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "question", "rewrite", "carried"),
    [
        (
            "What does error EXP-2041 mean?",
            "How do I fix it?",
            "How do I fix the expense report error?",
            ("EXP-2041",),
        ),
        (
            "How do I set up the VPN on a laptop?",
            "And on phones?",
            "How do I set up a secure connection on phones?",
            ("VPN",),
        ),
    ],
)
async def test_a_rewrite_that_drops_an_entity_of_the_earlier_question_is_not_used(
    earlier: str, question: str, rewrite: str, carried: tuple[str, ...]
) -> None:
    resolved = await resolve(says(rewrite), question, history(earlier))

    assert resolved.search_text == question
    assert resolved.outcome is RewriteOutcome.FALLBACK
    assert resolved.fallback == "InvalidRewrite"
    assert resolved.carried == carried
    assert resolved.usage is not None


async def test_a_rewrite_that_keeps_the_entity_is_used() -> None:
    rewrite = "How do I fix error EXP-2041?"

    resolved = await resolve(
        says(rewrite), "How do I fix it?", history("What does error EXP-2041 mean?")
    )

    assert resolved.search_text == rewrite
    assert resolved.carried == ("EXP-2041",)


async def test_a_ukrainian_follow_up_rewritten_in_english_is_not_used() -> None:
    resolved = await resolve(
        says("Daily allowance abroad"),
        "А за кордоном?",
        history("Які добові у відрядженні?"),
        language=UK,
    )

    assert resolved.search_text == "А за кордоном?"
    assert resolved.fallback == "InvalidRewrite"


async def test_a_failing_rewriter_keeps_the_question() -> None:
    model = FakeRewriteModel(error=RewriterUnavailable())

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == "And abroad?"
    assert resolved.fallback == "RewriterUnavailable"
    assert resolved.usage is None


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=120))
async def test_any_text_the_model_returns_is_used_only_when_it_keeps_the_entities(
    text: str,
) -> None:
    earlier = history("What does error EXP-2041 mean with the VPN?")

    resolved = await resolve(says(text), "How do I fix it?", earlier)

    if resolved.resolved:
        words = question_words(resolved.search_text)
        assert {"EXP-2041", "VPN"} <= set(words)
    else:
        assert resolved.search_text == "How do I fix it?"


# --- Typed refusals and quiet records -------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"history": (ALLOWANCE,)}, InvalidRewriteRequest),
        ({"policy": frozenset({RewriteClass.FOLLOW_UP})}, InvalidRewriteRequest),
        ({"language": "en"}, InvalidQuestion),
        ({"question": "x" * (MAX_QUERY_CHARACTERS + 1)}, InvalidQuestion),
        ({"question": "   "}, InvalidQuestion),
        ({"timeout_seconds": 0}, InvalidRewriteRequest),
    ],
)
async def test_a_bad_request_is_refused_before_anything_is_sent(
    fields: dict[str, Any], error: type[Exception]
) -> None:
    model = FakeRewriteModel()
    request: dict[str, Any] = {
        "question": "And abroad?",
        "language": EN,
        "history": history(ALLOWANCE),
        "policy": FOLLOW_UP_ONLY,
        "request_id": REQUEST,
        "timeout_seconds": 5.0,
    } | fields

    with pytest.raises(error):
        await resolve_question(model, **request)

    assert model.calls == []


async def test_no_question_or_rewrite_reaches_a_repr_or_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    earlier = history(f"What does {MARKER} mean?")

    resolved = await resolve(
        says(f"How do I fix {MARKER}?"), "How do I fix it?", earlier
    )

    assert MARKER not in repr(resolved)
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)
    assert [r.getMessage() for r in caplog.records].count("follow-up resolved") == 1


def plan_of(model: FakeRewriteModel) -> RewritePlan:
    return model.calls[0][1]


async def test_the_model_is_told_the_follow_up_class_and_the_stated_language() -> None:
    model = says(ABROAD)

    await resolve(model, "And abroad?", history(ALLOWANCE))

    assert plan_of(model).rewrite_class is RewriteClass.FOLLOW_UP
    assert plan_of(model).language is EN


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "language", "earlier", "rewrite"),
    [
        (
            "А за кордоном?",
            UK,
            "How much is the daily allowance for business trips abroad?",
            "How much is the daily allowance for business trips abroad",
        ),
        (
            "And abroad?",
            EN,
            "Які добові у відрядженні за кордоном?",
            "Які добові у відрядженні за кордоном",
        ),
    ],
    ids=["uk-after-english", "en-after-ukrainian"],
)
async def test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used(
    question: str, language: Language, earlier: str, rewrite: str
) -> None:
    resolved = await resolve(
        says(rewrite), question, history(earlier), language=language
    )

    assert resolved.search_text == question
    assert resolved.fallback == "InvalidRewrite"


@pytest.mark.parametrize(
    "fields",
    [
        {"earlier_questions": None},
        {"earlier_questions": (None,)},
        {"earlier_questions": "What is VPN"},
        {"turns": ("And abroad?",)},
    ],
    ids=["no-questions", "not-text", "one-string", "not-turns"],
)
async def test_a_history_that_is_not_one_is_refused_before_anything_is_sent(
    fields: dict[str, Any],
) -> None:
    model = FakeRewriteModel()
    broken = History(
        earlier_questions=fields.get("earlier_questions", ()),
        turns=fields.get("turns", ()),
        tokens=0,
        left_out=0,
        withheld=0,
    )

    with pytest.raises(InvalidRewriteRequest):
        await resolve(model, "And abroad?", broken)

    assert model.calls == []


async def test_the_questions_sent_always_end_at_the_newest_one() -> None:
    model = says("How do I fix error EXP-2041?")
    newest = "error EXP-9999 " * (MAX_QUERY_CHARACTERS // 15 + 1)

    resolved = await resolve(
        model, "How do I fix it?", history("What does error EXP-2041 mean?", newest)
    )

    assert model.calls == []
    assert resolved.earlier_questions == 0
    assert resolved.search_text == "How do I fix it?"
```

Create `tests/test_follow_up_integration.py`:

```python
"""Follow-up resolution on real PostgreSQL: stored History, retrieval both ways."""

import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.answering.follow_up_evaluation import (
    compare_resolution,
    render_resolution,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    REWRITE_RETRIEVAL_FILE,
    CaseFailed,
    DatasetRunRefused,
    RewriteDataset,
    load_rewrite_dataset,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteOutcome,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    load_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import RewriteClass
from knowledge_service.retrieval.rewrite_evaluation import Rollout, score_rewriting
from knowledge_service.settings import Settings
from rewriting_fakes import FakeRewriteModel

pytestmark = pytest.mark.integration

OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def rewrite() -> RewriteDataset:
    return load_rewrite_dataset(
        DATASET_DIRECTORY / REWRITE_RETRIEVAL_FILE,
        load_rewrite_cases(REWRITE_CASES_FILE),
    )


async def test_only_follow_ups_are_resolved_through_their_stored_history(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rewriter = FakeRewriteModel()

    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=rewriter,
        price=None,
    )

    follow_ups = [
        label
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    ]
    assert follow_ups
    # Each call holds a follow-up and its earlier Questions, read back from
    # the database through History; never another class, never a Chunk.
    assert [(q, h) for q, _, h in rewriter.calls] == [
        (label.question, label.history) for label in follow_ups
    ]
    assert all(RESTRICTED_MARKER not in repr(call) for call in rewriter.calls)
    for case in comparison.cases:
        if case.rewrite_class is not RewriteClass.FOLLOW_UP:
            assert case.outcome is not RewriteOutcome.REWRITTEN
            assert case.after == case.before
    resolved = [c for c in comparison.cases if c.outcome is RewriteOutcome.REWRITTEN]
    assert {c.case_id for c in resolved} == {label.case_id for label in follow_ups}
    rendered = render_resolution(comparison)
    assert "Only follow_up was switched on" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(label.question in text for label in rewrite.labelled)


async def test_resolution_is_measured_case_by_case_and_guards_the_rollout(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # The fake rewriter only joins the last earlier Question to the follow-up.
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # "And abroad?" alone finds nothing; with its earlier Question it does.
    assert not cases["en-follow-up-abroad"].before.found
    assert cases["en-follow-up-abroad"].after.found
    gains = {g.rewrite_class: g for g in score_rewriting(comparison)}
    follow_up = gains[RewriteClass.FOLLOW_UP]
    assert (follow_up.cases, follow_up.rewritten) == (3, 3)
    # Joining texts moves one Ukrainian case down, so rollout is refused.
    assert follow_up.harmed == ("uk-follow-up-abroad",)
    assert follow_up.rollout is Rollout.HARMS


async def test_a_failing_rewriter_leaves_every_case_as_typed(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(error=RewriterUnavailable()),
        price=None,
    )

    assert all(case.after == case.before for case in comparison.cases)
    assert {
        case.fallback
        for case in comparison.cases
        if case.rewrite_class is RewriteClass.FOLLOW_UP
    } == {"RewriterUnavailable"}
    assert comparison.usages == ()


async def test_a_rewriter_bug_stops_the_run_without_quoting_the_question(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    question = next(
        label.question
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    )

    with pytest.raises(CaseFailed, match="resolving raised RuntimeError") as caught:
        await compare_resolution(
            runtime,
            rewrite,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(error=RuntimeError(question)),
            price=None,
        )

    assert question not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# --- Review regressions ---------------------------------------------------------------


async def test_a_dataset_not_from_its_loader_is_refused(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    shuffled = replace(
        rewrite,
        dataset=replace(rewrite.dataset, cases=rewrite.dataset.cases[::-1]),
    )

    with pytest.raises(DatasetRunRefused):
        await compare_resolution(
            runtime,
            shuffled,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(),
            price=None,
        )


async def test_a_case_named_like_a_resolved_one_keeps_its_own_outcome(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # A legal case ID that ends the way a resolved copy's ID could.
    taken = "en-follow-up-abroad-resolved"
    victim = "en-en-annual-leave-days"
    renamed = RewriteDataset(
        dataset=replace(
            rewrite.dataset,
            cases=tuple(
                replace(c, case_id=taken) if c.case_id == victim else c
                for c in rewrite.dataset.cases
            ),
        ),
        labelled=tuple(
            replace(c, case_id=taken) if c.case_id == victim else c
            for c in rewrite.labelled
        ),
    )

    comparison = await compare_resolution(
        runtime,
        renamed,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # Searched as typed, the annual leave Question finds nothing in this run.
    assert not cases[taken].before.found
    assert cases[taken].after == cases[taken].before
```

Check:

```bash
uv run pytest tests/test_follow_up.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_follow_up_integration.py -m integration -q
```

Expected: 34 passed, then 6 passed.

### Step 5 — No coding in this step: break it, measure, and decide

This step shows what the narrowed policy protects, reads the measurement, and records what you learned.

**Action 1:** break the resolver on purpose. In `src/knowledge_service/answering/follow_up.py`, in `resolve_question`, replace:

```python
        policy.classes & FOLLOW_UP_ONLY.classes
```

with:

```python
        policy.classes
```

Run:

```bash
uv run pytest tests/test_follow_up.py -q
```

Expected: 2 failed, 32 passed: `test_only_a_follow_up_is_ever_sent` for the acronym and the cross-language Question, which now reach the model because the flag switches their classes on. The identifier and self-contained cases still pass, because `rewrite_question` never sends those classes. Put the line back and run again until all pass.

**Action 2:** read the measurement. The reference run with the fake rewriter, as `render_resolution` prints it, had these rows:

| Class | Cases | Rewritten | MRR as typed | MRR rewritten | Gain | Harmed | Rollout |
|---|---:|---:|---:|---:|---:|---|---|
| follow_up | 3 | 3 | 0.444 | 0.367 | -0.078 | uk-follow-up-abroad | harms |

| Case | Ranks as typed | Ranks rewritten |
|---|---|---|
| en-follow-up-abroad | missed | 10 |
| uk-follow-up-abroad | 1 | 2 |
| uk-follow-up-his | 3 | 2 |

Every other case was not sent and kept its ranks. "And abroad?" alone finds nothing; joined to its earlier Question it finds the right Chunk. But joining moved one Ukrainian case from rank 1 to 2, so `score_rewriting` says `harms` and the class may not be switched on from this run. The fake rewriter does not resolve anything; it only joins two texts, and the dense ranks come from a fake embedding. So this run proves the path and the guard, not the gain. The gain must come from a live run: M05-T15 measured +0.389 MRR for the same three cases with a real model, and the resolver sends it exactly what M05-T15 sent for them.

**Action 3:** under `## Decision`, write:

- why only follow-ups are resolved, whatever the flag switches on;
- why the rewriter sees earlier Questions and never Answers;
- why a follow-up's rewrite is checked again for language and carried entities;
- what the fake measurement shows and what it cannot show, and what a live run must show before `follow_up` is switched on;
- what is left for later: calling `resolve_question` in the answer flow, and storing the search text with the turn;
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 6 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, add the resolver to the `answering` row and to the paragraph about query rewriting:

**1.** Find:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Replace with:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Find:

```markdown
Query rewriting calls OpenAI too. `OpenAIRewriteModel` sends only the
Question, the earlier Questions of its Conversation, the stated language,
the rewrite class, and the words to keep, with `store=False`; never a
Chunk. Nothing rewrites by default: `QUERY_REWRITE_CLASSES` is empty, and
`--compare-rewriting` exits 1 when it switches on a class the measurement
did not allow. The decision and its numbers are recorded in
`docs/evidence/M05-T15-query-rewriting.md`.

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.

The Evaluation module has no package yet. Code that measures retrieval
```

Replace with:

```markdown
Query rewriting calls OpenAI too. `OpenAIRewriteModel` sends only the
Question, the earlier Questions of its Conversation, the stated language,
the rewrite class, and the words to keep, with `store=False`; never a
Chunk. Nothing rewrites by default: `QUERY_REWRITE_CLASSES` is empty, and
`--compare-rewriting` exits 1 when it switches on a class the measurement
did not allow. The decision and its numbers are recorded in
`docs/evidence/M05-T15-query-rewriting.md`. In answering,
`resolve_question` sends only follow-ups, whatever else the flag switches
on, and reads their earlier Questions from the reader's own History
(`docs/evidence/M06-T12-resolve-follow-up-questions.md`).

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.

The Evaluation module has no package yet. Code that measures retrieval
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_follow_up.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2255 passed for `make test-unit`, 34 more than M06-T11, and 376 passed for `make test-integration`, 6 more. If it says there is no test database, start it with `docker start knowledge-service-postgres`.

**Action 3:** complete the evidence file:

- **Design:** what is sent and when, the two extra checks, `ResolvedQuestion`, and how `compare_resolution` measures.
- **Verification:** the commands and counts, the deliberate break, the measurement, and the known limits from "Before you begin".
- **Review:** an independent adversarial review fuzzed the resolver with a planted marker: only follow-ups were sent, never an Answer, and no text leaked. It found five problems, all fixed with tests that failed before the fix: a follow-up rewritten in the language of an earlier Question was used; a malformed History crashed untyped; an unsearchable newest Question let an older topic take its place; a misaligned dataset was measured; a case ID could collide with a resolved copy. One limit is documented: an entity search syntax would misread can never be kept.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T12 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T13, and commit with `m06-t12: resolve follow up questions`.

## Reflection questions

1. What does `resolve_question` hide from the code that will answer a follow-up? List what that code never has to decide about rewriting.
2. Someone suggests sending the earlier Answers to the rewriter too, because they name the subject better. What could leave storage that does not leave now, and which test would you add first?
3. The joining rewriter made one Ukrainian case rank worse. Why can adding the earlier Question's words push the right Chunk down, and what would you look at to find out?
4. A User asks "How do I fix it?" right after a very long pasted error log, and the log was an earlier Question. What does the resolver send, and is that the right choice?
5. Which conversation would you add to the dataset next to try to make resolution harm a case, and what do you expect the rollout decision to be?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] Only a `follow_up` Question in English or Ukrainian is ever sent, whatever the policy switches on.
- [ ] Only the reader's earlier Questions are sent, at most ten, ending at the newest; never an Answer or a Chunk.
- [ ] A rewrite with words from another alphabet, or without the earlier Question's identifiers and acronyms, is not used.
- [ ] Every failure falls back to the Question as typed, and a billed answer keeps its usage.
- [ ] `ResolvedQuestion` records both texts and how the search text was chosen; no text reaches a repr, a log record, an error, or the report.
- [ ] `compare_resolution` measures the bilingual cases with and without resolution, and the rollout guard reads it.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
