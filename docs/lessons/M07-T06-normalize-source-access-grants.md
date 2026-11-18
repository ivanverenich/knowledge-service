# M07-T06 — Normalize source Access Grants

Task: [M07-T06 — Normalize source Access Grants](../curriculum/milestones/M07-identity-and-authorization/M07-T06-normalize-source-access-grants.md)

## What you will do

This lesson connects who a Source says may read a file to who a token says is asking. Until now, a Source could only mark an item public; any item with named readers was stored but readable by nobody, because the service did not know who those names were. At the end, a Source's `group:engineering` becomes exactly the Group alice's token carries, so alice finds the file and bob does not.

- **One canonical form:** every reader is a public, user, or group Subject, using the same UUIDv5 IDs the Authorization Context builds from tokens.
- **A stable mapping:** a `PrincipalDirectory` maps each Source's own user and group names to identity-provider users and group paths, exactly as written.
- **Distinct and order-free:** the same readers in any order, or named twice, give the same result.
- **Fail closed:** a name the directory does not map, or a malformed one, grants nothing and is reported, never logged by name.
- **Broken files lose readers too:** a file that stops parsing keeps its last good version, but only for readers its old and new access both name.

| The task asks for | Where it happens |
|---|---|
| Canonical user, group, and public subjects | Step 3: `normalize_access` returns `Subject`s with the token's own IDs |
| Stable external mappings | Step 3: `PrincipalDirectory`; Step 2 makes it use the token's own name rules |
| Deduplication | Step 3: readers come back distinct and sorted |
| Fail-closed behavior for unresolved identities | Step 3: unresolved principals grant nothing; Step 4: synchronization reports them and narrows broken files |
| Property tests: order independence and unresolved-principal quarantine | Step 5: `tests/test_grant_normalization.py` |
| Authorization preserved before data leaves storage | Step 6: retrieval's own eligibility check lets only mapped readers see the file |
| Telemetry holds no seeded sensitive text | Steps 5 and 6: marker tests on reprs and logs |
| Update only what the change made inaccurate | Step 8: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 6 write code and tests. Steps 1, 7, and 8 do not, though Step 8 edits two documents.

Terms used in this lesson:

- **Principal:** a reader as the Source names it, before the service understands it, such as `group:engineering` or `user:carol@example.test`.
- **Principal directory:** the translation table for one Source: Source user ID to the identity provider's `sub`, Source group ID to a full group path. Like a phone book that turns a nickname into a full name.
- **Canonical subject:** the one form the database stores: everyone, one `UserId`, or one `GroupId`.
- **Unresolved principal:** a name the directory cannot translate. It is put aside (quarantined), like a letter with an unknown address: kept for someone to fix, never delivered.
- **Property test:** a test that states a rule and lets Hypothesis try hundreds of generated inputs against it, instead of a few hand-picked ones.
- **Fail closed:** when in doubt, show less. A missing mapping can hide a Document, never reveal one.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Principals are written `user:<id>` or `group:<id>`.** The kind comes first, so a user and a group with the same name never mix. Anything else, such as `hr`, `Group:x`, or `group: x`, is malformed. IDs are matched exactly: no case folding, no trimming, no Unicode tricks, so a lookalike name finds nothing.
- **The same IDs as tokens.** A mapped group becomes `group_id_for(issuer, path)`, the function M07-T04 uses for token groups. So no second ID scheme exists, and a Source group mapped to `/engineering` matches every token that lists `/engineering`.
- **Only names a token could carry.** The directory refuses a `sub` or path that the Authorization Context would never accept, such as a path with `~` or a hidden character. Step 2 makes those two rules public so both sides use the same code.
- **Unresolved grants nothing, and is reported.** The Document is still published for the readers that did resolve. The synchronization summary lists the unresolved principals per file, so an operator can add the mapping; the log gets only a count, because a principal may name a person. With no directory at all, only public items are readable, as before.
- **Every caller names its directory.** `principal_directory` has no default. Forgetting it would silently make every private Document unreadable on that pass, so a caller must write `principal_directory=None` on purpose.
- **A broken file shrinks, never grows.** When a file stops parsing, its last good version stays searchable. Its new access is still applied at once, but only as the overlap of old and new readers: whoever the Source removed loses it now, and whoever it added waits for a version that parses.
- **What the review found.** An independent adversarial review tried lookalike and malformed principals, Unicode tricks, colons inside IDs, user and group crossovers, hostile directories, 3,000 generated cases against its own reference model, and 41 code mutations. The normalizer never widened access. It found these problems, all fixed with tests that failed before the fix:
  - A file that stopped parsing kept its old grants, so a group the Source had just removed could still read the last good version, and a public file made private stayed public. Now `narrow_access` applies the overlap at once.
  - The directory accepted paths and subjects no token can carry, such as `/a~/b` or a name with a zero-width space, which made grants that match nobody. Now it uses the token's own rules and refuses them.
  - The directory's repr printed every mapping, which names people. Now the mappings are left out.
  - A `str` subclass in the directory could compare or hash as another name. Now only exact `str` is accepted.
  - The `principal_directory` argument defaulted to none, so a forgotten argument hid every private Document. Now every caller passes it.
  - A file offered twice in one pass could be reported with access it no longer had. Now the last report wins.
  - Three mutations survived: splitting at the last colon, trimming a trailing slash from paths, and a sort key without the ID. Each now has a test.
- **Known limits.** No stored Source configuration holds a directory yet, so callers pass it by hand. Confluence's site, space, and page permissions are combined in M08-T14. The count of unresolved principals is logged only when a pass completes. Retrieval already filters by grants; M07-T07 and M07-T08 make every vector and lexical query take the reader.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 7 checks the last prediction.

Create `docs/evidence/M07-T06-normalize-source-access-grants.md`:

```markdown
# M07-T06 — Normalize source Access Grants evidence

## Invariant

A Document's Access Grants name only everyone, for an item the Source marks
public, or the users and groups its principals resolve to through the
Source's `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization
Context uses. A principal that is malformed or unmapped grants nothing and is
reported; the result does not depend on the principals' order or repetition.
A record that fails to normalize keeps its last good version only for readers
both its old and new access name. No principal or mapping text reaches a log
or a repr.

## Expected failing check

Before the change, `uv run pytest tests/test_grant_normalization.py -q` fails
with `ModuleNotFoundError: No module named 'knowledge_service.ingestion.grant_normalization'`.

## Predictions

- Which readers a file gets when the Source names `group:engineering`, `group:contractors`, and `hr`, and the directory maps only `engineering` to `/engineering`; and what the summary reports:
- Whether alice (in `/engineering`) and bob (in `/finance`) can retrieve that file after the pass:
- What alice can read if the file then stops parsing and the Source names only `user:carol@example.test`:
- Which tests fail if the call to `narrow_access` is removed from synchronization:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: share the token's name rules

**In plain words:** the Authorization Context already knows which user names and group paths a token may carry. This step makes those two checks public, under clear names, so the Source mapping in Step 3 can refuse any name a token could never match. Nothing about tokens changes.

| Change | What it does |
|---|---|
| `MAX_NAME_LENGTH` | The 255-character limit, now one named constant |
| `_plain_path` → `plain_group_path` | Public, and takes a length instead of a whole policy |
| `_visible_name` → `visible_name` | The same |
| Callers in the module | Pass `policy.max_name_length` |

In `src/knowledge_service/identity/context.py`:

**1.** Find:

```python
from knowledge_service.roles import ApplicationRole

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
BEARER = re.compile(r"Bearer +([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE | re.ASCII)
# Letters, marks that combine with them, digits, punctuation, and symbols.
VISIBLE = frozenset(
    {"Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Nd", "Nl", "No"}
    | {"Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "Sk", "So"}
```

Replace with:

```python
from knowledge_service.roles import ApplicationRole

logger = logging.getLogger(__name__)

# Fixed forever: changing it would give every User and Group a new ID.
IDENTITY_NAMESPACE = UUID("6f1d3c52-4b8e-4c1a-9a57-2e0b7d4f9c31")
# The longest subject or group path a token may name.
MAX_NAME_LENGTH = 255
# RFC 6750 §2.1: the scheme, one or more spaces, and a b64token.
BEARER = re.compile(r"Bearer +([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE | re.ASCII)
# Letters, marks that combine with them, digits, punctuation, and symbols.
VISIBLE = frozenset(
    {"Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Nd", "Nl", "No"}
    | {"Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sm", "Sc", "Sk", "So"}
```

**2.** Find:

```python
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_roles: int = 64
    max_name_length: int = 255

    def __post_init__(self) -> None:
        if type(self.trusted_clients) is not frozenset or not self.trusted_clients:
            raise ValueError("trusted_clients must be a non-empty frozenset")
        if min(self.max_groups, self.max_roles, self.max_name_length) <= 0:
            raise ValueError("limits must be positive")
```

Replace with:

```python
class ContextPolicy:
    """Which clients may call, and how large the identity claims may be."""

    trusted_clients: frozenset[str]
    max_groups: int = 64
    max_roles: int = 64
    max_name_length: int = MAX_NAME_LENGTH

    def __post_init__(self) -> None:
        if type(self.trusted_clients) is not frozenset or not self.trusted_clients:
            raise ValueError("trusted_clients must be a non-empty frozenset")
        if min(self.max_groups, self.max_roles, self.max_name_length) <= 0:
            raise ValueError("limits must be positive")
```

**3.** Find:

```python
    issuer: object = claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise _defect(ClaimDefect.ISSUER)
    client: object = claims.get("azp")
    if not isinstance(client, str) or client not in policy.trusted_clients:
        raise _defect(ClaimDefect.UNTRUSTED_CLIENT)
    if not _visible_name(token.subject, policy):
        raise _defect(ClaimDefect.SUBJECT)
    return AuthorizationContext(
        user_id=user_id_for(issuer, token.subject),
        group_ids=frozenset(
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
        ),
```

Replace with:

```python
    issuer: object = claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise _defect(ClaimDefect.ISSUER)
    client: object = claims.get("azp")
    if not isinstance(client, str) or client not in policy.trusted_clients:
        raise _defect(ClaimDefect.UNTRUSTED_CLIENT)
    if not visible_name(token.subject, policy.max_name_length):
        raise _defect(ClaimDefect.SUBJECT)
    return AuthorizationContext(
        user_id=user_id_for(issuer, token.subject),
        group_ids=frozenset(
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
        ),
```

**4.** Find:

```python
        raise _defect(ClaimDefect.GROUPS_OVERSIZED)
    named: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            raise _defect(ClaimDefect.GROUPS_MALFORMED)
        named.add(path)
    kept = {path for path in named if _plain_path(path, policy)}
    if ignored := len(named - kept):
        # A count only: the names may be anything the provider holds.
        logger.warning("group claim entries ignored: %d", ignored)
    return kept


def _plain_path(path: str, policy: ContextPolicy) -> bool:
    """A full path such as `/finance/payroll`, with nothing ambiguous in it.

    Keycloak writes a `/` inside a group's name as `~/`, but leaves `~`
    itself alone, so a path with `~` could be either of two groups.
    """
    return (
        path.startswith("/")
        and path != "/"
        and "~" not in path
        and _visible_name(path, policy)
    )


def _visible_name(name: str, policy: ContextPolicy) -> bool:
    """Non-empty, within the length limit, and every character visible.

    Spaces are allowed inside a name, not at either end.
    """
    return (
        0 < len(name) <= policy.max_name_length
        and name == name.strip()
        and all(
            char == " " or (unicodedata.category(char) in VISIBLE and char not in BLANK)
            for char in name
        )
    )
```

Replace with:

```python
        raise _defect(ClaimDefect.GROUPS_OVERSIZED)
    named: set[str] = set()
    for path in paths:
        if not isinstance(path, str):
            raise _defect(ClaimDefect.GROUPS_MALFORMED)
        named.add(path)
    kept = {path for path in named if plain_group_path(path, policy.max_name_length)}
    if ignored := len(named - kept):
        # A count only: the names may be anything the provider holds.
        logger.warning("group claim entries ignored: %d", ignored)
    return kept


def plain_group_path(path: str, max_length: int = MAX_NAME_LENGTH) -> bool:
    """A full path such as `/finance/payroll`, with nothing ambiguous in it.

    Keycloak writes a `/` inside a group's name as `~/`, but leaves `~`
    itself alone, so a path with `~` could be either of two groups. Only a
    plain path ever reaches an Authorization Context.
    """
    return (
        path.startswith("/")
        and path != "/"
        and "~" not in path
        and visible_name(path, max_length)
    )


def visible_name(name: str, max_length: int = MAX_NAME_LENGTH) -> bool:
    """Non-empty, within the length limit, and every character visible.

    Spaces are allowed inside a name, not at either end. Only a visible
    subject ever reaches an Authorization Context.
    """
    return (
        0 < len(name) <= max_length
        and name == name.strip()
        and all(
            char == " " or (unicodedata.category(char) in VISIBLE and char not in BLANK)
            for char in name
        )
    )
```

**Check:** `uv run pytest tests/test_authorization_context.py -q` gives `89 passed`, unchanged.

### Step 3 — Coding step: the grant normalizer

**In plain words:** this file translates a Source's reader list into the readers the database stores. It reads each name, looks it up in the Source's phone book, and turns a hit into the same user or group ID a token would give. Anything it cannot translate is set aside and reported, never granted. The answer is the same whatever order the names came in.

| Part | What it does |
|---|---|
| `PrincipalDirectory` | One Source's mapping: user ID to `sub`, group ID to full path; exact `str` only, names a token could carry, a private read-only copy, mappings left out of the repr |
| `subject_for` | A mapped principal's `Subject`, or None |
| `normalize_access` | Public stays public; otherwise each `user:<id>` or `group:<id>` the directory maps becomes a reader, and everything else an `UnresolvedPrincipal` |
| `UnresolvedPrincipal` | The principal and why: `malformed` or `unknown`; the principal is left out of the repr |
| `NormalizedAccess` | Distinct readers and unresolved principals, each in a stable order |

Create `src/knowledge_service/ingestion/grant_normalization.py`:

```python
"""Turn who a Source says may read an item into Access Grant subjects.

A Source names readers in its own words, as `user:<id>` or `group:<id>`,
with IDs only that Source understands. A `PrincipalDirectory` maps each ID
it knows to the identity provider's subject or full group path, and the
same deterministic IDs the Authorization Context uses make the Subject. So a
Source group mapped to `/engineering` is exactly the Group a token listing
`/engineering` carries.

Normalization fails closed. A principal that is malformed, or that the
directory does not map, grants nothing and is reported as unresolved, so a
gap in the mapping can only hide a Document, never show it. The result does
not depend on the order or the repetition of the principals.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from knowledge_service.access import Subject
from knowledge_service.identity.context import (
    group_id_for,
    plain_group_path,
    user_id_for,
    visible_name,
)
from knowledge_service.ingestion.sources import SourceAccess


class InvalidPrincipalDirectory(ValueError):
    """A principal directory names an identity no token could ever carry."""


class UnresolvedReason(StrEnum):
    """Why a Source principal grants nothing."""

    MALFORMED = "malformed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UnresolvedPrincipal:
    """A principal that grants nothing until the directory maps it.

    The principal is kept for whoever fixes the mapping, but left out of the
    repr: it may name a person, and reprs end up in logs.
    """

    principal: str = field(repr=False)
    reason: UnresolvedReason


@dataclass(frozen=True, slots=True)
class NormalizedAccess:
    """The distinct readers of one item, and the principals that grant nothing."""

    readers: tuple[Subject, ...]
    unresolved: tuple[UnresolvedPrincipal, ...]


@dataclass(frozen=True, slots=True)
class PrincipalDirectory:
    """The stable mapping from one Source's principals to provider identities.

    `users` maps a Source user ID to the provider's `sub`; `groups` maps a
    Source group ID to a full group path such as `/engineering`. IDs are
    matched exactly, as the Source wrote them. Each `sub` and path must be
    one a token could carry, by the Authorization Context's own rules, so a
    mapping can never name an identity nobody can ever be. The mappings are
    left out of the repr: they name people.
    """

    issuer: str
    users: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
    groups: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)

    def __post_init__(self) -> None:
        if not _exact(self.issuer) or not visible_name(self.issuer):
            raise InvalidPrincipalDirectory("the issuer must be a visible name")
        for name in ("users", "groups"):
            mapping: object = getattr(self, name)
            if not isinstance(mapping, Mapping):
                raise InvalidPrincipalDirectory(f"{name} must be a mapping")
            # A private, read-only copy: changing the caller's dictionary
            # later must not change who may read what.
            copied = dict(cast("Mapping[object, object]", mapping))
            if not all(_exact(key) and _exact(value) for key, value in copied.items()):
                raise InvalidPrincipalDirectory(f"{name} must map names to names")
            object.__setattr__(self, name, MappingProxyType(copied))
        if not all(visible_name(subject) for subject in self.users.values()):
            raise InvalidPrincipalDirectory("a user must map to a visible subject")
        if not all(plain_group_path(path) for path in self.groups.values()):
            raise InvalidPrincipalDirectory("a group must map to a plain full path")

    def subject_for(self, kind: str, external_id: str) -> Subject | None:
        """The Subject a Source principal is, or None if it is not mapped."""
        if kind == "user" and (subject := self.users.get(external_id)) is not None:
            return Subject.user(user_id_for(self.issuer, subject))
        if kind == "group" and (path := self.groups.get(external_id)) is not None:
            return Subject.group(group_id_for(self.issuer, path))
        return None


def normalize_access(
    access: SourceAccess, directory: PrincipalDirectory | None
) -> NormalizedAccess:
    """The readers a Source's access names, failing closed.

    A public item is readable by everyone. Otherwise each principal must be
    `user:<id>` or `group:<id>` and mapped by `directory`; any other becomes
    an `UnresolvedPrincipal`. With no directory, nothing resolves. Readers and
    unresolved principals come back distinct and sorted.
    """
    if access.public:
        return NormalizedAccess(readers=(Subject.public(),), unresolved=())
    readers: set[Subject] = set()
    unresolved: set[UnresolvedPrincipal] = set()
    for principal in access.principals:
        kind, separator, external_id = principal.partition(":")
        if not separator or kind not in ("user", "group") or not _exact(external_id):
            unresolved.add(UnresolvedPrincipal(principal, UnresolvedReason.MALFORMED))
            continue
        subject = (
            None if directory is None else directory.subject_for(kind, external_id)
        )
        if subject is None:
            unresolved.add(UnresolvedPrincipal(principal, UnresolvedReason.UNKNOWN))
        else:
            readers.add(subject)
    return NormalizedAccess(
        readers=tuple(sorted(readers, key=_subject_order)),
        unresolved=tuple(sorted(unresolved, key=lambda u: (u.principal, u.reason))),
    )


def _subject_order(subject: Subject) -> tuple[str, str]:
    return (subject.kind.value, str(subject.identifier))


def _exact(name: object) -> bool:
    """A plain, non-empty string with no surrounding whitespace.

    Exactly `str`: a subclass could compare or hash as another name.
    """
    return type(name) is str and bool(name) and name == name.strip()
```

**Check:** `uv run pyright src/knowledge_service/ingestion/grant_normalization.py` reports `0 errors`.

### Step 4 — Coding step: synchronization uses it, and broken files shrink

**In plain words:** this step puts the normalizer into every synchronization pass. Each file's readers now come from the Source's directory, and the summary lists, per file, the names that did not resolve. When a file stops parsing, the pass still applies its new access, but only by taking readers away: nobody gains a file whose new version cannot be read.

| File | Change |
|---|---|
| `src/knowledge_service/ingestion/reconciliation.py` | `narrow_access`: for a stored Document, keep only the readers both old and new access name, and record that in its authorization fingerprint |
| `src/knowledge_service/ingestion/synchronization.py` | A required `principal_directory`; readers from `normalize_access`; `UnresolvedAccess` per file, last report winning; `narrow_access` for quarantined records; a count-only warning; `source_readers` removed |
| `src/knowledge_service/retrieval/evaluation_dataset.py` | Passes `principal_directory=None`: its corpus is public or restricted to nobody |

In `src/knowledge_service/ingestion/reconciliation.py`:

**1.** Find:

```python
than the stored metadata that would change it raises `InvalidDocument`
instead of rolling it back.

A reader sees either the previous complete version or the new one, never a
mixture, because the Document row, its Chunks, its Access Grants, and its
metadata are written together and committed once.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
```

Replace with:

```python
than the stored metadata that would change it raises `InvalidDocument`
instead of rolling it back.

A reader sees either the previous complete version or the new one, never a
mixture, because the Document row, its Chunks, its Access Grants, and its
metadata are written together and committed once.

When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
```

**2.** Find:

```python
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentMetadata,
    InvalidDocument,
)
from knowledge_service.identifiers import AccessGrantId, ChunkId, DocumentId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.ingestion.normalization import NormalizedDocument
from knowledge_service.ingestion.provenance import chunk_sources
from knowledge_service.persistence.access import replace_access_grants
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.chunks import replace_chunks
from knowledge_service.persistence.documents import (
    insert_document,
    load_document,
    load_document_metadata,
```

Replace with:

```python
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentMetadata,
    InvalidDocument,
)
from knowledge_service.identifiers import AccessGrantId, ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.ingestion.normalization import NormalizedDocument
from knowledge_service.ingestion.provenance import chunk_sources
from knowledge_service.persistence.access import (
    load_access_grants,
    replace_access_grants,
)
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.chunks import replace_chunks
from knowledge_service.persistence.documents import (
    insert_document,
    load_document,
    load_document_metadata,
```

**3.** Find:

```python
            restored=restored,
            source_version=source_version_changed,
        ),
    )


def build_chunks(
    normalized: NormalizedDocument,
    document: Document,
) -> tuple[Chunk, ...]:
    """Chunk a document by its structure and give each chunk its source anchor."""
    drafts = chunk_by_structure(normalized.parsed)
```

Replace with:

```python
            restored=restored,
            source_version=source_version_changed,
        ),
    )


async def narrow_access(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    external_id: str,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.

    For a record that failed to normalize: its last good version stays, so
    the Source's new access cannot be fully applied, but anyone the Source
    no longer names loses it now. Nobody gains it until the record
    normalizes again. Returns whether the Access Grants changed.
    """
    async with runtime.transaction() as connection:
        stored = await load_document(
            connection, source_id=source_id, external_id=external_id, for_update=True
        )
        if stored is None or stored.availability is DocumentAvailability.TOMBSTONED:
            return False
        granted = await load_access_grants(connection, document_id=stored.document_id)
        kept = _both(tuple(grant.subject for grant in granted), readers)
        access = authorization_fingerprint(
            document_id=stored.document_id,
            grants=_grants(stored.document_id, kept, observed_at),
        )
        document = stored.reconcile(
            title=stored.title,
            content_fingerprint=stored.content_fingerprint,
            authorization_fingerprint=access,
            source_version=stored.provenance.source_version,
            observed_at=observed_at,
        )
        if document is stored:
            return False
        # The fingerprint records the narrowed readers, so the next good
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


def _both(stored: Sequence[Subject], observed: Sequence[Subject]) -> list[Subject]:
    """The readers both sets let in; everyone stands for any reader."""
    everyone = Subject.public()
    if everyone in stored:
        return list(observed)
    if everyone in observed:
        return list(stored)
    return [subject for subject in stored if subject in set(observed)]


def build_chunks(
    normalized: NormalizedDocument,
    document: Document,
) -> tuple[Chunk, ...]:
    """Chunk a document by its structure and give each chunk its source anchor."""
    drafts = chunk_by_structure(normalized.parsed)
```

`narrow_access` writes the narrowed readers into the authorization fingerprint, the hash reconciliation compares on the next pass. If it kept the old fingerprint, a later good version with the old access would look unchanged, and the narrowed grants would stay.

In `src/knowledge_service/ingestion/synchronization.py`:

**1.** Find:

```python
"""Run one complete pass over a Source and publish what it offers.

The pass pages through the Source, normalizes each present record, and
reconciles it. Only after the last page does it tombstone the available
Documents it did not see: a failure part-way through raises before anything
is tombstoned, so a broken run never makes documents disappear.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecordState,
)

PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class SynchronizationSummary:
    """What one complete pass did."""

    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
    requeued_purges: tuple[JobId, ...]


async def synchronize_source(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    page_size: int = PAGE_SIZE,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    A Source that returns a checkpoint it returned before raises
    `InvalidSource` instead of paging forever.
    """
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
    quarantined: list[Quarantined] = []
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            if record.state is SourceRecordState.DELETED:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted one.
                quarantined.append(outcome)
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=source_readers(outcome.access),
                observed_at=observed_at,
            )
            changes[result.change] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

Replace with:

```python
"""Run one complete pass over a Source and publish what it offers.

The pass pages through the Source, normalizes each present record, and
reconciles it. Only after the last page does it tombstone the available
Documents it did not see: a failure part-way through raises before anything
is tombstoned, so a broken run never makes documents disappear.

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecordState,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class UnresolvedAccess:
    """A published Document whose Source named readers the directory lacks.

    The Document is readable only by the readers that did resolve.
    """

    external_id: str
    principals: tuple[UnresolvedPrincipal, ...]


@dataclass(frozen=True, slots=True)
class SynchronizationSummary:
    """What one complete pass did."""

    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
    requeued_purges: tuple[JobId, ...]
    unresolved: tuple[UnresolvedAccess, ...]


async def synchronize_source(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    """
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one, but whoever the Source no longer names loses it now.
                quarantined.append(outcome)
                if await narrow_access(
                    runtime,
                    source_id=source_id,
                    external_id=record.external_id,
                    readers=access.readers,
                    observed_at=observed_at,
                ):
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[result.change] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

**2.** Find:

```python
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
    )
    return SynchronizationSummary(
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
    )


def source_readers(access: SourceAccess) -> Sequence[Subject]:
    """Return who may read an item: everyone if public, otherwise nobody yet.

    Mapping a Source's principals to users and groups is identity work for
    M07; until then a non-public document is readable by nobody.
    """
    if access.public:
        return [Subject.public()]
    return []
```

Replace with:

```python
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
    )
    if unresolved:
        # Counts only: a principal may name a person.
        logger.warning(
            "unresolved source principals: %d in %d documents",
            sum(len(item.principals) for item in unresolved.values()),
            len(unresolved),
        )
    return SynchronizationSummary(
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )
```

In `src/knowledge_service/retrieval/evaluation_dataset.py`:

Find:

```python
        access = SourceAccess(public=folder is Folder.PUBLIC)
        summary = await synchronize_source(
            runtime,
            DirectorySource(corpus / folder, access=access),
            source_id=source_id,
            observed_at=observed_at,
        )
        if summary.quarantined:
            raise InvalidDataset(f"a Document in {folder}/ could not be ingested")


async def _embed_corpus(
```

Replace with:

```python
        access = SourceAccess(public=folder is Folder.PUBLIC)
        summary = await synchronize_source(
            runtime,
            DirectorySource(corpus / folder, access=access),
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=None,
        )
        if summary.quarantined:
            raise InvalidDataset(f"a Document in {folder}/ could not be ingested")


async def _embed_corpus(
```

**Check:** `uv run pyright src` reports `0 errors`. The tests that call `synchronize_source` without a directory, and `tests/test_deletions.py`, which imports the removed `source_readers`, fail until Steps 5 and 6.

### Step 5 — Coding step: examples and properties for the normalizer

**In plain words:** this step tests the translation on its own, without a database. Hand-picked examples pin the rules: what maps, what is malformed, what the directory refuses. Two property tests then let Hypothesis generate hundreds of reader lists to prove the general rules: order never changes the result, and a name that does not resolve never grants anything.

| File | Change |
|---|---|
| `tests/test_deletions.py` | Drops the test of the removed `source_readers`; the new file covers it |
| `tests/test_grant_normalization.py` | Mapping to the token's IDs; dedup and stable order; malformed and unknown principals; directory validation; reprs without names; the two properties |

In `tests/test_deletions.py`:

Find:

```python
"""Unit tests for what retrieval may read once Documents are tombstoned."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_service.access import Subject
from knowledge_service.ingestion.sources import SourceAccess
from knowledge_service.ingestion.synchronization import source_readers
from knowledge_service.persistence.chunks import searchable_chunks_statement


def test_searchable_chunks_belong_only_to_available_documents() -> None:
    statement = searchable_chunks_statement(UUID(int=1))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "documents.availability = 'available'" in sql


def test_only_public_items_get_readers_until_identity_mapping_exists() -> None:
    assert source_readers(SourceAccess(public=True)) == [Subject.public()]
    assert source_readers(SourceAccess(public=False, principals=("hr",))) == []
```

Replace with:

```python
"""Unit tests for what retrieval may read once Documents are tombstoned."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_service.persistence.chunks import searchable_chunks_statement


def test_searchable_chunks_belong_only_to_available_documents() -> None:
    statement = searchable_chunks_statement(UUID(int=1))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "documents.availability = 'available'" in sql
```

Create `tests/test_grant_normalization.py`:

```python
"""Source principals to Access Grant subjects: canonical, stable, fail-closed.

The examples pin the mapping; the properties show the result never depends
on the principals' order and that a principal the directory cannot resolve
never grants anything.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.access import Subject
from knowledge_service.identity.context import (
    ContextPolicy,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import VerifiedToken
from knowledge_service.ingestion.grant_normalization import (
    InvalidPrincipalDirectory,
    NormalizedAccess,
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
    normalize_access,
)
from knowledge_service.ingestion.sources import SourceAccess

ISSUER = "http://localhost:8180/realms/knowledge-service"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
CAROL = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03"
DIRECTORY = PrincipalDirectory(
    issuer=ISSUER,
    users={"carol@example.test": CAROL},
    groups={"engineering": "/engineering", "eng-all": "/engineering"},
)


class Name(str):
    """A string subclass could compare or hash as another name."""

    __slots__ = ()


ENGINEERING = Subject.group(group_id_for(ISSUER, "/engineering"))
CAROL_SUBJECT = Subject.user(user_id_for(ISSUER, CAROL))
MARKER = "SEEDED-SECRET-5e1a"

MALFORMED = UnresolvedReason.MALFORMED
UNKNOWN = UnresolvedReason.UNKNOWN


def normalized(*principals: str, directory: Any = DIRECTORY) -> NormalizedAccess:
    return normalize_access(
        SourceAccess(public=False, principals=principals), directory
    )


# --- Success: mapped principals become the token's own IDs ----------------------------


def test_mapped_principals_become_canonical_subjects() -> None:
    access = normalized("group:engineering", "user:carol@example.test")

    assert access.readers == tuple(
        sorted([ENGINEERING, CAROL_SUBJECT], key=lambda s: (s.kind, str(s.identifier)))
    )
    assert access.unresolved == ()


def test_a_source_group_is_the_group_a_token_for_that_path_carries() -> None:
    # alice's token lists /engineering; the Source calls the group "engineering".
    token = VerifiedToken(
        subject=ALICE,
        key_id="key-1",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        claims={
            "iss": ISSUER,
            "sub": ALICE,
            "azp": "open-webui",
            "groups": ["/engineering"],
        },
    )
    reader = build_authorization_context(
        token, ContextPolicy(trusted_clients=frozenset({"open-webui"}))
    )

    (subject,) = normalized("group:engineering").readers

    assert subject.identifier in reader.group_ids


def test_a_public_item_is_readable_by_everyone_with_or_without_a_directory() -> None:
    for directory in (DIRECTORY, None):
        access = normalize_access(SourceAccess(public=True), directory)
        assert access == NormalizedAccess(readers=(Subject.public(),), unresolved=())


def test_two_source_names_for_one_group_give_one_subject() -> None:
    assert normalized("group:engineering", "group:eng-all").readers == (ENGINEERING,)


def test_readers_come_back_in_one_order_within_a_kind() -> None:
    directory = PrincipalDirectory(
        issuer=ISSUER, groups={"engineering": "/engineering", "finance": "/finance"}
    )

    access = normalized("group:finance", "group:engineering", directory=directory)

    assert [str(subject.identifier) for subject in access.readers] == sorted(
        str(group_id_for(ISSUER, path)) for path in ("/engineering", "/finance")
    )


def test_an_id_with_a_colon_is_matched_whole() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, groups={"eng:all": "/engineering"})

    assert normalized("group:eng:all", directory=directory).readers == (ENGINEERING,)
    assert normalized("group:all", directory=directory).readers == ()


def test_a_path_with_a_trailing_slash_is_another_group() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, groups={"eng": "/engineering/"})

    assert normalized("group:eng", directory=directory).readers != (ENGINEERING,)


def test_the_same_names_from_another_issuer_are_other_subjects() -> None:
    other = PrincipalDirectory(
        issuer="https://idp.example.test/realms/other",
        groups={"engineering": "/engineering"},
    )

    assert normalized("group:engineering", directory=other).readers != (ENGINEERING,)


# --- Denial: anything unresolved grants nothing, and is reported ----------------------


def test_a_private_item_with_no_principals_is_readable_by_nobody() -> None:
    assert normalized() == NormalizedAccess(readers=(), unresolved=())


@pytest.mark.parametrize(
    ("principal", "reason"),
    [
        ("group:contractors", UNKNOWN),
        ("user:mallory@example.test", UNKNOWN),
        ("group:Engineering", UNKNOWN),
        ("user:engineering", UNKNOWN),
        ("group:carol@example.test", UNKNOWN),
        ("hr", MALFORMED),
        ("Group:engineering", MALFORMED),
        ("team:engineering", MALFORMED),
        ("group:", MALFORMED),
        ("group: engineering", MALFORMED),
        ("group:engineering ", MALFORMED),
        (":engineering", MALFORMED),
        ("public", MALFORMED),
    ],
)
def test_a_principal_that_does_not_resolve_grants_nothing(
    principal: str, reason: UnresolvedReason
) -> None:
    access = normalized(principal, "group:engineering")

    assert access.readers == (ENGINEERING,)
    assert access.unresolved == (UnresolvedPrincipal(principal, reason),)


def test_without_a_directory_no_principal_resolves() -> None:
    access = normalized("group:engineering", "hr", directory=None)

    assert access.readers == ()
    assert access.unresolved == (
        UnresolvedPrincipal("group:engineering", UNKNOWN),
        UnresolvedPrincipal("hr", MALFORMED),
    )


def test_a_group_principal_never_resolves_through_the_user_mapping() -> None:
    directory = PrincipalDirectory(issuer=ISSUER, users={"admins": CAROL})

    assert normalized("group:admins", directory=directory).readers == ()


# --- The directory: exact, private, and read-only -------------------------------------


@pytest.mark.parametrize(
    "settings",
    [
        {"issuer": ""},
        {"issuer": " " + ISSUER},
        {"issuer": ISSUER, "users": {"": CAROL}},
        {"issuer": ISSUER, "users": {"carol ": CAROL}},
        {"issuer": ISSUER, "users": {"carol": ""}},
        {"issuer": ISSUER, "users": {"carol": 7}},
        {"issuer": ISSUER, "users": [("carol", CAROL)]},
        {"issuer": ISSUER, "groups": {"engineering": "engineering"}},
        {"issuer": ISSUER, "groups": {"everyone": "/"}},
        {"issuer": ISSUER, "groups": {"engineering": "/engineering\n"}},
        {"issuer": ISSUER, "groups": {"engineering": "/a~/b"}},
        {"issuer": ISSUER, "groups": {"engineering": "/eng" + chr(0x200B)}},
        {"issuer": ISSUER, "groups": {"engineering": "/e" + chr(0) + "g"}},
        {"issuer": ISSUER, "groups": {"engineering": "/" + "e" * 255}},
        {"issuer": ISSUER, "users": {"carol": "ca" + chr(0x200B) + "rol"}},
        {"issuer": chr(0x200B) + ISSUER},
        {"issuer": ISSUER, "users": {Name("carol"): CAROL}},
        {"issuer": ISSUER, "users": {"carol": Name(CAROL)}},
    ],
    ids=[
        "empty-issuer",
        "padded-issuer",
        "empty-user-id",
        "padded-user-id",
        "empty-subject",
        "number-subject",
        "list-of-pairs",
        "relative-path",
        "root-path",
        "padded-path",
        "tilde-path",
        "hidden-path",
        "nul-path",
        "long-path",
        "hidden-subject",
        "hidden-issuer",
        "str-subclass-key",
        "str-subclass-subject",
    ],
)
def test_a_directory_with_a_partial_or_padded_name_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(InvalidPrincipalDirectory):
        PrincipalDirectory(**settings)


def test_the_directory_keeps_its_own_copy_and_cannot_be_changed() -> None:
    groups = {"engineering": "/engineering"}
    directory = PrincipalDirectory(issuer=ISSUER, groups=groups)

    groups["contractors"] = "/engineering"

    assert normalized("group:contractors", directory=directory).readers == ()
    with pytest.raises(TypeError):
        directory.groups["contractors"] = "/engineering"  # type: ignore[index]


# --- Telemetry: the principal is kept, but never shown --------------------------------


def test_an_unresolved_principal_is_kept_but_left_out_of_reprs() -> None:
    access = normalized(f"user:{MARKER}", f"{MARKER}")

    assert {item.principal for item in access.unresolved} == {
        f"user:{MARKER}",
        MARKER,
    }
    assert MARKER not in repr(access) + str(access)


def test_the_directory_repr_names_no_one() -> None:
    directory = PrincipalDirectory(
        issuer=ISSUER, users={MARKER: CAROL}, groups={"x": f"/{MARKER}"}
    )

    assert MARKER not in repr(directory) + str(directory)


# --- Properties: order never matters, and unresolved never grants ---------------------


KNOWN = ["group:engineering", "group:eng-all", "user:carol@example.test"]
PRINCIPALS = st.lists(
    st.one_of(
        st.sampled_from(KNOWN),
        st.builds(
            "{}:{}".format,
            st.sampled_from(["user", "group", "Group", "team", ""]),
            st.text(max_size=12),
        ),
        st.text(max_size=20),
    ),
    unique=True,
    max_size=12,
).filter(lambda principals: all(principal.strip() for principal in principals))


@given(principals=PRINCIPALS, data=st.data())
def test_normalization_does_not_depend_on_the_order_of_principals(
    principals: list[str], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(principals))

    assert normalized(*shuffled) == normalized(*principals)


@given(principals=PRINCIPALS)
def test_an_unresolved_principal_never_grants_and_is_always_reported(
    principals: list[str],
) -> None:
    access = normalized(*principals)
    unresolved = {item.principal for item in access.unresolved}
    resolved = [principal for principal in principals if principal not in unresolved]

    # Every principal is either a reader or reported, never both.
    assert unresolved <= set(principals)
    assert set(resolved) <= set(KNOWN)
    # Taking every unresolved principal away changes no reader.
    assert normalized(*resolved).readers == access.readers
    assert normalized(*resolved).unresolved == ()
    # And nothing beyond the mapped subjects is ever granted.
    assert set(access.readers) <= {ENGINEERING, CAROL_SUBJECT}
```

The second property test is the quarantine proof: it removes every unresolved principal from the input and checks the readers do not change. If an unresolved name granted anything, removing it would change the result.

**Check:**

```bash
uv run pytest tests/test_grant_normalization.py tests/test_deletions.py tests/test_authorization_context.py -q
```

Expected: `137 passed`.

### Step 6 — Coding step: synchronization and retrieval against PostgreSQL

**In plain words:** this step checks the whole path with a real database. A folder's file names engineering, a contractor group nobody mapped, and a malformed name. After a pass, the stored grants are exactly engineering and carol, the summary reports the other two, and retrieval's own eligibility check lets alice see the file while bob cannot. Further tests break the file mid-way and check that removed readers lose it at once, added readers wait, and a later good version restores the right access.

| File | Change |
|---|---|
| `tests/test_document_title_integration.py` | Passes `principal_directory=None` |
| `tests/test_synchronization_integration.py` | Every existing call passes `principal_directory=None`; new tests for mapped grants and reports, retrieval by alice and bob, mapping added later, readers removed or added while a file is broken, recovery after narrowing, and a record offered twice |

In `tests/test_document_title_integration.py`:

Find:

```python
async def test_a_synchronized_file_keeps_its_title_and_a_second_pass_writes_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_bytes(HANDBOOK)
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )
    before = await stored(runtime, "handbook.md")

    second = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER
    )

    assert before[0].title == "handbook"
    assert second.changes == {Change.UNCHANGED: 1}
    assert await stored(runtime, "handbook.md") == before

```

Replace with:

```python
async def test_a_synchronized_file_keeps_its_title_and_a_second_pass_writes_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_bytes(HANDBOOK)
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    before = await stored(runtime, "handbook.md")

    second = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )

    assert before[0].title == "handbook"
    assert second.changes == {Change.UNCHANGED: 1}
    assert await stored(runtime, "handbook.md") == before

```

In `tests/test_synchronization_integration.py`:

**1.** Find:

```python

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import QuarantineReason
from knowledge_service.ingestion.reconciliation import Change
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.chunks import load_searchable_chunks
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)

```

Replace with:

```python

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
from knowledge_service.ingestion.normalization import QuarantineReason
from knowledge_service.ingestion.reconciliation import Change
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import (
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.chunks import load_searchable_chunks
from knowledge_service.persistence.schema import access_grants, chunks, documents
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)

```

**2.** Find:

```python
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    (tmp_path / "old-policy.md").write_text("# Old policy\n\nRetired rules.\n")
    (tmp_path / "empty.md").write_text("")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))

    first = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT, page_size=1
    )
    (tmp_path / "old-policy.md").unlink()
    second = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER, page_size=1
    )

    assert first.changes == {Change.CREATED: 2}
    assert [q.reason for q in first.quarantined] == [QuarantineReason.EMPTY_CONTENT]
    assert first.tombstones == ()
    assert second.changes == {Change.UNCHANGED: 1}
```

Replace with:

```python
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    (tmp_path / "old-policy.md").write_text("# Old policy\n\nRetired rules.\n")
    (tmp_path / "empty.md").write_text("")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))

    first = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        page_size=1,
        principal_directory=None,
    )
    (tmp_path / "old-policy.md").unlink()
    second = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        page_size=1,
        principal_directory=None,
    )

    assert first.changes == {Change.CREATED: 2}
    assert [q.reason for q in first.quarantined] == [QuarantineReason.EMPTY_CONTENT]
    assert first.tombstones == ()
    assert second.changes == {Change.UNCHANGED: 1}
```

**3.** Find:

```python
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "a.md").write_text("# A\n\nFirst file.\n")
    (tmp_path / "b.md").write_text("# B\n\nSecond file.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FailingSecondPage(source),
            source_id=SOURCE_ID,
            observed_at=LATER,
            page_size=1,
        )

    searchable = await searchable_text(runtime)
    assert "First file." in searchable
    assert "Second file." in searchable

```

Replace with:

```python
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "a.md").write_text("# A\n\nFirst file.\n")
    (tmp_path / "b.md").write_text("# B\n\nSecond file.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FailingSecondPage(source),
            source_id=SOURCE_ID,
            observed_at=LATER,
            page_size=1,
            principal_directory=None,
        )

    searchable = await searchable_text(runtime)
    assert "First file." in searchable
    assert "Second file." in searchable

```

**4.** Find:

```python
    (tmp_path / "salaries.md").write_text("# Salaries\n\nConfidential.\n")
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=("hr",))
    )

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    assert summary.changes == {Change.CREATED: 1}
    async with runtime.transaction() as connection:
        grants = (
            await connection.execute(text("SELECT count(*) FROM access_grants"))
        ).scalar()
    assert grants == 0


@pytest.mark.integration
async def test_a_file_that_stops_parsing_keeps_its_last_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )
    (tmp_path / "handbook.md").write_bytes(b"\xff\xfe broken")

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER
    )

    assert [q.reason for q in summary.quarantined] == [
        QuarantineReason.UNDECODABLE_TEXT
    ]
    assert summary.tombstones == ()
```

Replace with:

```python
    (tmp_path / "salaries.md").write_text("# Salaries\n\nConfidential.\n")
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=("hr",))
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert summary.changes == {Change.CREATED: 1}
    assert [item.principals for item in summary.unresolved] == [
        (UnresolvedPrincipal("hr", UnresolvedReason.MALFORMED),)
    ]
    async with runtime.transaction() as connection:
        grants = (
            await connection.execute(text("SELECT count(*) FROM access_grants"))
        ).scalar()
    assert grants == 0


ISSUER = "http://localhost:8180/realms/knowledge-service"
CAROL = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03"
DIRECTORY = PrincipalDirectory(
    issuer=ISSUER,
    users={"carol@example.test": CAROL},
    groups={"engineering": "/engineering"},
)


async def readable_by(runtime: DatabaseRuntime, reader: AuthorizationContext) -> int:
    """How many Chunks retrieval's own eligibility check lets `reader` see."""
    statement = (
        sa.select(sa.func.count())
        .select_from(
            chunks.join(documents, chunks.c.document_id == documents.c.document_id)
        )
        .where(eligible_chunks_predicate(reader))
    )
    async with runtime.transaction() as connection:
        return (await connection.execute(statement)).scalar_one()


@pytest.mark.integration
async def test_mapped_principals_become_grants_and_the_rest_are_reported(
    runtime: DatabaseRuntime, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    principals = (
        "group:contractors",
        "group:engineering",
        "hr",
        "user:carol@example.test",
    )
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=principals)
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(access_grants.c.subject_kind, access_grants.c.subject_id)
            )
        ).all()
    assert sorted((kind, str(subject)) for kind, subject in rows) == [
        ("group", str(group_id_for(ISSUER, "/engineering").value)),
        ("user", str(user_id_for(ISSUER, CAROL).value)),
    ]
    assert [item.external_id for item in summary.unresolved] == ["roadmap.md"]
    assert [item.principals for item in summary.unresolved] == [
        (
            UnresolvedPrincipal("group:contractors", UnresolvedReason.UNKNOWN),
            UnresolvedPrincipal("hr", UnresolvedReason.MALFORMED),
        )
    ]
    assert "unresolved source principals: 2 in 1 documents" in caplog.messages
    assert "contractors" not in caplog.text


@pytest.mark.integration
async def test_retrieval_lets_only_the_mapped_readers_see_the_document(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    source = DirectorySource(
        tmp_path,
        access=SourceAccess(public=False, principals=("group:engineering",)),
    )
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    alice = AuthorizationContext(
        user_id=user_id_for(ISSUER, "alice"),
        group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
    )
    bob = AuthorizationContext(
        user_id=user_id_for(ISSUER, "bob"),
        group_ids=frozenset({group_id_for(ISSUER, "/finance")}),
    )

    assert await readable_by(runtime, alice) > 0
    assert await readable_by(runtime, bob) == 0


@pytest.mark.integration
async def test_mapping_a_principal_later_opens_the_document_on_the_next_pass(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    source = DirectorySource(
        tmp_path,
        access=SourceAccess(public=False, principals=("group:engineering",)),
    )
    alice = AuthorizationContext(
        user_id=user_id_for(ISSUER, "alice"),
        group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
    )

    first = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    hidden = await readable_by(runtime, alice)
    second = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    assert (hidden, len(first.unresolved), len(second.unresolved)) == (0, 1, 0)
    assert await readable_by(runtime, alice) > 0


@pytest.mark.integration
async def test_a_file_that_stops_parsing_keeps_its_last_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    (tmp_path / "handbook.md").write_bytes(b"\xff\xfe broken")

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )

    assert [q.reason for q in summary.quarantined] == [
        QuarantineReason.UNDECODABLE_TEXT
    ]
    assert summary.tombstones == ()
```

**5.** Find:

```python
async def test_an_item_reported_deleted_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    summary = await synchronize_source(
        runtime, ReportsDeletion(), source_id=SOURCE_ID, observed_at=LATER
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


```

Replace with:

```python
async def test_an_item_reported_deleted_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    summary = await synchronize_source(
        runtime,
        ReportsDeletion(),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


```

**6.** Find:

```python
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = OffersThenDeletes(
        DirectorySource(tmp_path, access=SourceAccess(public=True))
    )

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


```

Replace with:

```python
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = OffersThenDeletes(
        DirectorySource(tmp_path, access=SourceAccess(public=True))
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


```

**7.** Find:

```python


@pytest.mark.integration
async def test_a_repeated_checkpoint_is_refused(runtime: DatabaseRuntime) -> None:
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await synchronize_source(
            runtime, StuckCheckpoint(), source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
```

Replace with:

```python


@pytest.mark.integration
async def test_a_repeated_checkpoint_is_refused(runtime: DatabaseRuntime) -> None:
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await synchronize_source(
            runtime,
            StuckCheckpoint(),
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )


def broken(path: Path) -> None:
    """Make the file one normalization quarantines."""
    path.write_bytes(b"\xff\xfe broken")


ALICE = AuthorizationContext(
    user_id=user_id_for(ISSUER, "alice"),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
BOB = AuthorizationContext(
    user_id=user_id_for(ISSUER, "bob"),
    group_ids=frozenset({group_id_for(ISSUER, "/finance")}),
)
ANYONE = AuthorizationContext(user_id=user_id_for(ISSUER, "anyone"))


async def sync(
    runtime: DatabaseRuntime, root: Path, access: SourceAccess, at: datetime
) -> SynchronizationSummary:
    return await synchronize_source(
        runtime,
        DirectorySource(root, access=access),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after", "loses"),
    [
        (("group:engineering",), ("user:carol@example.test",), ALICE),
        ((), ("group:engineering",), ANYONE),
    ],
    ids=["group-removed", "public-made-private"],
)
async def test_a_reader_removed_while_the_file_is_broken_loses_it_at_once(
    runtime: DatabaseRuntime,
    tmp_path: Path,
    before: tuple[str, ...],
    after: tuple[str, ...],
    loses: AuthorizationContext,
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    access = SourceAccess(public=not before, principals=before)
    await sync(runtime, tmp_path, access, OBSERVED_AT)
    assert await readable_by(runtime, loses) > 0
    broken(tmp_path / "roadmap.md")

    summary = await sync(
        runtime, tmp_path, SourceAccess(public=False, principals=after), LATER
    )

    assert len(summary.quarantined) == 1
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_by(runtime, loses) == 0


@pytest.mark.integration
async def test_a_reader_added_while_the_file_is_broken_waits_for_a_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    path = tmp_path / "roadmap.md"
    path.write_text("# Roadmap\n\nEngineering only.\n")
    engineering = SourceAccess(public=False, principals=("group:engineering",))
    await sync(runtime, tmp_path, engineering, OBSERVED_AT)
    broken(path)
    wider = SourceAccess(public=True)

    await sync(runtime, tmp_path, wider, LATER)
    hidden_from_bob = await readable_by(runtime, BOB)
    path.write_text("# Roadmap\n\nEveryone now.\n")
    await sync(runtime, tmp_path, wider, LATER + timedelta(hours=1))

    assert (hidden_from_bob, await readable_by(runtime, ALICE) > 0) == (0, True)
    assert await readable_by(runtime, BOB) > 0


@pytest.mark.integration
async def test_access_narrowed_while_broken_is_restored_by_a_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    path = tmp_path / "roadmap.md"
    path.write_text("# Roadmap\n\nEngineering only.\n")
    engineering = SourceAccess(public=False, principals=("group:engineering",))
    await sync(runtime, tmp_path, engineering, OBSERVED_AT)
    broken(path)
    await sync(
        runtime,
        tmp_path,
        SourceAccess(public=False, principals=("user:carol@example.test",)),
        LATER,
    )
    path.write_text("# Roadmap\n\nEngineering only.\n")

    summary = await sync(runtime, tmp_path, engineering, LATER + timedelta(hours=1))

    # The stored fingerprint names the narrowed readers, so this is a change.
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_by(runtime, ALICE) > 0


class OffersTwice:
    """A Source that offers one item twice in a pass, with different access."""

    def __init__(self, first: SourceAccess, last: SourceAccess) -> None:
        self.accesses = (first, last)

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        index = 0 if checkpoint is None else 1
        record = SourceRecord.present(
            external_id="roadmap.md",
            version=f"v{index}",
            media_type="text/markdown",
            content=b"# Roadmap\n\nEngineering only.\n",
            access=self.accesses[index],
            metadata=SourceMetadata(
                title="roadmap", modified_at=OBSERVED_AT, size_bytes=30
            ),
        )
        return SourcePage(
            records=(record,), next_checkpoint="page-2" if index == 0 else None
        )


@pytest.mark.integration
async def test_a_record_offered_twice_is_reported_as_last_seen(
    runtime: DatabaseRuntime,
) -> None:
    source = OffersTwice(
        SourceAccess(public=False, principals=("group:contractors",)),
        SourceAccess(public=False, principals=("group:engineering",)),
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()
```

`readable_by` asks the database the same question retrieval asks, through `eligible_chunks_predicate`, so these tests check what really leaves storage, not just which rows exist.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_synchronization_integration.py tests/test_document_title_integration.py -q
```

Expected: `18 passed`.

### Step 7 — No coding in this step: break it on purpose

This step removes the narrowing of broken files, watches the right tests fail, and puts it back.

**Action 1:** in `synchronize_source` in `src/knowledge_service/ingestion/synchronization.py`, delete these lines:

```python
                if await narrow_access(
                    runtime,
                    source_id=source_id,
                    external_id=record.external_id,
                    readers=access.readers,
                    observed_at=observed_at,
                ):
                    changes[Change.ACCESS] += 1
```

**Action 2:** run the integration file from Step 6's check, `tests/test_synchronization_integration.py` only. Expected: `3 failed, 12 passed`:

- `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once[group-removed]`
- `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once[public-made-private]`
- `test_access_narrowed_while_broken_is_restored_by_a_good_version`

The first two show the leak the review found: a group the Source removed, or anyone at all after the file was made private, still reads the last good version.

**Action 3:** put the lines back and run the file again: `15 passed`. Ruff would have reported the unused `narrow_access` import while the lines were gone.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 8 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result.

**Action 1:** update two documents.

In `docs/architecture.md`:

**1.** Find:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
```

Replace with:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
```

**2.** Find:

```markdown
    participant Del as deletions
    participant DB as PostgreSQL
    Caller->>Sync: source, source_id, observed_at
    loop until next_checkpoint is None (a repeated checkpoint raises)
        Sync->>Source: fetch_page(checkpoint, page_size)
```

Replace with:

```markdown
    participant Del as deletions
    participant DB as PostgreSQL
    Caller->>Sync: source, source_id, observed_at, principal_directory
    loop until next_checkpoint is None (a repeated checkpoint raises)
        Sync->>Source: fetch_page(checkpoint, page_size)
```

**3.** Find:

```markdown
                Norm-->>Sync: NormalizedDocument or Quarantined
                alt NormalizedDocument
                    Sync->>Rec: NormalizedDocument + readers
                    Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
                else Quarantined
                    Sync->>Sync: keep it in seen, so its last good version stays
                end
            end
```

Replace with:

```markdown
                Norm-->>Sync: NormalizedDocument or Quarantined
                alt NormalizedDocument
                    Sync->>Sync: normalize_access: mapped principals become readers, the rest are kept as unresolved
                    Sync->>Rec: NormalizedDocument + readers
                    Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
                else Quarantined
                    Sync->>Sync: keep it in seen, so its last good version stays
                    Sync->>Rec: narrow_access: keep only readers old and new access both name
                end
            end
```

**4.** Find:

````markdown
    Sync->>Del: requeue_unfinished_purges
    Del->>DB: queue a new purge for each exhausted one
    Sync-->>Caller: SynchronizationSummary
    Note over Del,DB: later, per Job: purge_document_chunks deletes Chunks of a still-tombstoned Document
```
````

Replace with:

````markdown
    Sync->>Del: requeue_unfinished_purges
    Del->>DB: queue a new purge for each exhausted one
    Sync-->>Caller: SynchronizationSummary (with unresolved principals)
    Note over Del,DB: later, per Job: purge_document_chunks deletes Chunks of a still-tombstoned Document
```
````

In `docs/threat-model.md`:

**1.** Find:

```markdown
## Abuse cases and mitigations

### Ingestion

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable by nobody until its principals are mapped | Source group normalization (M07-T06) |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

```

Replace with:

```markdown
## Abuse cases and mitigations

### Ingestion

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable only by the principals `normalize_access` resolves | None |
| A Source principal that is unmapped, malformed, or spelled like another widens who reads a Document | `normalize_access` fails closed: only `user:<id>` and `group:<id>` that the Source's `PrincipalDirectory` maps exactly become grants, as the same UUIDv5 IDs tokens carry; the rest grant nothing and are reported in the synchronization summary, with only their count logged; a directory may map only to subjects and paths a token could carry, and every caller of `synchronize_source` must name its directory | Confluence permission mapping (M08-T14) |
| A reader the Source removed keeps reading the last good version of a file that stopped parsing | `narrow_access` applies the new access to a quarantined record at once, keeping only readers its old and new access both name; nobody gains access until the record normalizes again | None |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

```

**2.** Find:

```markdown
### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | Source group normalization (M07-T06) |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |
```

Replace with:

```markdown
### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | None |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |
```

**3.** Find:

```markdown
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; Source groups get their own mapping in M07-T06. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
```

Replace with:

```markdown
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
uv run pytest tests/test_grant_normalization.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2733 passed for `make test-unit`, 46 more than M07-T05, and 427 passed for `make test-integration`, 8 more.

**Action 3:** complete the evidence file:

- **Design:** the principal format, the directory and its rules, the shared IDs, fail-closed reporting, and narrowing for broken files.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why the token's own IDs and name rules, why unresolved principals still let the Document publish, and why broken files only shrink; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T06 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T07, and commit with `m07-t06: normalize source access grants`.

## Reflection questions

1. Synchronization calls `normalize_access` and gets readers back. What does it no longer need to know about how a Source writes its user and group names?
2. A later change wants to skip reading grants for files whose content did not change, to save time. Why would that break revocation, and which test would catch it?
3. A Source names `group:Engineering`, but the directory maps `engineering`. What happens, who notices, and why is that better than matching names without regard to case?
4. Suppose `narrow_access` applied the Source's new readers fully instead of the overlap. Describe a file and two passes where that shows someone text they were never meant to see.
5. Carol is mapped as `user:carol@example.test` and is in no group. A file names her and `group:finance`. Write which readers it gets, and which of alice, bob, and carol can retrieve it.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `plain_group_path` and `visible_name` are public, and the Authorization Context tests still pass (89).
- [ ] `knowledge_service.ingestion.grant_normalization` maps `user:<id>` and `group:<id>` through a `PrincipalDirectory` to the token's own IDs, distinct and sorted, and reports everything else without granting it.
- [ ] Synchronization requires a directory, reports unresolved principals per file, logs only counts, and narrows the readers of a file that stops parsing.
- [ ] The normalizer's examples and property tests pass (137 with the context and deletion tests).
- [ ] The integration tests pass against PostgreSQL (18).
- [ ] You ran the deliberate break, saw the 3 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe the normalizer and its limits.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
