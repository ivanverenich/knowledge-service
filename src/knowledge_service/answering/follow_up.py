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
