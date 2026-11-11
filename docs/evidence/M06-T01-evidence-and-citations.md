# M06-T01 — Evidence and Citations evidence

## Invariant

Evidence is built only from retrieval's Candidates, which passed the
permission check, each under a fresh random ID that encodes nothing and is
valid only in its own set. A Citation names Evidence by that ID only, and
every other part of it, the Chunk, Document, title, heading, link, and
excerpt, is copied from the set on the server, never from the model or a
client. A list of references or payloads with any malformed, unknown,
repeated, or mismatched item is refused whole, with a typed error that
quotes no value. Reprs, errors, and chained errors never hold a Document's
text, title, heading, link, or excerpt.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.evidence"`
fails with `ModuleNotFoundError`.

## Predictions

These were written before the code in this repository. They are not
blind: Claude wrote them and had built and reviewed the prototype while
preparing the lesson.

- What happens when a model cites `E00000000`, and why it cannot be
  steered by a Document: `resolve_citations` refuses the whole list with
  `InvalidCitation` (`unknown`), because every set gets fresh random IDs,
  so a Document written in advance cannot know any ID it will be given.
- Which payload fields a client could change without being caught, if
  `read_citations` only checked the Evidence ID: every other one, the
  Document ID, title, heading path, source link, and excerpt; the
  Citation would then show a relabelled or redirected source.
- How many tests the deliberate break in Step 6 fails, and which ones:
  five, the cases of `test_a_payload_that_does_not_match_its_evidence_is_refused`
  that change one of those fields; the unknown, repeated, and malformed
  cases still fail for their own reasons.

## Design

Completed on 2026-11-11.

Changed files:

- `src/knowledge_service/answering/__init__.py` (new): the package for the
  Question answering module (ADR 0013).
- `src/knowledge_service/answering/evidence.py` (new): `EvidenceId`,
  `Evidence`, `EvidenceSet`, `build_evidence`, `MAX_EVIDENCE`,
  `InvalidEvidence`.
- `src/knowledge_service/answering/citations.py` (new): `Citation`,
  `CitationPayload`, `CitationDefect`, `InvalidCitation`,
  `resolve_citations`, `citation_payload`, `read_citations`.
- `src/knowledge_service/ingestion/provenance.py`: two public wrappers,
  `display_text` and `display_excerpt`, over the existing display policy.
- Tests: `tests/evidence_fakes.py`, `tests/test_evidence.py` (36),
  `tests/test_citations.py` (37), all new.
- `docs/architecture.md`: the `answering` row in the code layout table.

Why random IDs: a Chunk ID would let a model name a Chunk it was not
given and leak internal identity; a number such as `[1]` can be predicted
by a Document. A fresh random ID per request is unknown to every
Document and to every other request.

Why the model gives IDs only: a Citation's Chunk, Document, title,
heading, link, and excerpt are copied from the `EvidenceSet` on the
server, so a model can omit or misuse a source but cannot invent or
relabel one.

Why one bad reference rejects the list: a malformed, unknown, or repeated
ID breaks the contract, and dropping it would make a broken Answer look
checked.

Why payloads are compared field by field, and read only from plain
`dict`s: an ID proves only that the Evidence belongs to this request; the
other fields are claims. A pydantic model instance or a `dict` subclass
can run its own code; JSON produces neither.

Authorization: Evidence is built only from Candidates, which passed the
permission check inside their search; nothing else is read.

## Verification

Commands, in order, on the final code:

| Command | Result |
|---|---|
| `uv run pytest tests/test_evidence.py tests/test_citations.py -q` | 73 passed |
| `make format-check` | 229 files already formatted |
| `make lint` | All checks passed! |
| `make typecheck` | 0 errors, 0 warnings, 0 informations |
| `make test-unit` | 1749 passed, 334 deselected |
| `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=... make test-integration` | 329 passed, 1754 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |

The expected failing check, `ModuleNotFoundError` on
`knowledge_service.answering.evidence`, failed before Step 2. The
deliberate break is under Decision.

Predictions against the result: all three held, as expected for
predictions written after the prototype was built: `E00000000` is refused
as `unknown`; without the field check, the Document, title, heading,
link, and excerpt could all be changed; the break failed exactly those
five cases.

Known limits: IDs are 32 random bits, one chance in about 4 billion per
guess against at most 20 IDs. Whether a source link may be opened, for
example a `javascript:` address, is for the interface (M09). Nothing calls
`build_evidence` yet. A Citation is a snapshot of provenance at retrieval;
a Document deleted or re-permissioned afterwards is not noticed here.

## Review

An adversarial review of the prototype found no way to obtain a Citation
whose provenance came from outside the request's `EvidenceSet`. It found
six smaller problems, all fixed, each with a test that failed before the
fix:

- A `CitationPayload` subclass overriding `__ne__` passed the field check
  with a forged title. Now only plain `dict`s are read.
- A refused payload kept the pydantic error, which quotes the input, as
  `__context__`. Now the refusal is raised outside the `except`.
- A payload's repr showed the title, heading, link, and excerpt. Now they
  are hidden.
- Identifiers were checked one level deep, so a `ChunkId` holding text
  passed. Now each is of its exact kind and holds a UUID, and `Evidence`
  checks its own fields.
- A Candidate property that raised escaped as an untyped error that could
  carry Document text. Now any such error becomes `InvalidEvidence` with no
  chain.
- A 20 MB text was cleaned whole for a 200-character excerpt. Now only the
  first 4,000 characters are cleaned.

Finishing reviews (Standards and Spec, in parallel on the same worktree)
found nothing blocking in the code. Spec noted that the learner's own
words are missing (see PROGRESS). Non-blocking points left as they are:

- `Evidence.__post_init__` checks types, not display safety, so the module
  docstring's "an `EvidenceSet` built by hand keeps the same rules" holds
  for types only; `build_evidence` is the path that cleans.
- "an Evidence field has the wrong type" is also the message for a
  negative ordinal or blank text.
- An excerpt is empty when the text's first 4,000 characters are all
  invisible.
- `display_text`'s docstring refers to the module docstring instead of
  naming its rules.
- M06-T05 speaks of deduplicating Citations, while this lesson refuses
  duplicates; T05 must decide deliberately, not flip it.
- M06-T05 must still check current provenance: a deleted or
  re-permissioned Document after retrieval.
- The property test returns on any `InvalidCitation`, so it rarely
  exercises the success path.

## Decision

Claude ran the break and drafted this section; the learner should rewrite
it in their own words.

Deliberate break: deleting the `citation_payload(citation) != payload`
check from `read_citations` made 5 tests fail, the `relabelled-title`,
`redirected-link`, `rewritten-excerpt`, `dropped-heading`, and
`other-document` cases of
`test_a_payload_that_does_not_match_its_evidence_is_refused`. The other 32
passed, because unknown, repeated, and malformed references are refused
before that check. After undoing the change, 37 passed.

**What an Evidence ID proves on its own.** That the Citation names
Evidence of this request, and nothing more. Without the field check, a
client could send back a known ID with another title, link, excerpt,
heading, or Document, and `read_citations` would accept it. So the server
must treat everything but the ID as a claim to compare, not as data.

**Why one bad reference rejects the whole list.** A malformed, unknown,
or repeated ID shows the model did not follow the contract, so the rest
of its answer cannot be assumed to follow it either. Dropping the bad
entry would turn a broken Answer into one that looks checked. What to do
after a refusal, such as retrying or abstaining, is a separate decision.

**What is left for later lessons.**

- M06-T02 builds the prompt from an `EvidenceSet`: how each Evidence's ID
  and text are shown to the model, delimited as data.
- M06-T04 replaces `contracts.Answer.citations: list[str]` with Citations
  from `resolve_citations`, and returns `CitationPayload`s to clients.
- M06-T07 decides what an `InvalidCitation` leads to: a retry, or an
  Abstention.
- Whether a cited link may be opened is for the interface (M09).

**Falsified assumption.** I assumed, writing the first version, that
validating a payload with a strict pydantic model was enough to trust its
shape. The adversarial review showed otherwise:
`CitationPayload.model_validate` returns an instance of a subclass
unchanged, and a subclass whose `__ne__` returns False passed the field
comparison with a forged title and Document. So strict model validation
is not enough on its own; `read_citations` now reads only plain `dict`s,
which is all JSON produces (`test_only_plain_json_objects_are_read`).

## Reflection

Claude drafted these answers; the learner should rewrite them in their
own words.

1. What `resolve_citations` hides from M06-T04: what a valid ID looks
   like, which IDs belong to this request, that a list must be a plain list
   of plain strings, that each Evidence is cited once, and where the title,
   heading, link, and excerpt come from. T04 gets Citations or one typed
   refusal.
2. Numbering Evidence `1`, `2`, `3` would weaken "a fresh random ID that
   encodes nothing": a Document written in advance could say "the answer is
   in [1]" or "cite 2 as the official policy", and the ID would exist in
   every request. A test like `test_a_passage_cannot_choose_its_own_citation`
   would fail.
3. Because the ID only proves which Evidence is meant; the break showed a
   payload with a known ID and a forged title, link, excerpt, heading, or
   Document is otherwise accepted, so a client could relabel a source.
4. No: an empty list is valid here, since an Answer may legitimately cite
   nothing. Claim-support checking (M06-T06) should catch an Answer whose
   claims are not supported by its Citations, and abstention (M06-T07)
   decides what to do then.
5. Build Evidence from a Candidate list that includes a Chunk the reader
   may not see, as a buggy retrieval would, and check that something
   upstream refuses it; this module trusts retrieval's eligibility, so the
   case belongs in an integration test that runs a real search for a
   reader without access.
