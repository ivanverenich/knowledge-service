# M07-T09 — Authorize Document fetches

Task: [M07-T09 — Authorize Document fetches](../curriculum/milestones/M07-identity-and-authorization/M07-T09-authorize-document-fetches.md)

## What you will do

This lesson makes the answer stream read Documents again, as the reader, at the two moments where reading by ID matters: just before the model sees the Evidence, and just before the client gets the Citations. Search already decides who may see what. But between the search and the end of the answer, two things can go wrong: Evidence that is not the reader's can reach the stream through a bug, and a grant can be revoked while the model writes. Today nothing in the stream looks again.

- **Before the model:** every Evidence is reread as the reader. If one is not visible now, the stream answers like an empty search, and the model never sees it, so none of its text can be streamed.
- **Before the Citations:** every cited Chunk is reread with `confirm_citations`, the check written in M06-T05 and never wired in. If one fails, the Answer is withdrawn and ends with the same decision and message as an empty search.
- **Direct-object-reference attacks:** tests hand alice's stream a real Chunk of bob's, and revoke alice's grant while the model writes. Neither reaches her as a Citation, and bob's text never reaches her at all.

| The task asks for | Where it happens |
|---|---|
| Citation-resolution reads with the same current Access Grants | Steps 2 and 3: `CurrentCitations` and the stream's `CitationCheck` |
| Full-document, excerpt, export, and agent-tool reads | None exist yet; "Before you begin" lists what each must do when it is built |
| Tests attempt direct-object-reference attacks using valid IDs from another User | Step 5: `tests/test_citation_confirmation_integration.py` |
| Authorization preserved before data leaves storage; no seeded text | Steps 4 and 5: the raw bytes hold no hidden text; the log holds counts |
| Update only what the change made inaccurate | Step 7: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 5 write code and tests. Steps 1, 6, and 7 do not, though Step 7 edits two documents.

Terms used in this lesson:

- **Direct object reference (DOR):** reading something by its ID, such as a Chunk or a Document. The attack is to use a real ID that belongs to someone else and see whether the system checks who is asking.
- **Citation check:** the stream's second look at the database. `still_visible` checks the Evidence before the model; `confirm` checks the Citations after it.
- **Withdrawn:** an Answer taken back because something it cites is no longer the reader's to see. It ends like an empty search, so the client cannot tell why.
- **Current version:** a Chunk belongs to one version of its Document; a rewritten Document gets new Chunks, so an old Chunk ID no longer counts.
- **Provenance:** where a Citation points: title, heading, link, excerpt. Sent as the database has it at confirmation time, not as the search saw it.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why two checks.** The check after the model protects against change: a Document deleted, rewritten, or taken away while the model wrote. The check before the model protects against Evidence that was never the reader's, through a retrieval bug or a crafted Evidence set. Only the first check existed on paper, and nothing called it.
- **Why the check before the model matters most.** The stream sends the answer's text as the model writes it, so a check at the end can withdraw the Citations but cannot unsend the text. If bob's Chunk reached the model, the model could quote it to alice before any check ran. Rereading the Evidence first means the model only ever sees what the reader may see now.
- **What a revocation mid-answer can and cannot do.** If alice's grant is revoked after the model was asked, the text already streamed was written from Evidence she could see at that moment. The withdrawal replaces it with the empty-search message and sends no Citation, but it cannot unsend those words. The threat model says so.
- **One protocol, two methods.** `CitationCheck` has `still_visible(evidence, reader)` and `confirm(result, reader)`. `CurrentCitations` implements both over PostgreSQL, each in its own short transaction, so no connection is held while the model writes. The stream requires one: `citations=` has no default.
- **The stream checks the check.** A check may only answer yes or no for the Evidence, and for an Answer it may only withdraw it or return the same text citing the same Chunks of the same Documents, Sources, and versions. Anything else ends the stream with an error, never with the check's own Citations.
- **Other reads by ID.** No endpoint reads a whole Document, an excerpt, or an export, and agent tools come in M11-T01. The review listed every function that reads by ID without a reader, such as `load_document_by_id`, `load_chunks`, and `load_conversation`; none is reachable from a request today. Each must read through `eligible_chunks_predicate` with the request's reader when it gets an endpoint.
- **What the review found.** An independent adversarial review tried crafted check replies, subclasses, a check slower than the deadline (the connection was released), DEBUG logs, and 17 mutations of the wiring. The reread itself held. It found these problems, all fixed with tests that failed before:
  - The first version checked only at the end. When bob's Chunk was handed to alice's stream, the Citation was withdrawn but the model's text quoting it had already been sent, and the test missed it because it read the rebuilt answer, not the bytes. Now `still_visible` runs before the model, and the test checks the raw bytes.
  - The documents claimed withdrawn and empty "read the same"; on the wire, a withdrawn answer has text and `checking` events that an empty one lacks. Now they claim the same decision and message, which the test checks.
  - The stream accepted a reply citing the same Evidence under another Document, Source, or version, or a list of Citations. Now it compares each Citation's full identity.
  - Several mutations survived because no test sent an abstention back as a copy, an Answer back as an abstention, a withdrawn reply keeping text or Citations, or a slow check. Each now has a test. One mutation, accepting an `AnswerResult` subclass, still survives `_confirms` but is caught later, because `decide` itself accepts only the exact type.
- **Known limits.** No production entry point builds the stream yet. A check could be any object with the two methods; nothing forces it to be `CurrentCitations`. The finisher judges support against the Evidence as retrieval saw it, which is safe only because a changed version withdraws the Answer first.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 6 checks the last prediction.

Create `docs/evidence/M07-T09-authorize-document-fetches.md`:

```markdown
# M07-T09 — Authorize Document fetches evidence

## Invariant

The answer stream reads Documents again as the request's reader before the
model sees any Evidence and before the client gets any Citation, through
`eligible_chunks_predicate` and each Chunk's current version. Evidence the
reader may not see now makes the stream end like an empty search, so the
model never sees it; a Citation that fails withdraws the Answer, which ends
with the same decision and message as an empty search and sends no
Citation. A valid ID of another User's Chunk never reaches the client as
text, title, link, or Citation, and the log holds counts only.

## Expected failing check

Before the change, `uv run pytest tests/test_citation_confirmation_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_citation_confirmation_integration.py`.

## Predictions

- Which events alice's client receives when a retrieval bug hands her stream a real Chunk of bob's, and whether the model is called:
- What alice's client receives if her grant is revoked while the model writes, and whether the text already streamed is taken back:
- What the stream does when the check returns the Answer citing the same Evidence but under another Document:
- Which tests fail if the stream ignores a "no" from `still_visible`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: rereading Evidence and Citations in PostgreSQL

**In plain words:** this step gives the citation module a second question to answer: "may this reader still see every piece of this Evidence, in its current version?" It uses the same database query that already confirms Citations. It also adds a small class that asks either question in its own short transaction, so the answer stream can call it without holding a connection while the model writes.

| Part | What it does |
|---|---|
| `evidence_still_visible` | Rereads every Evidence's Chunk under the reader's permission check; True only if all are still there, in the same Document, Source, and version; logs counts |
| `_same` | The shared "still this Chunk, Document, and version" test, now used by both checks |
| `CurrentCitations` | `still_visible` and `confirm`, each in its own transaction |

In `src/knowledge_service/answering/current_citations.py`:

**1.** Find:

```python
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

The caller passes a connection for a short transaction of its own; it
must not hold one open while the model writes.
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID
```

Replace with:

```python
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

The caller passes a connection for a short transaction of its own; it
must not hold one open while the model writes. `CurrentCitations` does that
for the answer stream: it opens the transaction only once the model is done.
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID
```

**2.** Find:

```python

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.evidence import (
    EXCERPT_SOURCE_CHARACTERS,
    MAX_EVIDENCE,
    EvidenceId,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents

```

Replace with:

```python

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.evidence import (
    EXCERPT_SOURCE_CHARACTERS,
    MAX_EVIDENCE,
    Evidence,
    EvidenceId,
    EvidenceSet,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents

```

**3.** Find:

```python
            defect=result.defect,
            usage=result.usage,
            context=result.context,
        )
    _log(request_id, cited=len(cited), withdrawn=withdrawn, outcome=result.outcome)
    return result


def current_citations_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.
```

Replace with:

```python
            defect=result.defect,
            usage=result.usage,
            context=result.context,
        )
    _log(request_id, cited=len(cited), withdrawn=withdrawn, outcome=result.outcome)
    return result


async def evidence_still_visible(
    connection: AsyncConnection,
    context: AuthorizationContext,
    evidence: EvidenceSet,
    *,
    request_id: UUID,
) -> bool:
    """Whether the reader may still see every Evidence, in its current version.

    The same query as `confirm_citations`, over every Chunk the Evidence
    holds, before any of it is shown to the model. An empty set is visible.
    Raises `TypeError` for anything but the request's context and a real
    `EvidenceSet`.
    """
    if type(context) is not AuthorizationContext:
        raise TypeError("context must be an AuthorizationContext")
    if type(evidence) is not EvidenceSet:
        raise TypeError("evidence must be an EvidenceSet")
    if not evidence.items:
        return True
    statement = current_citations_statement(
        [item.chunk_id.value for item in evidence.items],
        eligibility=eligible_chunks_predicate(context),
    )
    records = (await connection.execute(statement)).mappings().all()
    rows = {cast(UUID, record["chunk_id"]): dict(record) for record in records}
    hidden = sum(
        not _same(item, rows.get(item.chunk_id.value)) for item in evidence.items
    )
    fields = {
        "request_id": str(request_id),
        "evidence": len(evidence.items),
        "hidden": hidden,
    }
    level = logging.WARNING if hidden else logging.INFO
    logger.log(level, "evidence confirmed", extra={"evidence_check": fields})
    return hidden == 0


def current_citations_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.
```

**4.** Find:

```python
        )
        and type(item.content_version) is ContentVersion
        and type(item.content_version.number) is int
    )


def _current(citation: Citation, row: dict[str, object] | None) -> Citation | None:
    """The Citation rebuilt from its stored row, or None if it is not current."""
    if (
        row is None
        or row["document_id"] != citation.document_id.value
        or row["source_id"] != citation.source_id.value
        or row["content_version"] != citation.content_version.number
    ):
        return None
    return Citation(
        evidence_id=citation.evidence_id,
        chunk_id=ChunkId(cast(UUID, row["chunk_id"])),
        document_id=DocumentId(cast(UUID, row["document_id"])),
        source_id=SourceId(cast(UUID, row["source_id"])),
```

Replace with:

```python
        )
        and type(item.content_version) is ContentVersion
        and type(item.content_version.number) is int
    )


def _same(item: Citation | Evidence, row: dict[str, object] | None) -> bool:
    """Whether a stored row is still this item's Chunk, Document, and version."""
    return (
        row is not None
        and row["document_id"] == item.document_id.value
        and row["source_id"] == item.source_id.value
        and row["content_version"] == item.content_version.number
    )


def _current(citation: Citation, row: dict[str, object] | None) -> Citation | None:
    """The Citation rebuilt from its stored row, or None if it is not current."""
    if row is None or not _same(citation, row):
        return None
    return Citation(
        evidence_id=citation.evidence_id,
        chunk_id=ChunkId(cast(UUID, row["chunk_id"])),
        document_id=DocumentId(cast(UUID, row["document_id"])),
        source_id=SourceId(cast(UUID, row["source_id"])),
```

**5.** Find:

```python
        "cited": cited,
        "withdrawn": withdrawn,
        "outcome": outcome.value,
    }
    level = logging.WARNING if withdrawn else logging.INFO
    logger.log(level, "citations confirmed", extra={"citation_check": fields})
```

Replace with:

```python
        "cited": cited,
        "withdrawn": withdrawn,
        "outcome": outcome.value,
    }
    level = logging.WARNING if withdrawn else logging.INFO
    logger.log(level, "citations confirmed", extra={"citation_check": fields})


class CurrentCitations:
    """The answer stream's `CitationCheck` over PostgreSQL.

    Each call runs in its own short transaction: `still_visible` before the
    model is asked, `confirm` after it has finished writing.
    """

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self._runtime = runtime

    async def still_visible(
        self, evidence: EvidenceSet, *, reader: AuthorizationContext, request_id: UUID
    ) -> bool:
        """Whether the reader may see every Evidence now, in its current version."""
        async with self._runtime.transaction() as connection:
            return await evidence_still_visible(
                connection, reader, evidence, request_id=request_id
            )

    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult:
        """The Answer with current provenance, `WITHDRAWN`, or unchanged."""
        async with self._runtime.transaction() as connection:
            return await confirm_citations(
                connection, reader, result, request_id=request_id
            )
```

**Check:** `uv run pyright src/knowledge_service/answering/current_citations.py` reports `0 errors`.

### Step 3 — Coding step: the stream asks before the model and before the Citations

**In plain words:** this step puts both questions into the answer stream. After search and before the model, it asks whether the reader may still see every piece of Evidence; if not, it answers as if nothing was found. After the model and before the decision, it asks the database to confirm the Citations, and checks that the reply is a real answer to its question. Everything the client then receives comes from what the database says now.

| Change | What it does |
|---|---|
| Module docstring | Two new promises: Evidence as it is now, Citations as they are now |
| `CitationCheck` | The protocol: `still_visible` and `confirm` |
| `_Plan.reader` | The stream remembers whom it answers |
| `AnswerStreamer(..., citations=...)` | A required check |
| `open` | Calls `still_visible` under the deadline; anything but `True` or `False` is refused; `False` becomes an empty search |
| `_finished` | Calls `confirm` under the deadline, checks the reply with `_confirms`, and passes the confirmed result on |
| `_confirms`, `_identity` | A non-answer comes back unchanged; an Answer comes back withdrawn, or with the same text citing the same Chunks of the same Documents, Sources, and versions |

In `src/knowledge_service/answering/streaming.py`:

**1.** Find:

```python
  `AnswerFinisher` decides. The client keeps the text only if the decision
  is `answer`, and the streamed text is then exactly the checked text: a
  missing end is sent, and any other difference abstains.
- **Citations after the decision.** They are sent only once the decision
  is `answer`, so the title of a Document behind an Answer the policy
  rejects never leaves the server.
- **The usage once billed.** When the model's reply arrived, its usage is
  sent before the terminal event, also before an `error`, and also for a
  reply too long to be an Answer.
- **Always a terminal event.** A failure after the first byte, also an
  event the writer refuses, ends the stream with an `error` event.
- **Cancelled cleanly.** When the client goes away, the stream is
```

Replace with:

```python
  `AnswerFinisher` decides. The client keeps the text only if the decision
  is `answer`, and the streamed text is then exactly the checked text: a
  missing end is sent, and any other difference abstains.
- **Citations after the decision.** They are sent only once the decision
  is `answer`, so the title of a Document behind an Answer the policy
  rejects never leaves the server.
- **Evidence as it is now.** Before the model is asked, the
  `CitationCheck` rereads every Evidence as the reader. If one is not
  visible, through a retrieval bug or a change since the search, the
  stream abstains like an empty search and the model never sees it, so
  none of its text can be streamed.
- **Citations as they are now.** Before the decision, the check rereads
  every cited Chunk again. If one fails, the Answer is `WITHDRAWN` and the
  stream ends with the same decision and message as an empty search. Text
  already streamed was written from Evidence the reader could see when
  the model was asked; the abstention replaces it but cannot unsend it.
  Citations are sent with the provenance the check read.
- **The usage once billed.** When the model's reply arrived, its usage is
  sent before the terminal event, also before an `error`, and also for a
  reply too long to be an Answer.
- **Always a terminal event.** A failure after the first byte, also an
  event the writer refuses, ends the stream with an `error` event.
- **Cancelled cleanly.** When the client goes away, the stream is
```

**2.** Find:

```python
    Decision,
    EvidenceSignals,
    Sufficiency,
    assess_evidence,
    decide,
)
from knowledge_service.answering.context_budget import (
    AllocatedContext,
    ContextBudget,
    allocate_context,
)
from knowledge_service.answering.events import AnswerEventWriter, InvalidEventStream
```

Replace with:

```python
    Decision,
    EvidenceSignals,
    Sufficiency,
    assess_evidence,
    decide,
)
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.context_budget import (
    AllocatedContext,
    ContextBudget,
    allocate_context,
)
from knowledge_service.answering.events import AnswerEventWriter, InvalidEventStream
```

**3.** Find:

```python
    """The checked result and the policy's decision about it."""

    result: AnswerResult
    decision: AbstentionDecision


class AnswerFinisher(Protocol):
    """Decides whether a checked result is shown to the client."""

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer: ...
```

Replace with:

```python
    """The checked result and the policy's decision about it."""

    result: AnswerResult
    decision: AbstentionDecision


class CitationCheck(Protocol):
    """Rereads Evidence and Citations against the Documents as they are now.

    `CurrentCitations` is the implementation. `still_visible` says whether
    the reader may see every Evidence; `confirm` returns an Answer with
    current provenance, the Answer withdrawn, or anything else unchanged.
    """

    async def still_visible(
        self, evidence: EvidenceSet, *, reader: AuthorizationContext, request_id: UUID
    ) -> bool: ...

    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult: ...


class AnswerFinisher(Protocol):
    """Decides whether a checked result is shown to the client."""

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer: ...
```

**4.** Find:

```python

@dataclass(frozen=True, slots=True)
class _Plan:
    """What `open` settled before the first byte."""

    request_id: UUID
    deadline: float
    language: Language
    sufficiency: Sufficiency
    context: AllocatedContext | None


```

Replace with:

```python

@dataclass(frozen=True, slots=True)
class _Plan:
    """What `open` settled before the first byte."""

    request_id: UUID
    reader: AuthorizationContext
    deadline: float
    language: Language
    sufficiency: Sufficiency
    context: AllocatedContext | None


```

**5.** Find:

```python
        source: EvidenceSource,
        model: StreamingAnswerModel,
        finisher: AnswerFinisher,
        policy: AbstentionPolicy,
        budget: ContextBudget,
        *,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._source = source
        self._model = model
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(
```

Replace with:

```python
        source: EvidenceSource,
        model: StreamingAnswerModel,
        finisher: AnswerFinisher,
        policy: AbstentionPolicy,
        budget: ContextBudget,
        *,
        citations: CitationCheck,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._source = source
        self._model = model
        self._citations = citations
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(
```

**6.** Find:

```python
            async with _before(deadline):
                found = await self._source.find(
                    question.text, reader=reader, request_id=request_id
                )
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
                else None
            )
```

Replace with:

```python
            async with _before(deadline):
                found = await self._source.find(
                    question.text, reader=reader, request_id=request_id
                )
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            async with _before(deadline):
                visible = await self._citations.still_visible(
                    found.evidence, reader=reader, request_id=request_id
                )
            if type(visible) is not bool:
                raise InvalidAnswerRequest("the citation check must answer yes or no")
            if not visible:
                # Something the reader may not see: answer as if nothing was found.
                found = FoundEvidence(EvidenceSet(()), EvidenceSignals(0, None, None))
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
                else None
            )
```

**7.** Find:

```python
            span.outcome = "failed"
            span.audit_outcome = failed.audit_outcome
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            deadline=deadline,
            language=_language(question.text),
            sufficiency=sufficiency,
            context=context,
        )
        return AnswerStream(self._events(plan, span), span)
```

Replace with:

```python
            span.outcome = "failed"
            span.audit_outcome = failed.audit_outcome
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            reader=reader,
            deadline=deadline,
            language=_language(question.text),
            sufficiency=sufficiency,
            context=context,
        )
        return AnswerStream(self._events(plan, span), span)
```

**8.** Find:

```python
        streamed = "".join(span.text)
        if result.outcome is AnswerOutcome.ANSWERED and not result.text.startswith(
            streamed
        ):
            # The client holds text that is not the checked Answer.
            return FinishedAnswer(result, _abstain(self._policy))
        async with _before(plan.deadline):
            finished = await self._finisher.finish(
                result, context.evidence, request_id=plan.request_id
            )
        if (
            type(finished) is not FinishedAnswer
```

Replace with:

```python
        streamed = "".join(span.text)
        if result.outcome is AnswerOutcome.ANSWERED and not result.text.startswith(
            streamed
        ):
            # The client holds text that is not the checked Answer.
            return FinishedAnswer(result, _abstain(self._policy))
        async with _before(plan.deadline):
            confirmed = await self._citations.confirm(
                result, reader=plan.reader, request_id=plan.request_id
            )
        if not _confirms(result, confirmed):
            raise InvalidAnswerRequest("the citation check must answer this result")
        result = confirmed
        async with _before(plan.deadline):
            finished = await self._finisher.finish(
                result, context.evidence, request_id=plan.request_id
            )
        if (
            type(finished) is not FinishedAnswer
```

**9.** Find:

```python
        return None
    if not well_formed_reply(part):
        raise ChatModelMalformedResponse("the model streamed something else")
    return cast(ModelReply, part)


def _abstain(policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN,
        reason=AbstentionReason.ANSWER_FAILED,
        policy_version=policy.version,
    )
```

Replace with:

```python
        return None
    if not well_formed_reply(part):
        raise ChatModelMalformedResponse("the model streamed something else")
    return cast(ModelReply, part)


def _confirms(result: AnswerResult, confirmed: object) -> bool:
    """Whether `confirmed` is a check of `result`, not some other result.

    Anything but an Answer comes back unchanged. An Answer comes back
    withdrawn, or with the same text and the same Evidence cited in the
    same order, only its provenance read again.
    """
    if type(confirmed) is not AnswerResult:
        return False
    if result.outcome is not AnswerOutcome.ANSWERED:
        return confirmed is result
    if confirmed.outcome is AnswerOutcome.WITHDRAWN:
        return confirmed.citations == () and confirmed.text == ""
    if (
        confirmed.outcome is not AnswerOutcome.ANSWERED
        or confirmed.text != result.text
        or type(confirmed.citations) is not tuple
        or len(confirmed.citations) != len(result.citations)
    ):
        return False
    # Only the provenance may be read again; the same Evidence must be
    # cited, as the same Chunk of the same Document, Source, and version.
    return all(
        type(new) is Citation and _identity(new) == _identity(old)
        for new, old in zip(confirmed.citations, result.citations, strict=True)
    )


def _identity(citation: Citation) -> tuple[object, ...]:
    return (
        citation.evidence_id,
        citation.chunk_id,
        citation.document_id,
        citation.source_id,
        citation.content_version,
    )


def _abstain(policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN,
        reason=AbstentionReason.ANSWER_FAILED,
        policy_version=policy.version,
    )
```

**Check:** `uv run pyright src` reports `0 errors`. The stream tests now fail with `TypeError` about the missing `citations` argument; Step 4 fixes them.

### Step 4 — Coding step: the stream tests and a stand-in check

**In plain words:** this step gives the older stream tests a stand-in check that sees everything and keeps every answer, so they pass again. The stand-in can also be told to hide Evidence, withdraw, rename, fail, slow down, or answer with something else, and new tests use that to prove the stream reacts correctly to each.

In `tests/test_streaming.py`:

**1.** Find:

```python
"""Streaming an Answer: deltas, the decision, the deadline, and cancellation."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import httpx
import pytest
from hypothesis import given, settings
```

Replace with:

```python
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
```

**2.** Find:

```python
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelIdentity, ModelReply
from knowledge_service.answering.openai_answering import AnswerSettings
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    FinishedAnswer,
    FoundEvidence,
    PolicyFinisher,
    _Span,  # pyright: ignore[reportPrivateUsage]
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.model import ChatModelError, ChatModelRateLimited
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000050")
QUESTION = Question(text="How many days of annual leave do I get?")
EVIDENCE = build_evidence(
```

Replace with:

```python
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
    CitationCheck,
    FinishedAnswer,
    FoundEvidence,
    PolicyFinisher,
    _Span,  # pyright: ignore[reportPrivateUsage]
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.identifiers import ChunkId, DocumentId
from knowledge_service.model import ChatModelError, ChatModelRateLimited
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000050")
QUESTION = Question(text="How many days of annual leave do I get?")
EVIDENCE = build_evidence(
```

**3.** Find:

```python
            # A provider's close awaits too, such as closing its connection.
            if self.close_delay:
                await asyncio.sleep(self.close_delay)
            self.closed = True


class SlowFinisher:
    """Never decides."""

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
```

Replace with:

```python
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


class SlowFinisher:
    """Never decides."""

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
```

**4.** Find:

```python
    model: Model,
    *,
    source: Source | None = None,
    finisher: AnswerFinisher | None = None,
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
        model,
        finisher or PolicyFinisher(LexicalSupportJudge(), policy, timeout_seconds=5),
        policy,
        AnswerSettings().budget,
        timeout_seconds=timeout_seconds,
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
```

Replace with:

```python
    model: Model,
    *,
    source: Source | None = None,
    finisher: AnswerFinisher | None = None,
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
    citations: CitationCheck | None = None,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
        model,
        finisher or PolicyFinisher(LexicalSupportJudge(), policy, timeout_seconds=5),
        policy,
        AnswerSettings().budget,
        citations=citations or Citations(),
        timeout_seconds=timeout_seconds,
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
```

**5.** Find:

```python

    sent = await chunks(streamer(Model(), source=Source(empty)), question)

    assert rebuilt(sent).message == (
        "Я не знайшов документів, які відповідають на це запитання."
    )


# --- The streamed text is the checked text -------------------------------------


async def test_a_reply_in_another_key_order_sends_the_text_at_the_end() -> None:
```

Replace with:

```python

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
```

**Check:**

```bash
uv run pytest tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py tests/test_app_authentication.py -q
```

Expected: `190 passed`.

### Step 5 — Coding step: direct-object-reference attacks over HTTP

**In plain words:** these tests run the whole stream over HTTP with the real database check. One hands alice's stream a real Chunk of bob's, as a retrieval bug would, and checks that the model is never asked and no byte of bob's file reaches her. Another revokes alice's grant while the model writes and checks the answer is withdrawn. A third checks that a withdrawn answer and an empty search end the same way.

Create `tests/test_citation_confirmation_integration.py`:

```python
"""The answer stream confirms its Citations against PostgreSQL, as the reader.

Each test runs the whole stream over HTTP with the real `CurrentCitations`.
A model cites the first Evidence it is shown; before it replies, a test can
change the database, as a Synchronization Run might while the model writes.
Evidence the reader may not see must never reach the model, so none of its
text is streamed; a Citation the reader may no longer see withdraws the
Answer, which ends with the same decision and message as an empty search.
"""

import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.abstention import AbstentionReason, EvidenceSignals
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import UserId
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_streaming import QUESTION, USAGE, Model, streamer
from test_vector_evidence_integration import FixedModel
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
    plain_text,
)

BOB = AuthorizationContext(user_id=UserId(UUID(int=0xB0B)))
READERS_GROUP = next(iter(READER.group_ids))
LATER = OBSERVED_AT.replace(hour=OBSERVED_AT.hour + 1)
EVIDENCE_ID = re.compile(r'"id": "(E[0-9a-f]{8})"')


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


class CitingModel(Model):
    """Answers with the text of a file and cites the first Evidence shown.

    `before_reply` runs after the text has streamed and before the reply
    completes, as a change committed while the model is still writing.
    """

    def __init__(
        self,
        name: str,
        before_reply: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self.answer = f"The {name} policy."
        self.before_reply = before_reply

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        found = EVIDENCE_ID.search(prompt.input)
        assert found is not None
        final = (
            '{"answer": "' + self.answer + '", "citations": ["' + found[1] + '"],'
            ' "abstained": false, "reason": null}'
        )
        yield final[:-10]
        if self.before_reply is not None:
            await self.before_reply()
        yield final[-10:]
        yield ModelReply(text=final, refused=False, usage=USAGE)


class FoundFor:
    """An Evidence source that returns what another reader's search found.

    It stands for a retrieval bug, or IDs taken from someone else's answer:
    the Evidence is real, but not the requesting reader's to see.
    """

    def __init__(self, runtime: DatabaseRuntime, owner: AuthorizationContext) -> None:
        self.runtime = runtime
        self.owner = owner

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            found = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=self.owner, limit=1
            )
        return FoundEvidence(
            build_evidence(found.candidates), EvidenceSignals(1, None, None)
        )


async def ask(runtime: DatabaseRuntime, model: Model, source: Any) -> StreamedAnswer:
    answer, _ = await ask_raw(runtime, model, source)
    return answer


async def ask_raw(
    runtime: DatabaseRuntime, model: Model, source: Any
) -> tuple[StreamedAnswer, bytes]:
    """The answer as a client rebuilds it, and every byte that was sent."""
    app = create_app(
        streamer=streamer(model, source=source, citations=CurrentCitations(runtime)),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )
    assert response.status_code == 200
    reader = AnswerEventReader()
    reader.feed(response.content)
    return reader.close(), response.content


def vector_source(runtime: DatabaseRuntime) -> VectorEvidenceSource:
    return VectorEvidenceSource(runtime, FixedModel(), embed_timeout_seconds=5)


@pytest.mark.integration
async def test_a_citation_the_reader_may_see_is_sent_with_its_provenance(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    answer = await ask(runtime, CitingModel("roadmap.txt"), vector_source(runtime))

    assert answer.outcome is StreamOutcome.ANSWERED
    (citation,) = answer.citations
    assert citation.title == "roadmap.txt"


@pytest.mark.integration
async def test_another_users_chunk_is_never_cited_to_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    # Bob's Document, found as bob, handed to alice's stream: a valid ID
    # of another User.
    await a_document(
        runtime,
        "SECRET-bob.txt",
        readers=[Subject.user(BOB.user_id)],
        vector=[1.0, 0.0, 0.0],
    )

    model = CitingModel("SECRET-bob.txt")

    answer, sent = await ask_raw(runtime, model, FoundFor(runtime, BOB))

    assert answer.outcome is StreamOutcome.ABSTAINED
    assert answer.reason is AbstentionReason.NO_EVIDENCE
    assert model.calls == 0
    # Not even streamed text: the model was never shown bob's Chunk.
    assert b"SECRET" not in sent


@pytest.mark.integration
async def test_a_grant_revoked_while_the_model_writes_withdraws_the_answer(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    answer = await ask(
        runtime, CitingModel("roadmap.txt", before_reply=revoke), vector_source(runtime)
    )

    assert answer.outcome is StreamOutcome.ABSTAINED
    assert answer.reason is AbstentionReason.NO_EVIDENCE
    assert answer.citations == ()
    assert answer.text == ""


@pytest.mark.integration
async def test_withdrawn_and_empty_end_with_the_same_decision_and_message(
    runtime: DatabaseRuntime,
) -> None:
    # The text streamed before the withdrawal stays sent; the end is the same.
    await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    withdrawn = await ask(
        runtime, CitingModel("roadmap.txt", before_reply=revoke), vector_source(runtime)
    )
    empty = await ask(runtime, CitingModel("roadmap.txt"), vector_source(runtime))

    assert (withdrawn.outcome, withdrawn.reason, withdrawn.message) == (
        empty.outcome,
        empty.reason,
        empty.message,
    )
```

`ask_raw` returns every byte the client received, not only the answer a client would rebuild. The review showed why: a rebuilt answer drops text that was streamed and then withdrawn, so a test reading only the rebuilt answer cannot see a leak in the stream.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_citation_confirmation_integration.py -q
```

Expected: `4 passed`.

### Step 6 — No coding in this step: break it on purpose

This step makes the stream ignore a "no" before the model, and shows the leak the review found.

**Action 1:** in `open` in `src/knowledge_service/answering/streaming.py`, delete these lines:

```python
            if not visible:
                # Something the reader may not see: answer as if nothing was found.
                found = FoundEvidence(EvidenceSet(()), EvidenceSignals(0, None, None))
```

**Action 2:** run

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_streaming.py tests/test_citation_confirmation_integration.py -q
```

Expected: `2 failed, 64 passed`:

- `test_evidence_the_reader_may_not_see_never_reaches_the_model`
- `test_another_users_chunk_is_never_cited_to_the_reader`

The second is the leak: the check at the end still withdraws bob's Citation, but the model has already streamed "The SECRET-bob.txt policy." to alice.

**Action 3:** put the lines back and run the command again: `66 passed`.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 7 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model named `M07-T09` as an owner of invariant 7 and said nothing calls `confirm_citations`; both change.

**Action 1:** update two documents.

In `docs/architecture.md`:

Find:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

Replace with:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

In `docs/threat-model.md`:

**1.** Find:

```markdown
### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt | Retrieved-instruction hardening (M07-T13) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | None in any served path yet: `confirm_citations` exists and is tested, but nothing calls it | Citation reads with current grants (M07-T09) |
| An old Answer in History repeats revoked content | History keeps an Answer only while everything it cited is current and visible | None |
| A follow-up rewrite or History carries another User's text into a prompt | History is read only from the reader's own Conversation; the rewriter gets only that History's earlier Questions, never an Answer | None |
| The provider keeps Questions and Evidence | The grounded path asks it not to (`store=False`); `POST /v1/answer` does not | Versioned Question route (M09-T01) |
| A Question floods the model to run up cost | On the grounded path, context and token budgets and one deadline per stream; `POST /v1/answer` sets no output limit | Identity-aware rate limits (M09-T10) |

### Agents
```

Replace with:

```markdown
### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt | Retrieved-instruction hardening (M07-T13) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | The answer stream passes every Answer through its `CitationCheck`, `CurrentCitations` over PostgreSQL, before the decision: `confirm_citations` rereads each cited Chunk under the same permission check and its current version, and one that fails withdraws the Answer, which ends with the same decision and message as an empty search; Citations carry the provenance read then. Text streamed before a revocation was written from Evidence the reader could see when the model was asked; the abstention replaces it but cannot unsend it | None |
| A valid ID of another User's Chunk reaches the stream, through a retrieval bug or a crafted Evidence set | Before the model is asked, `still_visible` rereads every Evidence as the request's reader; one the reader may not see makes the stream abstain like an empty search, so the model never sees it and none of its text, title, or link is sent; the stream refuses a check that answers with other text, or cites another Chunk, Document, Source, or version | Read-only agent tools with the reader (M11-T01) |
| An old Answer in History repeats revoked content | History keeps an Answer only while everything it cited is current and visible | None |
| A follow-up rewrite or History carries another User's text into a prompt | History is read only from the reader's own Conversation; the rewriter gets only that History's earlier Questions, never an Answer | None |
| The provider keeps Questions and Evidence | The grounded path asks it not to (`store=False`); `POST /v1/answer` does not | Versioned Question route (M09-T01) |
| A Question floods the model to run up cost | On the grounded path, context and token budgets and one deadline per stream; `POST /v1/answer` sets no output limit | Identity-aware rate limits (M09-T10) |

### Agents
```

**2.** Find:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Replace with:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
uv run pytest tests/test_streaming.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2784 passed for `make test-unit`, 22 more than M07-T08, and 625 passed for `make test-integration`, 4 more.

**Action 3:** complete the evidence file:

- **Design:** the two checks and where they run, what `CurrentCitations` reads, what `_confirms` accepts, and what the client sees in each case.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why a check before the model as well as after, why withdrawn ends like empty, and why the stream checks the check; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T09 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T10, and commit with `m07-t09: authorize document fetches`.

## Reflection questions

1. The stream calls `still_visible` and `confirm`. What does it no longer need to know about grants, versions, or SQL to keep a Citation honest?
2. Someone wants to save a database query by dropping `still_visible`, since `confirm` checks everything at the end anyway. Using the deliberate break, explain what alice would see.
3. A grant is revoked two seconds into a ten-second answer. What has alice already received, what does she receive at the end, and why can no server design fully undo the first part?
4. Why should a withdrawn answer end with the same message as an empty search, rather than "this document was withdrawn"?
5. An export endpoint is added next year: "download every Document cited in my last Conversation". Write the two checks it must make, with which reader, before it returns a byte.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `evidence_still_visible` and `CurrentCitations` reread Evidence and Citations as the reader, in short transactions of their own.
- [ ] The stream requires a `CitationCheck`, asks it before the model and before the decision, and refuses a reply that is not a real answer to its question.
- [ ] The stream tests pass with the stand-in check (190).
- [ ] The direct-object-reference tests pass against PostgreSQL (4), and the raw bytes hold no hidden text.
- [ ] You ran the deliberate break, saw the 2 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe both checks and their limits, and the trace names the new tests.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
