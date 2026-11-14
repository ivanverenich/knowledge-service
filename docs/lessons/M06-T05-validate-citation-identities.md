# M06-T05 — Validate Citation identities

Source task: [M06-T05](../curriculum/milestones/M06-answers-and-conversations/M06-T05-validate-citation-identities.md)

## What you will do

This lesson makes sure no Citation reaches a user unless it is still true at the moment the Answer is sent. M06-T04 already checks that the model cites only Evidence it was shown. But the model takes seconds to write, and in that time a Document can be deleted, rewritten, renamed, or taken away from the reader. So after generation, the server asks the database once more about every cited Chunk.

- A model that lists the same Evidence ID twice now gets one Citation, instead of losing the whole paid Answer.
- `confirm_citations(connection, context, result, ...)` rechecks every Citation of an Answer against the stored Documents, with the same permission check retrieval uses.
- If even one Citation is no longer allowed or current, the whole Answer is withdrawn: a new outcome, `WITHDRAWN`, with no text and no Citations.
- A confirmed Citation is rebuilt from the stored rows, so a renamed Document shows its new title, and nothing a caller put into a Citation reaches the client.

| The task asks for | Where it happens |
|---|---|
| Accept only Evidence IDs supplied to generation | Already true since M06-T04: `checked_answer` resolves IDs against the Evidence in the prompt; Step 3 adds the second check, against storage |
| Deduplicate citations | Step 2: `resolve_citations` cites a repeated ID once, at its first position |
| Map them to current Document provenance server-side | Step 3: `confirm_citations` rebuilds each Citation from its stored rows |
| Adversarial tests: invented or unauthorized IDs never reach the client | Step 4: forged Citations, revoked access, another reader, deleted and rewritten Documents |
| Deterministic tests: success, highest-risk edge, typed failure | Step 4: an unchanged Answer passes; a Chunk the reader may not see; `TypeError` for a forged result |
| No seeded text in telemetry | Step 4: a planted marker in a title never reaches a log record, a repr, or an error |

This lesson includes coding: Steps 2 to 4 write code. Steps 1, 5, and 6 do not. The new tests in Step 4 need the PostgreSQL test database.

Terms used in this lesson:

- **Provenance:** where a Citation comes from: the Document's title, the heading path inside it, the source link, and a short excerpt. A client shows these next to the Answer.
- **Current:** the cited Chunk still exists, in the same version of its Document, and the Document is not deleted. A new version of a Document gets new Chunks with new IDs, so an old Chunk ID simply finds nothing.
- **Eligible:** the reader may see the Chunk right now: its Document is available, and one of its Access Grants names everyone, this User, or one of this User's Groups. Retrieval uses the same rule.
- **Tombstoned:** a Document marked deleted. Its Chunks stay in the database until a background job purges them, so "the Chunk exists" is not enough; the Document must also be available.
- **Withdrawn:** the outcome of an Answer that was written, and paid for, but may not be shown, because a Citation stopped being allowed or current.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `answering/citations.py` turns the model's Evidence IDs into Citations built from server-side Evidence. `answering/generation.py` gives `generate_answer`, which returns an `AnswerResult` with one outcome. `persistence/access.py` has `eligible_chunks_predicate`, the SQL condition every search uses to show only Chunks the reader may see.
- **Why repeated IDs are now merged.** M06-T01 refused a list that named the same ID twice, and left the choice to this lesson. A repeat cannot invent or relabel a source: it points at the same Evidence again. In M06-T04 a refusal turns into `INVALID`, so the user would lose an Answer the provider already billed. OpenAI's strict mode cannot forbid repeats in the schema, so they will happen. So `resolve_citations` now keeps the first one and drops the rest. Reading stored Citations back with `read_citations` still refuses a repeat, because the server never writes one; there, a repeat means the data was changed.
- **Why check again after generation.** Retrieval's permission check is correct at the moment of the search. The model then works for seconds, up to the 30-second deadline. A Synchronization Run can revoke access, delete, or rewrite a Document in that window. Without a second check, the client would get a Citation, and an Answer written from it, that the reader may no longer see.
- **Why the whole Answer is withdrawn.** The Answer's text was written from the cited Evidence. Dropping only the bad Citation would still show what that Evidence said, now unsupported. So one failed Citation withdraws everything. The usage and the context report stay, because the call was paid for.
- **Why every failure looks the same.** A revoked grant, a deleted Chunk, a new version, and a tombstoned Document all give `WITHDRAWN`. A client cannot tell them apart, so it cannot learn that a Document it may not see exists, or that it changed.
- **Why rebuild the Citation from the database.** The title can change without a new version: a renamed Document keeps its Chunks. And an `AnswerResult` is a plain object; a bug or a forged object could carry any title or excerpt. Rebuilding every field from the stored rows, except the Evidence ID, means what the client sees is what storage says now.
- **Why a separate short transaction.** The caller passes a connection. It must not hold a database transaction open while the model writes for up to 30 seconds. So retrieval runs in one transaction, generation runs with none, and the confirmation runs in a new, short one. The one gap left is between confirmation and delivery, which no check can close.
- **What the review found.** An adversarial review found no path from real retrieval and generation to a Citation the reader may not see. It found five ways a hand-built or forged result got through, each fixed with a test that fails without the fix:
  - An outcome given as the plain text "answered" skipped the check. Now the outcome must be a real `AnswerOutcome`.
  - A result that was not an Answer kept its Citations unchecked. Now only an Answer may hold Citations.
  - An identifier holding a lying UUID passed the comparison. Now every identifier must hold exactly a UUID.
  - A Chunk ID holding text crashed in the database, with the text in the error message. Now it is a `TypeError`, raised before any SQL.
  - A forged title or excerpt was kept. Now every field except the Evidence ID is rebuilt from storage.
- **Where the code lives.** `answering/current_citations.py`, small changes in `answering/citations.py`, `answering/evidence.py`, and `answering/generation.py`.
- **Known limits.** Nothing calls `confirm_citations` yet; the streaming endpoint in M06-T09 will call it before it sends any Citation. Between confirmation and delivery, a Document can still change. The check confirms identity and permission, not truth: whether each claim is supported by its Evidence is M06-T06.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 5 checks the predictions.

Create `docs/evidence/M06-T05-citation-identities.md`:

```markdown
# M06-T05 — Citation identities evidence

## Invariant

`resolve_citations` accepts only IDs of the request's own Evidence and
cites a repeated ID once, at its first position; `read_citations` still
refuses a repeat. `confirm_citations` returns any outcome other than
`ANSWERED` unchanged and without a query, and refuses with `TypeError`,
before any SQL, a result, outcome, or Citation that is not exactly what
generation builds. For an Answer, one statement selects the cited Chunks
under the retrieval permission check; every Citation must match its
stored Chunk, Document, Source, and content version, in an available
Document the reader may see. Otherwise the whole Answer is `WITHDRAWN`,
with no text, no reason, and no Citations, keeping its usage and context
report. A confirmed Citation is rebuilt from the stored rows, except its
Evidence ID. No log record, repr, or error holds a title, link, or text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.current_citations"`
fails with `ModuleNotFoundError`.

## Predictions

- What a model's reply that cites the same Evidence ID three times became in M06-T04, and what it becomes after this lesson: ...
- What the reader gets when a cited Document was renamed between retrieval and confirmation, and when access to it was revoked: ...
- How many tests fail if the permission check is left out of the confirmation statement, and which kinds of change they cover: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 5 can prove wrong.

### Step 2 — Coding step: merge repeated citations and prepare the new outcome

This step changes three existing files a little, so the new module in Step 3 has what it needs.

**In plain words:** When the model names the same source twice, it now gets one citation instead of losing its whole answer. Reading saved citations back still refuses a repeat, because the server never saves one. The answer gets a new possible outcome, "withdrawn", for an answer that may not be shown. And the length of text an excerpt is cut from becomes shared, so the new module cuts excerpts exactly as retrieval did.

| Change | Why |
|---|---|
| `resolve_citations` keeps the first of repeated IDs | A repeat cannot invent a source; refusing it would throw away a paid Answer |
| `read_citations` still refuses a repeat | The server never writes one, so a repeat in stored data means it was changed |
| `AnswerOutcome.WITHDRAWN` | The outcome `confirm_citations` sets for an Answer that may not be shown |
| `EXCERPT_SOURCE_CHARACTERS` is public | Step 3 rebuilds the excerpt from the same 4,000 characters Evidence used |

**1.** In `src/knowledge_service/answering/citations.py`, update the module docstring. Find:

```python
- **Strict references.** `resolve_citations` takes the model's list of
  IDs as it arrived. It refuses, with `InvalidCitation`, anything that is
  not a list or tuple of plain `str`, an ID that is malformed, an ID not
  in the set, and an ID listed twice. One bad reference rejects the whole
  list; nothing is guessed or dropped.
```

Replace with:

```python
- **Strict references.** `resolve_citations` takes the model's list of
  IDs as it arrived. It refuses, with `InvalidCitation`, anything that is
  not a list or tuple of plain `str`, an ID that is malformed, and an ID
  not in the set. One bad reference rejects the whole list; nothing is
  guessed or dropped.
- **Each source once.** An ID the model lists twice is cited once, at its
  first position. It names the same Evidence again, so it adds nothing a
  user should see, and it is no reason to throw away a paid Answer.
```

Then find:

```python
  same checks, and also refuses a payload whose Document, title, heading,
  link, or excerpt differs from its Evidence, or that has a field too many.
```

Replace with:

```python
  same checks, and also refuses an ID listed twice, since the server
  never writes one, and a payload whose Document, title, heading, link, or
  excerpt differs from its Evidence, or that has a field too many.
```

**2.** In the same file, change `resolve_citations`. Find:

```python
    """Turn the model's Evidence IDs into Citations, or raise `InvalidCitation`."""
```

Replace with:

```python
    """Turn the model's Evidence IDs into Citations, or raise `InvalidCitation`.

    A repeated ID is cited once, at its first position.
    """
```

Then find:

```python
    for index, reference in enumerate(cast(Sequence[object], references)):
        item = _known(evidence, reference, index, seen)
        citations.append(_citation(item))
```

Replace with:

```python
    for index, reference in enumerate(cast(Sequence[object], references)):
        item = _known(evidence, reference, index)
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        citations.append(_citation(item))
```

**3.** In `read_citations`, the duplicate check moves out of `_known` and stays here. Find:

```python
        item = _known(evidence, payload.evidence_id, index, seen)
        citation = _citation(item)
```

Replace with:

```python
        item = _known(evidence, payload.evidence_id, index)
        if item.evidence_id in seen:
            raise InvalidCitation(CitationDefect.DUPLICATE, index)
        seen.add(item.evidence_id)
        citation = _citation(item)
```

**4.** `_known` no longer tracks what was seen. Find:

```python
def _known(
    evidence: EvidenceSet, reference: object, index: int, seen: set[EvidenceId]
) -> Evidence:
    """The Evidence a reference names, if it is well formed, known, and new."""
```

Replace with:

```python
def _known(evidence: EvidenceSet, reference: object, index: int) -> Evidence:
    """The Evidence a reference names, if it is well formed and known."""
```

Then find:

```python
    if item is None:
        raise InvalidCitation(CitationDefect.UNKNOWN, index)
    if evidence_id in seen:
        raise InvalidCitation(CitationDefect.DUPLICATE, index)
    seen.add(evidence_id)
    return item
```

Replace with:

```python
    if item is None:
        raise InvalidCitation(CitationDefect.UNKNOWN, index)
    return item
```

**5.** In `src/knowledge_service/answering/evidence.py`, rename the private `_EXCERPT_SOURCE_CHARACTERS` to `EXCERPT_SOURCE_CHARACTERS` in both places it appears. Find:

```python
_EXCERPT_SOURCE_CHARACTERS = 4_000
```

Replace with:

```python
EXCERPT_SOURCE_CHARACTERS = 4_000
```

Then find:

```python
            excerpt=display_excerpt(text[:_EXCERPT_SOURCE_CHARACTERS]),
```

Replace with:

```python
            excerpt=display_excerpt(text[:EXCERPT_SOURCE_CHARACTERS]),
```

**6.** In `src/knowledge_service/answering/generation.py`, find:

```python
    REFUSED = "refused"
    INVALID = "invalid"
```

Replace with:

```python
    REFUSED = "refused"
    INVALID = "invalid"
    # Set by `confirm_citations` (see `answering.current_citations`).
    WITHDRAWN = "withdrawn"
```

**7.** Update the tests that expected a repeat to be refused. In `tests/test_citations.py`, find:

```python
def test_an_unknown_or_repeated_reference_rejects_the_whole_list() -> None:
    evidence = evidence_set()
    known = ids(evidence)

    with pytest.raises(InvalidCitation) as unknown:
        resolve_citations(evidence, [known[0], absent(evidence)])
    with pytest.raises(InvalidCitation) as repeated:
        resolve_citations(evidence, [known[1], known[0], known[1]])

    assert (unknown.value.defect, unknown.value.index) == (CitationDefect.UNKNOWN, 1)
    assert (repeated.value.defect, repeated.value.index) == (
        CitationDefect.DUPLICATE,
        2,
    )
```

Replace with:

```python
def test_an_unknown_reference_rejects_the_whole_list() -> None:
    evidence = evidence_set()

    with pytest.raises(InvalidCitation) as unknown:
        resolve_citations(evidence, [ids(evidence)[0], absent(evidence)])

    assert (unknown.value.defect, unknown.value.index) == (CitationDefect.UNKNOWN, 1)


def test_a_repeated_reference_is_cited_once_at_its_first_position() -> None:
    evidence = evidence_set()
    first, second, _ = ids(evidence)

    citations = resolve_citations(evidence, [second, first, second, second])

    assert [c.evidence_id.value for c in citations] == [second, first]


def test_a_repeated_reference_still_needs_every_other_reference_valid() -> None:
    evidence = evidence_set()
    first = ids(evidence)[0]

    with pytest.raises(InvalidCitation) as caught:
        resolve_citations(evidence, [first, first, "E1"])

    assert (caught.value.defect, caught.value.index) == (CitationDefect.MALFORMED, 2)
```

Then, at the end of the property test at the bottom of the file, find:

```python
    assert all(evidence.get(c.evidence_id) is not None for c in citations)
    assert len({c.evidence_id for c in citations}) == len(citations)
```

Replace with:

```python
    assert all(evidence.get(c.evidence_id) is not None for c in citations)
    assert len({c.evidence_id for c in citations}) == len(citations)
    # Each listed ID is cited once, in the order it first appears.
    assert [c.evidence_id.value for c in citations] == list(dict.fromkeys(mixed))
```

**8.** In `tests/test_generation.py`, a repeated ID is no longer a broken reply. Delete this line from the list of broken replies:

```python
        (body(citations=[FIRST, FIRST]), AnswerDefect.INVALID_CITATION),
```

Delete this line from its `ids` list:

```python
        "repeated-id",
```

Then find:

```python
async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
```

Replace with:

```python
async def test_a_repeated_citation_is_cited_once() -> None:
    model = FakeAnswerModel(body(citations=[FIRST, SECOND, FIRST]))

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [FIRST, SECOND]


async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
```

Check:

```bash
uv run pytest tests/test_citations.py tests/test_generation.py tests/test_evidence.py -q
```

Expected: 126 passed.

### Step 3 — Coding step: confirm Citations against storage

This step writes the module that stands between a generated Answer and the client.

**In plain words:** First it checks that it was given exactly the kind of answer generation builds; anything else is refused before the database is touched. An outcome that is not an answer passes straight through. For an answer, it asks the database, in one query, which of the cited pieces of text the reader may still see in their current version. If every one is still there, the answer goes on with each citation rebuilt from what is stored now. If even one is missing, the whole answer is withdrawn, keeping only its cost.

| Part | What it holds or does |
|---|---|
| `confirm_citations` | Checks the input, sends one query, and returns the Answer confirmed, or withdrawn |
| `current_citations_statement` | The query: the cited Chunks, joined to their Documents, current version only, under the permission check |
| `_checked`, `_well_formed` | The input rules: 1 to 20 real Citations, identifiers holding exactly UUIDs, each Evidence and Chunk once |
| `_current` | Compares one Citation with its row and rebuilds it from the row, or returns None |
| `_log` | One record per check: the request ID, how many were cited and withdrawn, the outcome |

Create `src/knowledge_service/answering/current_citations.py`:

```python
"""Confirm an Answer's Citations against the Documents as they are now.

Retrieval checks permissions when it ranks. The model then takes seconds
to write the Answer, and in that time a Document can be deleted, rewritten,
renamed, or taken away from the reader. `confirm_citations` runs after
generation and before anything reaches the client, and asks the database
once more about every cited Chunk.

What `confirm_citations` promises:

- **Checked shape first.** The context, the result, its outcome, and every
  Citation must be of exactly the types generation builds, with UUIDs
  inside the identifiers; otherwise `TypeError`, before any SQL. A result
  that is not `ANSWERED` must hold no Citations.
- **Only Answers are confirmed.** Any other outcome is returned unchanged,
  and the database is not asked.
- **Every Citation is still allowed.** One statement selects the cited
  Chunks under `eligible_chunks_predicate`, the same permission check
  retrieval uses, for the same `AuthorizationContext`. A Citation counts
  only when its Chunk still exists in the same Document, Source, and
  content version, the Document is available, and the reader may see it.
- **All or nothing.** If even one Citation fails, the whole Answer is
  withdrawn: the outcome becomes `WITHDRAWN`, with no text, no reason, and
  no Citations. The text was written from that Evidence, so showing it
  without the Citation would still show what the reader may no longer
  see. The usage and the context report are kept, since the call was paid.
- **Provenance from the stored rows.** A confirmed Citation is rebuilt from
  its row: identifiers, version, title, heading path, source link, and
  excerpt, cleaned for display as Evidence is. Only its Evidence ID comes
  from the result. So a renamed Document is cited under its new title,
  and nothing a caller put into a Citation reaches the client.
- **Same answer to every failure.** A deleted Chunk, a new version, a
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

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

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

logger = logging.getLogger(__name__)


async def confirm_citations(
    connection: AsyncConnection,
    context: AuthorizationContext,
    result: AnswerResult,
    *,
    request_id: UUID,
) -> AnswerResult:
    """Return the Answer with current provenance, or withdraw it.

    Raises `TypeError` when `context` or `result` is not what the request
    and generation produce.
    """
    if type(context) is not AuthorizationContext:
        raise TypeError("context must be an AuthorizationContext")
    if type(result) is not AnswerResult or type(result.outcome) is not AnswerOutcome:
        raise TypeError("result must be an AnswerResult")
    if result.outcome is not AnswerOutcome.ANSWERED:
        if result.citations != ():
            raise TypeError("only an Answer holds Citations")
        return result
    cited = _checked(result.citations)
    statement = current_citations_statement(
        [citation.chunk_id.value for citation in cited],
        eligibility=eligible_chunks_predicate(context),
    )
    records = (await connection.execute(statement)).mappings().all()
    rows = {cast(UUID, record["chunk_id"]): dict(record) for record in records}
    confirmed = [
        _current(citation, rows.get(citation.chunk_id.value)) for citation in cited
    ]
    withdrawn = sum(citation is None for citation in confirmed)
    if withdrawn:
        result = AnswerResult(
            outcome=AnswerOutcome.WITHDRAWN,
            text="",
            reason=None,
            citations=(),
            defect=None,
            usage=result.usage,
            context=result.context,
        )
    else:
        result = AnswerResult(
            outcome=result.outcome,
            text=result.text,
            reason=result.reason,
            citations=tuple(cast(list[Citation], confirmed)),
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

    The IDs are one array parameter, however many there are. A Chunk is
    current when its version is its Document's; replacing a version gives
    its Chunks new IDs, so an old ID finds nothing. Only the start of the
    text is read, as much as an excerpt needs.
    """
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            sa.func.left(chunks.c.text, EXCERPT_SOURCE_CHARACTERS).label("opening"),
        )
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunks.c.chunk_id
            == sa.any_(
                sa.bindparam(
                    "chunk_ids", list(chunk_ids), type_=ARRAY(sa.Uuid()), unique=True
                )
            ),
            chunks.c.content_version == documents.c.content_version,
            eligibility,
        )
    )


def _checked(citations: object) -> tuple[Citation, ...]:
    """The result's Citations, if they are exactly what generation builds."""
    if type(citations) is not tuple:
        raise TypeError("Citations must be a tuple")
    items = cast(tuple[object, ...], citations)
    if not 1 <= len(items) <= MAX_EVIDENCE:
        raise TypeError(f"an Answer holds 1 to {MAX_EVIDENCE} Citations")
    if not all(_well_formed(item) for item in items):
        raise TypeError("a Citation is not what generation builds")
    cited = cast(tuple[Citation, ...], items)
    if len({c.chunk_id.value for c in cited}) != len(cited) or len(
        {c.evidence_id.value for c in cited}
    ) != len(cited):
        raise TypeError("an Answer cites each Evidence and Chunk once")
    return cited


def _well_formed(item: object) -> bool:
    """A Citation whose identifiers hold exactly a UUID, a str, or an int."""
    if type(item) is not Citation:
        return False
    identifiers = (
        (item.chunk_id, ChunkId),
        (item.document_id, DocumentId),
        (item.source_id, SourceId),
    )
    return (
        type(item.evidence_id) is EvidenceId
        and type(item.evidence_id.value) is str
        and all(
            type(value) is kind and type(value.value) is UUID
            for value, kind in identifiers
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
        content_version=ContentVersion(cast(int, row["content_version"])),
        title=display_text(cast(str, row["title"])),
        heading_path=_shown(row["heading_path"]),
        source_link=_shown(row["source_anchor"]),
        excerpt=display_excerpt(cast(str, row["opening"])),
    )


def _shown(value: object) -> str | None:
    return None if value is None else display_text(cast(str, value))


def _log(
    request_id: UUID, *, cited: int, withdrawn: int, outcome: AnswerOutcome
) -> None:
    """Record one check: the request, counts, and the outcome. No text."""
    fields = {
        "request_id": str(request_id),
        "cited": cited,
        "withdrawn": withdrawn,
        "outcome": outcome.value,
    }
    level = logging.WARNING if withdrawn else logging.INFO
    logger.log(level, "citations confirmed", extra={"citation_check": fields})
```

Three details matter here. The permission check is `eligible_chunks_predicate`, the same function every search uses, placed in the same WHERE; there is no second copy of the rule to drift apart. The query reads only the first 4,000 characters of each Chunk, which is all the excerpt needs. And the comparison in `_current` is safe because `_well_formed` already made sure each identifier holds a real UUID, not an object that claims to equal anything.

Check:

```bash
uv run python -c "import knowledge_service.answering.current_citations"
uv run pyright src/knowledge_service/answering/current_citations.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 4 — Coding step: unit and integration tests

This step proves that only current, permitted Citations get through, and that nothing else leaks.

**In plain words:** The unit tests need no database. They check that an outcome that is not an answer passes through untouched, and that every kind of broken or forged input is refused before any query. The integration tests use the real database. They publish Documents, answer from real retrieval with a fake model that cites everything it was given, then change the Documents the way a synchronization would and check what is let through.

| Test | What it proves |
|---|---|
| `test_a_current_answer_reaches_the_client_unchanged` | Success: nothing changed, so the Answer and its Citations are unchanged |
| `test_a_renamed_document_is_cited_under_its_current_title` | Current provenance: same Chunk, new title |
| `test_an_answer_citing_a_document_that_changed_is_withdrawn` | Revoked access, new version, tombstoned, purged: each withdraws the whole Answer |
| `test_another_readers_context_cannot_confirm_the_answer` | The check uses the reader's own permissions |
| `test_a_citation_of_a_chunk_the_reader_may_not_see_never_reaches_the_client` | The highest-risk edge: a forged Citation of a private Chunk, with a planted marker |
| `test_a_citation_whose_identity_differs_from_storage_is_withdrawn` | Each identity check on its own: Document, Source, version |
| `test_a_confirmed_citation_is_rebuilt_from_storage` | A forged title, heading, link, or excerpt is replaced by the stored one |
| `test_a_broken_request_is_refused_before_the_database` | Typed failure: 13 broken or forged inputs give `TypeError` before any SQL |
| `test_each_check_logs_counts_only` | One log record with counts; no title |

Create `tests/test_current_citations.py`:

```python
"""Confirming Citations: what is checked before the database is asked."""

from dataclasses import replace
from uuid import UUID

import pytest

from evidence_fakes import MARKER, candidate
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.current_citations import (
    confirm_citations,
    current_citations_statement,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.identifiers import ChunkId, DocumentId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000046")
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
EVIDENCE = build_evidence([candidate(1), candidate(2)])
FIRST, SECOND = (item.evidence_id.value for item in EVIDENCE.items)
REPORT = allocate_context("Leave?", EVIDENCE, ContextBudget(32_000, 1_000, 0)).report


class NoConnection:
    """Fails the test if anything asks the database."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the database was used: {name}")


def result(outcome: AnswerOutcome, citations: tuple[object, ...]) -> AnswerResult:
    return AnswerResult(
        outcome=outcome,
        text="You get 24 days.",
        reason=None,
        citations=citations,  # type: ignore[arg-type]
        defect=None,
        usage=None,
        context=REPORT,
    )


@pytest.mark.parametrize(
    "outcome",
    [o for o in AnswerOutcome if o is not AnswerOutcome.ANSWERED],
)
async def test_an_outcome_that_is_not_an_answer_passes_without_a_query(
    outcome: AnswerOutcome,
) -> None:
    given = result(outcome, ())

    confirmed = await confirm_citations(
        NoConnection(),  # type: ignore[arg-type]
        CONTEXT,
        given,
        request_id=REQUEST_ID,
    )

    assert confirmed is given


CITED = resolve_citations(EVIDENCE, [FIRST, SECOND])


class LyingUUID(UUID):
    """A UUID that claims to equal anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = UUID.__hash__


def answered(*citations: object) -> AnswerResult:
    return result(AnswerOutcome.ANSWERED, citations)


@pytest.mark.parametrize(
    ("context", "given"),
    [
        (None, result(AnswerOutcome.ANSWERED, CITED)),
        (CONTEXT, object()),
        (CONTEXT, result(AnswerOutcome.ANSWERED, ())),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], CITED[0]))),
        (CONTEXT, replace(answered(*CITED), outcome="answered")),
        (CONTEXT, result(AnswerOutcome.ABSTAINED, CITED)),
        (
            CONTEXT,
            answered(replace(CITED[0], document_id=DocumentId(LyingUUID(int=9)))),
        ),
        (CONTEXT, answered(replace(CITED[0], chunk_id=ChunkId(MARKER)))),  # type: ignore[arg-type]
        (CONTEXT, answered(replace(CITED[0], content_version=3))),
        (
            CONTEXT,
            answered(CITED[0], replace(CITED[1], evidence_id=CITED[0].evidence_id)),
        ),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], "E00000001"))),
        (CONTEXT, result(AnswerOutcome.ANSWERED, CITED[:1] * 21)),
        (
            CONTEXT,
            result(
                AnswerOutcome.ANSWERED,
                (CITED[0], replace(CITED[1], chunk_id=CITED[0].chunk_id)),
            ),
        ),
    ],
    ids=[
        "no-context",
        "not-a-result",
        "answer-without-citations",
        "same-citation-twice",
        "outcome-as-text",
        "abstention-with-citations",
        "lying-uuid",
        "text-as-chunk-id",
        "plain-int-version",
        "same-evidence-id-twice",
        "not-a-citation",
        "too-many",
        "same-chunk-twice",
    ],
)
async def test_a_broken_request_is_refused_before_the_database(
    context: object, given: object
) -> None:
    with pytest.raises(TypeError) as caught:
        await confirm_citations(
            NoConnection(),  # type: ignore[arg-type]
            context,  # type: ignore[arg-type]
            given,  # type: ignore[arg-type]
            request_id=REQUEST_ID,
        )

    assert MARKER not in str(caught.value)


def test_the_statement_checks_permission_version_and_ids_in_one_query() -> None:
    statement = current_citations_statement(
        [CITED[0].chunk_id.value], eligibility=eligible_chunks_predicate(CONTEXT)
    )

    sql = str(statement)
    assert sql.count("SELECT") == 2  # the query and the grant check inside it
    assert "chunks.content_version = documents.content_version" in sql
    assert "documents.availability" in sql
    assert "ANY (" in sql
```

Create `tests/test_current_citations_integration.py`:

```python
"""Confirming Citations against the Documents as they are now, in PostgreSQL.

Each test answers from real retrieval, changes the stored Documents the
way a Synchronization Run would, and checks what `confirm_citations`
lets through to the client.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.context_budget import ContextBudget
from knowledge_service.answering.current_citations import confirm_citations
from knowledge_service.answering.evidence import EvidenceId, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelIdentity,
    ModelReply,
    generate_answer,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.contracts import Usage
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, DocumentId, GroupId, SourceId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000056")
OBSERVED_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e2")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e3")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f1")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000045")
BUDGET = ContextBudget(context_tokens=32_000, answer_tokens=1_000, history_tokens=0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=900,
    output_tokens=40,
    total_tokens=940,
)
LEAVE = "Every employee receives 24 days of annual leave."
CARRY = "Unused annual leave carries over for 5 days."

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(name: str, *, title: str, text: str, version: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    *,
    title: str = "Leave policy",
    text: str = LEAVE,
    readers: Sequence[Subject] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text, version=version),
        readers=readers,
        observed_at=observed_at,
    )
    return reconciled.document.document_id


class CitingModel:
    """Answers by citing every Evidence it was given, in prompt order."""

    def __init__(self, answer: str = "You get 24 days of annual leave.") -> None:
        self._answer = answer

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
        schema = prompt.response_schema()
        ids = schema["properties"]["citations"]["items"]["enum"]
        text = json.dumps(
            {
                "answer": self._answer,
                "citations": ids,
                "abstained": False,
                "reason": None,
            }
        )
        return ModelReply(text=text, refused=False, usage=USAGE)


async def answer(
    runtime: DatabaseRuntime, question: str = "annual leave"
) -> AnswerResult:
    """Retrieve, then answer citing everything retrieved."""
    async with runtime.transaction() as connection:
        found = await search_lexical_chunks(
            connection, text=question, context=CONTEXT, limit=10
        )
    result = await generate_answer(
        question,
        build_evidence(found.candidates),
        BUDGET,
        CitingModel(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )
    assert result.outcome is AnswerOutcome.ANSWERED
    return result


async def confirm(
    runtime: DatabaseRuntime,
    result: AnswerResult,
    context: AuthorizationContext = CONTEXT,
) -> AnswerResult:
    async with runtime.transaction() as connection:
        return await confirm_citations(
            connection, context, result, request_id=REQUEST_ID
        )


def assert_withdrawn(confirmed: AnswerResult, original: AnswerResult) -> None:
    assert confirmed.outcome is AnswerOutcome.WITHDRAWN
    assert (confirmed.text, confirmed.reason, confirmed.citations) == ("", None, ())
    assert confirmed.defect is None
    # The call was paid for, and the context report still explains it.
    assert confirmed.usage == original.usage
    assert confirmed.context == original.context


@pytest.mark.integration
async def test_a_current_answer_reaches_the_client_unchanged(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)

    confirmed = await confirm(runtime, result)

    assert len(result.citations) == 2
    assert confirmed == result


@pytest.mark.integration
async def test_a_renamed_document_is_cited_under_its_current_title(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt", title="Leave policy")
    result = await answer(runtime)
    await publish(
        runtime, "leave.txt", title="Annual leave", version="v2", observed_at=LATER
    )

    confirmed = await confirm(runtime, result)

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    (before,) = result.citations
    (after,) = confirmed.citations
    assert (after.chunk_id, after.excerpt) == (before.chunk_id, before.excerpt)
    assert (before.title, after.title) == ("Leave policy", "Annual leave")
    assert confirmed.text == result.text


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime, "leave.txt", readers=(Subject.user(OTHER),), observed_at=LATER
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        text="Every employee receives 26 days of annual leave.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


async def purge(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.text.contains("24 days"))
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone, purge],
    ids=["access-revoked", "new-version", "tombstoned", "chunks-purged"],
)
async def test_an_answer_citing_a_document_that_changed_is_withdrawn(
    runtime: DatabaseRuntime, change: object
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)
    assert len(result.citations) == 2

    await change(runtime)  # type: ignore[operator]
    confirmed = await confirm(runtime, result)

    # One Citation failed, so the whole Answer is withheld, not just it.
    assert_withdrawn(confirmed, result)


@pytest.mark.integration
async def test_another_readers_context_cannot_confirm_the_answer(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)

    confirmed = await confirm(runtime, result, AuthorizationContext(user_id=OTHER))

    assert_withdrawn(confirmed, result)


async def forged(
    runtime: DatabaseRuntime, result: AnswerResult, *, private: DocumentId
) -> tuple[Citation, Citation]:
    """Two hand-made Citations of the private Document's Chunk."""
    async with runtime.transaction() as connection:
        record = (
            await connection.execute(
                sa.select(chunks.c.chunk_id).where(
                    chunks.c.document_id == private.value
                )
            )
        ).one()
    (real,) = result.citations
    secret = ChunkId(record.chunk_id)
    # A fresh Evidence ID each: otherwise the check refuses the repeat first.
    whole = replace(
        real, evidence_id=EvidenceId.new(), chunk_id=secret, document_id=private
    )
    relabelled = replace(real, evidence_id=EvidenceId.new(), chunk_id=secret)
    return whole, relabelled


@pytest.mark.integration
async def test_a_citation_of_a_chunk_the_reader_may_not_see_never_reaches_the_client(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt")
    private = await publish(
        runtime,
        "secret.txt",
        title=MARKER,
        text=f"{MARKER} salary bands.",
        readers=(Subject.user(OTHER),),
    )
    result = await answer(runtime)

    for citation in await forged(runtime, result, private=private):
        sneaked = replace(result, citations=(*result.citations, citation))
        confirmed = await confirm(runtime, sneaked)
        assert_withdrawn(confirmed, sneaked)
        assert MARKER not in repr(confirmed)
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in caplog.records)


@pytest.mark.integration
async def test_each_check_logs_counts_only(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt", title=MARKER)
    result = await answer(runtime)
    await revoke(runtime)

    await confirm(runtime, result)

    (record,) = [r for r in caplog.records if r.message == "citations confirmed"]
    assert record.__dict__["citation_check"] == {
        "request_id": str(REQUEST_ID),
        "cited": 1,
        "withdrawn": 1,
        "outcome": "withdrawn",
    }
    assert MARKER not in caplog.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["document_id", "source_id", "content_version"], ids=str
)
async def test_a_citation_whose_identity_differs_from_storage_is_withdrawn(
    runtime: DatabaseRuntime, field: str
) -> None:
    await publish(runtime, "leave.txt")
    other = await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime, "24 days")
    (real,) = result.citations
    wrong = {
        "document_id": other,
        "source_id": SourceId(UUID(int=99)),
        "content_version": real.content_version.next(),
    }[field]
    relabelled = replace(result, citations=(replace(real, **{field: wrong}),))

    confirmed = await confirm(runtime, relabelled)

    assert_withdrawn(confirmed, relabelled)


@pytest.mark.integration
async def test_a_confirmed_citation_is_rebuilt_from_storage(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)
    (real,) = result.citations
    forged = replace(
        real, title=MARKER, heading_path=MARKER, source_link=MARKER, excerpt=MARKER
    )

    confirmed = await confirm(runtime, replace(result, citations=(forged,)))

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    assert confirmed.citations == (real,)
```

`publish` uses `reconcile_document`, the same function a Synchronization Run uses, so each change in the tests is a real one: new readers, new text, or a new title. `tombstone_missing` marks a Document deleted the way a run does when a file disappears from its Source. `CitingModel` reads the allowed IDs from the prompt's own schema, so it cites exactly what retrieval returned.

Check:

```bash
uv run pytest tests/test_current_citations.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_current_citations_integration.py -m integration -q
```

Expected: 19 passed, then 13 passed. If the second command says there is no test database, start it with `docker start knowledge-service-postgres`.

### Step 5 — No coding in this step: break it and decide

This step shows what stands between a revoked Document and the client, and records what you learned.

**Action 1:** break the check on purpose. In `src/knowledge_service/answering/current_citations.py`, in `current_citations_statement`, delete this line from the `.where(...)` call:

```python
            eligibility,
```

Run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_current_citations.py tests/test_current_citations_integration.py -m "integration or not integration" -q
```

Expected: 6 failed, 26 passed. The failures are the statement test, revoked access, the tombstoned Document, another reader, the forged private Citation, and the log test. A new version and purged Chunks still pass, because their old Chunk IDs find nothing anyway. Without this one line, a reader would get Answers built from Documents they lost access to, and from deleted ones. Put the line back and run again until all 32 pass.

**Action 2:** under `## Decision`, write:

- why a repeated ID is merged by `resolve_citations` but refused by `read_citations`;
- why one failed Citation withdraws the whole Answer instead of only itself;
- why revoked, deleted, and rewritten all look the same to the client;
- what is left for later lessons: calling `confirm_citations` from the streaming endpoint (M06-T09), claim support (M06-T06), and what the user is told about a withdrawn Answer (M06-T07, M06-T13);
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 6 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, update the `answering` row: repeated IDs are now cited once, and `confirm_citations` joins the list. Find:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), and `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget) |
```

Replace with:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), and `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_current_citations.py tests/test_citations.py tests/test_generation.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 1909 passed for `make test-unit` and 342 passed for `make test-integration`: 13 more than M06-T04, all from this lesson.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why repeats are merged; what `confirm_citations` checks and in which order; the query; why the whole Answer is withdrawn; what is rebuilt from storage.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the adversarial review found no path from real retrieval and generation to a Citation the reader may not see; it found five ways a forged result got through, each fixed with a test that failed before the fix: an outcome given as plain text skipped the check; a result that was not an Answer kept its Citations; a lying UUID passed the comparison; a Chunk ID holding text crashed in the database with the text in the error; a forged title or excerpt was kept.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T05 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T06, and commit with `m06-t05: validate citation identities`.

## Reflection questions

1. What does `confirm_citations` hide from the streaming endpoint in M06-T09? List what that code never has to check before it sends a Citation.
2. A later change wants to skip the second query when the Answer took less than one second, since "nothing can change that fast". What could still go wrong, and which test would catch it?
3. When one of five Citations fails, the whole Answer is withdrawn. Is that too harsh for the user who could still have read the other four, or exactly right? Why?
4. A client receives `WITHDRAWN`. Why must it not learn whether the Document was deleted or only hidden from this reader?
5. What forged `AnswerResult` would you build next to try to get a Citation past this check?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] `resolve_citations` cites a repeated ID once; `read_citations` still refuses a repeat.
- [ ] `AnswerOutcome.WITHDRAWN` exists, and `EXCERPT_SOURCE_CHARACTERS` is public.
- [ ] `confirm_citations` refuses a forged or broken result with `TypeError` before any SQL.
- [ ] Outcomes other than an Answer pass through without a query.
- [ ] One query checks every cited Chunk with the retrieval permission check and its current version.
- [ ] One failed Citation withdraws the whole Answer, keeping its usage and context report.
- [ ] A confirmed Citation is rebuilt from storage, so a renamed Document shows its new title.
- [ ] No title, link, or text reaches a log record, a repr, or an error.
- [ ] You ran the deliberate break, saw 6 tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
