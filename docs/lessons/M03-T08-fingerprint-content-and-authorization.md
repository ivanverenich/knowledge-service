# M03-T08 — Fingerprint content and authorization

Source task: [M03-T08](../curriculum/milestones/M03-local-ingestion/M03-T08-fingerprint-content-and-authorization.md)

## What you will do

This lesson includes coding: Steps 2–5 write code, while Steps 1 and 6 do not.

Every Synchronization Run sees each document again, and most documents have not changed. The service needs a cheap way to tell three situations apart:

| Situation | What must happen later |
|---|---|
| Nothing changed | Do nothing. |
| The text changed | Re-chunk and re-embed (expensive). |
| Only who may read it changed | Update the Access Grants only, without re-chunking (cheap, and urgent when access is removed). |

`Document` already has a `content_fingerprint` and an `authorization_fingerprint`, and `Document.reconcile` already raises each version only when its fingerprint changes. Nothing computes those fingerprints yet; today's tests pass placeholder strings such as `"content-a"`. This lesson writes the two functions that compute them.

Terms used in this lesson:

- **Fingerprint (hash):** a short, fixed-length value computed from data, here 64 hex characters from SHA-256. Equal data always gives the same fingerprint, and different data almost certainly gives a different one.
- **Canonical form:** one fixed way to write a value down before hashing. Without it, the same set of readers written in two orders would hash differently.
- **Property test:** a test that states a rule ("any order gives the same fingerprint") and lets a library generate many inputs to check it, instead of a few hand-picked examples. This project uses **Hypothesis**.

## Before you begin

- **Which access shape is hashed.** Sources report `SourceAccess` (provider principal strings). The task asks for *normalized* Access Grants: `AccessGrant` values whose `Subject` names a `UserId`, a `GroupId`, or everyone. Mapping principals to those IDs comes in a later milestone; M03-T12 will compare grant fingerprints when it reconciles a Document.
- **What counts as content.** Everything the chunker will read: `ParsedDocument.text` and every field of every block, including offsets, because citations point into the text. The title, media type, language, and parser version are left out: a parser upgrade that produces identical blocks should not re-index anything.
- **Files you will touch:**
  - `pyproject.toml` and `uv.lock` (through `uv add`)
  - new `src/knowledge_service/fingerprints.py` and `tests/test_fingerprints.py`
  - `src/knowledge_service/access.py` and `tests/test_access.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the content fingerprint depends only on the parsed text and blocks, and the authorization fingerprint depends only on the set of readers. Equal inputs always give equal fingerprints, in any process and on any run. Grant order, grant IDs, and grant times never change the authorization fingerprint. Any change the chunker can see changes the content fingerprint. The two fingerprints can never be equal to each other.

**Action:** create `docs/evidence/M03-T08-fingerprints.md`:

```markdown
# M03-T08 — Content and authorization fingerprints evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect for: the same grants in two orders,
the same readers granted again with new grant IDs and times, one extra
reader, no readers at all versus a public grant, a user and a group that
share a UUID, the same reader granted twice, a grant for another document,
the same Markdown parsed twice, one changed word, a heading changed from `#`
to `##`, an extra blank line, and what `Document.reconcile` does for an
unchanged, a content-only, and an access-only update.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: add Hypothesis for property tests

**Decision: Hypothesis, as a development dependency only.** It is the standard Python library for property-based testing, it is MPL-2.0 licensed, and it never ships with the service.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv add --dev hypothesis
```

This adds `"hypothesis>=6.170.5"` to the `dev` group in `pyproject.toml` and updates `uv.lock`.

Hypothesis keeps a small cache in a `.hypothesis/` folder at the project root. It writes its own `.gitignore` (containing `*`) inside that folder, so Git ignores it; there is nothing to commit.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "import hypothesis; print(hypothesis.__version__)"` prints a version, and `uv lock --check` passes.

### Step 3 — Coding step: the fingerprint module

| Function | Hashes | Ignores |
|---|---|---|
| `content_fingerprint(parsed)` | `parsed.text`, and each block's kind, text, offsets, heading level, list depth, code language, links, and page, in order | nothing the chunker can see |
| `authorization_fingerprint(document_id=..., grants=...)` | the sorted set of subjects (kind plus serialized ID) | grant order, grant IDs, grant times |

Decisions:

- **Canonical JSON, then SHA-256.** `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)` writes a value one way only: keys sorted, no optional spaces. JSON also escapes quotes and commas inside strings, so two different values can never be written the same way.
- **A label inside each hash.** The hashed value starts with `knowledge-service/content/v1` or `knowledge-service/authorization/v1`. The two kinds of fingerprint therefore never collide, and if the canonical form ever has to change, bumping `v1` to `v2` makes every old fingerprint different on purpose.
- **Reuse `granted_subjects`.** It already refuses a reader granted twice and a grant for another document, with `InvalidGrant`. That is the module's typed failure, and it means the fingerprint only ever describes a valid grant set.
- **Sort subjects by their serialized form.** The kind comes first, so a user and a group that share a UUID stay different readers. An empty grant list is valid: it means nobody may read the document, and it has its own fingerprint.
- **Numbers are written with `int(...)`.** In Python `True == 1`, so a block built with `start=False, end=True` equals one built with `0, 1`. JSON would write `false` and `true`, giving equal blocks different fingerprints. An adversarial review found this.
- **`grants` is copied to a tuple first.** `granted_subjects` reads its input twice. A generator would be used up by the first read and silently hash as "nobody may read".
- **`"surrogatepass"` when encoding.** Parsed text can contain a lone surrogate, a half of a Unicode pair, which plain UTF-8 encoding refuses. This option still writes the same bytes every time.

**Action:** create `src/knowledge_service/fingerprints.py`:

```python
"""Fingerprint a Document's content and its access separately.

A fingerprint is a SHA-256 hash of a canonical form: one fixed way to write
the value down, so equal values always hash equally. Content and access are
hashed apart, so comparing both fingerprints tells an unchanged item, a
content-only change, and an access-only change apart. Each hash starts from
its own label, so a content fingerprint can never equal an authorization one.
"""

import hashlib
import json
from collections.abc import Sequence

from knowledge_service.access import AccessGrant, granted_subjects
from knowledge_service.identifiers import DocumentId
from knowledge_service.structure import ParsedDocument

_CONTENT_LABEL = "knowledge-service/content/v1"
_AUTHORIZATION_LABEL = "knowledge-service/authorization/v1"


def content_fingerprint(parsed: ParsedDocument) -> str:
    """Return the fingerprint of a document's text and structure.

    Everything the chunker reads counts: the parsed text and every field of
    every block, including offsets, because citations point into the text.
    """
    blocks = [
        {
            "kind": block.kind.value,
            "text": block.text,
            "start": _number(block.start),
            "end": _number(block.end),
            "heading_level": _number(block.heading_level),
            "list_depth": _number(block.list_depth),
            "language": block.language,
            "links": [[link.text, link.target] for link in block.links],
            "page": _number(block.page),
        }
        for block in parsed.blocks
    ]
    return _hash(_CONTENT_LABEL, {"text": parsed.text, "blocks": blocks})


def authorization_fingerprint(
    *, document_id: DocumentId, grants: Sequence[AccessGrant]
) -> str:
    """Return the fingerprint of who may read one document.

    Only the subjects count. The order of the grants, their IDs, and when they
    were granted do not, so the same readers always give the same fingerprint.
    An empty sequence means nobody may read the document; it has its own
    fingerprint.
    """
    subjects = [
        [
            grant.subject.kind.value,
            None
            if grant.subject.identifier is None
            else grant.subject.identifier.serialize(),
        ]
        for grant in granted_subjects(document_id=document_id, grants=tuple(grants))
    ]
    subjects.sort(key=lambda subject: (subject[0], subject[1] or ""))
    return _hash(_AUTHORIZATION_LABEL, subjects)


def _number(value: int | None) -> int | None:
    """Write a number as a plain int, so `True` and `1` hash the same."""
    return None if value is None else int(value)


def _hash(label: str, value: object) -> str:
    """Hash a label and a JSON value written in one canonical way."""
    canonical = json.dumps(
        [label, value], ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    # Parsed text can hold a lone surrogate; "surrogatepass" still encodes it
    # to the same bytes every time instead of raising.
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()
```

Some details:

- **`hashlib.sha256(...).hexdigest()`** returns 64 lowercase hex characters.
- **`[link.text, link.target]`** is a list, not a tuple, because JSON has no tuples; a tuple would be written as a list anyway.
- **`subject[1] or ""`** sorts the public subject, whose ID is `None`, as an empty string. Its kind already sorts it apart.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: make `Subject` reject a kind that is not a `SubjectKind`

The adversarial review found that `Subject("group", UserId.new())` was accepted: `__post_init__` compares the kind with `is SubjectKind.GROUP`, and a plain string never matches, so every check was skipped. The fingerprint then crashed with `AttributeError` instead of a typed error. Pyright would flag such a call, but data from a boundary can still reach it at runtime.

**Decision: check the kind's type first.** `type(self.kind) is not SubjectKind` matches the pattern `documents.py` already uses for `ContentVersion`. Pyright strict accepts it; an `isinstance` check is reported as unnecessary, because the declared type already says `SubjectKind`.

**Action:** in `src/knowledge_service/access.py`, add the first check to `Subject.__post_init__`:

```python
    def __post_init__(self) -> None:
        if type(self.kind) is not SubjectKind:
            raise InvalidGrant("a subject kind must be a SubjectKind")
```

The rest of the method stays the same.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_access.py -q` still passes.

### Step 5 — Coding step: tests

| Test | Proves |
|---|---|
| `test_the_order_of_grants_never_changes_the_fingerprint` | **Property:** any permutation of up to 8 readers gives the same fingerprint. This is the task's acceptance criterion. |
| `test_grant_ids_and_times_never_change_the_fingerprint` | **Property:** re-issuing grants with new IDs and times changes nothing. |
| `test_adding_a_reader_always_changes_the_fingerprint` | **Property:** a new reader is always visible. |
| `test_no_readers_has_its_own_fingerprint` | "Nobody" is not the same as "everyone". |
| `test_a_user_and_a_group_with_the_same_uuid_are_different_readers` | The kind is part of the identity. |
| `test_the_same_reader_granted_twice_is_refused` | The typed failure. |
| `test_a_grant_for_another_document_is_refused` | The typed failure. |
| `test_the_same_content_always_has_the_same_fingerprint` | Determinism. |
| `test_any_change_the_chunker_sees_changes_the_content_fingerprint` (4 cases) | Text, heading level, offsets, and block kind. |
| `test_equal_documents_have_equal_fingerprints_even_with_bool_offsets` | The `True == 1` case. |
| `test_a_lone_surrogate_in_text_still_has_a_fingerprint` | No crash on broken Unicode. |
| `test_the_canonical_form_does_not_drift` | Two pinned values. A silent change would re-index every Document. |
| `test_fingerprints_tell_content_and_access_changes_apart` | Through `Document.reconcile`: unchanged keeps the same object, content-only raises only the content version, access-only raises only the authorization version. |

**Decision: deterministic property tests.** `settings(derandomize=True, database=None, deadline=None)` makes Hypothesis generate the same examples on every run, which this repository expects of its tests, and turns off its time limit per example.

How a Hypothesis test reads:

- **`@given(readers=st.lists(subjects, unique=True, max_size=8))`** asks Hypothesis for many lists of up to 8 different readers and runs the test once per list.
- **`st.data()` and `data.draw(st.permutations(grants))`** draw a shuffled copy inside the test, after the grants exist.
- **A strategy such as `subjects`** describes how to build one value: here the public subject, or a user or group with a random UUID.

**Action:** create `tests/test_fingerprints.py`:

```python
"""Tests for content and authorization fingerprints."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.access import AccessGrant, InvalidGrant, Subject
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.structure import BlockKind, ParsedDocument, StructuralBlock

# The same examples on every run, and no example database written to disk.
DETERMINISTIC = settings(derandomize=True, database=None, deadline=None)

DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
ALICE = Subject.user(UserId.parse("00000000-0000-0000-0000-000000000031"))
FINANCE = Subject.group(GroupId.parse("00000000-0000-0000-0000-000000000041"))
GRANTED_AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
GUIDE = parse_markdown("# Onboarding\n\nRead the security policy first.\n")

subjects = st.one_of(
    st.just(Subject.public()),
    st.uuids().map(lambda value: Subject.user(UserId(value))),
    st.uuids().map(lambda value: Subject.group(GroupId(value))),
)
timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)


def grant(subject: Subject, *, document_id: DocumentId = DOCUMENT_ID) -> AccessGrant:
    return AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=document_id,
        subject=subject,
        granted_at=GRANTED_AT,
    )


@DETERMINISTIC
@given(data=st.data(), readers=st.lists(subjects, unique=True, max_size=8))
def test_the_order_of_grants_never_changes_the_fingerprint(
    data: st.DataObject, readers: list[Subject]
) -> None:
    grants = [grant(subject) for subject in readers]
    shuffled = data.draw(st.permutations(grants))

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=shuffled
    ) == authorization_fingerprint(document_id=DOCUMENT_ID, grants=grants)


@DETERMINISTIC
@given(
    readers=st.lists(subjects, unique=True, max_size=8),
    ids=st.lists(st.uuids(), min_size=8, max_size=8, unique=True),
    moments=st.lists(timestamps, min_size=8, max_size=8),
)
def test_grant_ids_and_times_never_change_the_fingerprint(
    readers: list[Subject], ids: list[UUID], moments: list[datetime]
) -> None:
    original = [grant(subject) for subject in readers]
    reissued = [
        replace(item, grant_id=AccessGrantId(new_id), granted_at=moment)
        for item, new_id, moment in zip(original, ids, moments, strict=False)
    ]

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=reissued
    ) == authorization_fingerprint(document_id=DOCUMENT_ID, grants=original)


@DETERMINISTIC
@given(
    readers=st.lists(subjects, unique=True, max_size=8),
    extra=subjects,
)
def test_adding_a_reader_always_changes_the_fingerprint(
    readers: list[Subject], extra: Subject
) -> None:
    if extra in readers:
        return
    before = [grant(subject) for subject in readers]

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[*before, grant(extra)]
    ) != authorization_fingerprint(document_id=DOCUMENT_ID, grants=before)


def test_no_readers_has_its_own_fingerprint() -> None:
    nobody = authorization_fingerprint(document_id=DOCUMENT_ID, grants=[])
    everyone = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.public())]
    )

    assert nobody != everyone


def test_a_user_and_a_group_with_the_same_uuid_are_different_readers() -> None:
    same = UUID("00000000-0000-0000-0000-000000000050")

    as_user = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.user(UserId(same)))]
    )
    as_group = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.group(GroupId(same)))]
    )

    assert as_user != as_group


def test_the_same_reader_granted_twice_is_refused() -> None:
    with pytest.raises(InvalidGrant, match="at most once"):
        authorization_fingerprint(
            document_id=DOCUMENT_ID, grants=[grant(ALICE), grant(ALICE)]
        )


def test_a_grant_for_another_document_is_refused() -> None:
    foreign = grant(FINANCE, document_id=OTHER_DOCUMENT_ID)

    with pytest.raises(InvalidGrant, match="same document"):
        authorization_fingerprint(document_id=DOCUMENT_ID, grants=[foreign])


def test_the_same_content_always_has_the_same_fingerprint() -> None:
    again = parse_markdown("# Onboarding\n\nRead the security policy first.\n")

    assert content_fingerprint(again) == content_fingerprint(GUIDE)


@pytest.mark.parametrize(
    "changed",
    [
        "# Onboarding\n\nRead the security policy second.\n",
        "## Onboarding\n\nRead the security policy first.\n",
        "# Onboarding\n\n\nRead the security policy first.\n",
        "# Onboarding\n\n- Read the security policy first.\n",
    ],
    ids=["text", "heading-level", "offsets", "kind"],
)
def test_any_change_the_chunker_sees_changes_the_content_fingerprint(
    changed: str,
) -> None:
    assert content_fingerprint(parse_markdown(changed)) != content_fingerprint(GUIDE)


def test_equal_documents_have_equal_fingerprints_even_with_bool_offsets() -> None:
    # `True == 1` in Python, so these blocks are equal and must hash equally.
    as_ints = ParsedDocument(
        text="x", blocks=(StructuralBlock(BlockKind.PARAGRAPH, "x", 0, 1),)
    )
    as_bools = ParsedDocument(
        text="x", blocks=(StructuralBlock(BlockKind.PARAGRAPH, "x", False, True),)
    )

    assert as_bools == as_ints
    assert content_fingerprint(as_bools) == content_fingerprint(as_ints)


def test_a_lone_surrogate_in_text_still_has_a_fingerprint() -> None:
    surrogate = parse_markdown("Broken \ud800 character.")

    assert content_fingerprint(surrogate) == content_fingerprint(surrogate)


def test_the_canonical_form_does_not_drift() -> None:
    # A changed canonical form would silently re-index every Document. If
    # this fails on purpose, bump the label's version and update the values.
    assert content_fingerprint(GUIDE) == (
        "9f9a307cdb75ab83f1c9f708bb2786f2fbe3568881ec395d2cc6900442d25964"
    )
    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(FINANCE), grant(ALICE)]
    ) == ("4d7d0315d598182e4926bae2f7f9308707bc279e2102817b2b5af70f9dd670e7")


def test_fingerprints_tell_content_and_access_changes_apart() -> None:
    provenance = DocumentProvenance(
        source_id=SourceId.parse("00000000-0000-0000-0000-000000000010"),
        external_id="guides/onboarding.md",
    )
    alice_only = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(ALICE)]
    )
    document = Document.register(
        document_id=DOCUMENT_ID,
        provenance=provenance,
        content_fingerprint=content_fingerprint(GUIDE),
        authorization_fingerprint=alice_only,
        observed_at=GRANTED_AT,
    )
    edited = content_fingerprint(
        parse_markdown("# Onboarding\n\nRead the new security policy.\n")
    )
    with_finance = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(FINANCE), grant(ALICE)]
    )

    def observe(content: str, access: str) -> Document:
        return document.reconcile(
            content_fingerprint=content,
            authorization_fingerprint=access,
            source_version=None,
            observed_at=GRANTED_AT,
        )

    unchanged = observe(content_fingerprint(GUIDE), alice_only)
    content_only = observe(edited, alice_only)
    access_only = observe(content_fingerprint(GUIDE), with_finance)

    assert unchanged is document
    assert (
        content_only.content_version.number,
        content_only.authorization_version.number,
    ) == (2, 1)
    assert (
        access_only.content_version.number,
        access_only.authorization_version.number,
    ) == (1, 2)
```

The two 64-character values in `test_the_canonical_form_does_not_drift` are the real fingerprints of the fixtures. If you ever change the canonical form on purpose, bump the label's `v1` and update them.

Then add this test to `tests/test_access.py`, before `test_a_naive_grant_time_raises_typed_failure`:

```python
def test_a_subject_kind_that_is_not_a_subject_kind_is_rejected() -> None:
    with pytest.raises(InvalidGrant, match="SubjectKind"):
        Subject("group", USER_ID)  # pyright: ignore[reportArgumentType]
```

The `# pyright: ignore[reportArgumentType]` comment is needed because the test passes a wrong type on purpose.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_fingerprints.py tests/test_access.py -v
```

All 23 tests pass: 16 in `test_fingerprints.py` and 7 in `test_access.py`.

### Step 6 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_fingerprints.py tests/test_access.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv lock --check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record:

- **HTML markup changes count as content changes.** For HTML, `parsed.text` is the raw page, and block offsets point into it. Changing `<p class="a">` to `class="b"` gives identical blocks but a new fingerprint, so the page is re-chunked. This keeps every citation offset valid; the price is re-indexing on cosmetic edits.
- **Cost.** One million characters hash in about 0.01 seconds; 100,000 blocks take about 1.3 seconds and 62 MB, because the whole canonical form is built in memory before hashing.
- **If the title ever goes into chunk text or embeddings, it must join the content fingerprint.**

Answer these reflection questions in your own words:

1. Why do content and access get two separate fingerprints instead of one hash of everything? Describe what a single hash would cost when one user is removed from a large document's readers.
2. Why must grant order, grant IDs, and grant times not affect the authorization fingerprint? What would go wrong in every Synchronization Run if they did?
3. Why does the content fingerprint include block offsets and the raw HTML text, and what does that cost?
4. What does `test_the_canonical_form_does_not_drift` protect against, and why would that failure be silent without it?

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `hypothesis` is a dev dependency in `pyproject.toml` and `uv.lock`.
- [ ] `fingerprints.py` has `content_fingerprint` and `authorization_fingerprint`, each hashing a labelled canonical JSON form with SHA-256.
- [ ] Property tests prove that grant order, grant IDs, and grant times never change the authorization fingerprint, and that a new reader always does.
- [ ] Any change the chunker can see changes the content fingerprint; equal documents hash equally.
- [ ] A duplicate reader or a grant for another document raises `InvalidGrant`, and `Subject` rejects a kind that is not a `SubjectKind`.
- [ ] Through `Document.reconcile`, unchanged, content-only, and access-only updates are told apart.
- [ ] All 23 tests in the two test files, the full deterministic suite, format, lint, types, `make check`, `make docs-check`, and `uv lock --check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the three recorded notes), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
