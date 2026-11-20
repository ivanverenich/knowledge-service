# M07-T15 — Build the authorization adversarial suite

Task: [M07-T15 — Build the authorization adversarial suite](../curriculum/milestones/M07-identity-and-authorization/M07-T15-build-the-authorization-adversarial-suite.md)

## What you will do

This lesson builds one reusable set of attack tests for "who may read what". Earlier lessons each tested one door: search, the Citation check, History. This lesson builds one generator of tricky situations and walks every person in it through every door, checking that nobody ever sees a Document they may not read.

- **A generator of scenarios.** Each seed makes a small company: people with real token claims, and Documents granted to everyone, to one person, or to one group.
- **The hard cases, every time.** Every scenario contains nested groups, look-alike group names, a public and a private Document with the same text, revoked and deleted Documents, and both English and Ukrainian.
- **Every door.** Vector, lexical, and hybrid search; the Evidence source; the answer stream over HTTP; the Citation and Evidence checks given real IDs; Conversations and History; and a Document taken away while the model writes.
- **Zero leakage, proved.** Each Document carries a unique marker word. No marker of a Document the reader may not see may appear anywhere: in results, the model's prompt, the bytes sent, any log field, or any audit row.
- **Proof that the proof works.** The suite is run against fifteen deliberately broken versions of the code, and it must fail on every one.

| The task asks for | Where it happens |
|---|---|
| Generate users | Step 2: `Person`, built from token claims |
| Nested group scenarios | Step 2: `/engineering` and `/engineering/backend`, plus look-alikes |
| Identical public/private text | Step 2: the twin Documents |
| Revocation races | Step 4: `test_a_document_taken_away_while_the_model_writes_is_withdrawn` |
| Direct IDs | Step 4: `test_a_documents_real_ids_never_let_another_person_read_it` |
| Bilingual probes | Step 2: `PROBES` in English and Ukrainian |
| As reusable tests | Step 2: `tests/authorization_scenarios.py`, a module any test can import |
| Retrieval, answer, citation, Conversation interfaces | Step 4 |
| Tool interfaces | None exist yet; tools join the suite as a probe in M11 |
| Success, highest-risk edge, typed failure or denial | Steps 3 and 4: readers get exactly their Documents; a race mid-write; `WITHDRAWN` and `Unauthenticated` |
| Telemetry holds no seeded text | Step 4: every test scans every field of every log record |
| Update only what the change made inaccurate | Step 6: `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 4 write test code. Steps 1, 5, and 6 do not, though Step 6 edits one document. No application code changes: the suite found no leak.

Terms used in this lesson:

- **Adversarial test:** a test written like an attack. It sets up the situation most likely to break a rule, then checks that the rule held.
- **Oracle:** an independent answer to compare against. Here it is `may_read`, the access rule written in a few lines of plain Python, without the SQL.
- **Marker:** a unique made-up word, such as `mark3s611720`, planted in one Document's title and text. If it shows up somewhere, that Document leaked there.
- **Seed:** a number that makes the random generator produce the same scenario every time, so a failure can be reproduced.
- **Mutant:** a copy of the code with one small deliberate bug, such as "any group matches". A good test suite fails on it; a mutant that passes shows a hole in the tests.
- **Direct ID:** a real identifier of a Document or Chunk, used by someone who should not have it, for example copied from someone else's answer.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already existed.** `test_search_security_contract_integration.py` runs random corpora through the searches; `test_existence_leaks_integration.py` checks that a forbidden Document looks like a missing one; `test_revocation_integration.py` and `test_history_integration.py` cover revocation and History. Each builds its own small world for one door.
- **One world, every door.** The new generator is a plain module, not a test, so any later test can import it. The suite then runs the same scenarios through every door, so a rule broken at only one door still shows up.
- **People from real tokens.** Each person's Authorization Context is built by `build_authorization_context` from token claims, as a real request's is. So group paths become Group IDs exactly as in production, and nested or look-alike paths are tested where they are decided.
- **No inheritance between groups.** In Keycloak, `/engineering/backend` is its own group. A member of it is not a member of `/engineering` unless the token says so. The oracle says the same, and the suite proves the code agrees.
- **Every Document is the best match.** All Documents are about one topic and sit exactly on the query vector. So for every reader, the Documents they may not see are as good a match as their own, and only the access rule keeps them out.
- **Twins test provenance.** The twin Documents share one body text, so the text itself leaks nothing. What may leak is the private twin's title, Document ID, or Chunk ID, so the suite also searches for those IDs.
- **Direct IDs for every fate.** Each Document is first also readable by a hidden snapshot reader, whose search captures its real identifiers. Then the Document gets its real readers and is revoked or deleted. So the Citation check is tried with real IDs of other people's, revoked, and deleted Documents.
- **A model that repeats everything.** The test model cites every Evidence it is shown and repeats every marker in its prompt. Whatever reached the model would reach the client, so the stream itself becomes a leak detector.
- **Positive checks too.** "No forbidden marker" alone passes when a door returns nothing at all. So each door must also return exactly what the reader may read.
- **Logs, all of them.** `caplog.text` shows only the message line. The suite scans every field of every log record, the `extra` ones too, because that is where the service puts its structured fields.
- **What the review found.** An independent adversarial review attacked the first version with seven new mutants, and all seven passed. Each is now caught:
  - Deleted and revoked Documents were never tried by direct ID, because their IDs could not be captured. The snapshot reader now captures every Document's IDs.
  - The owner's own History was never checked after a revocation. The test now revokes a cited Document and expects the Answer withheld.
  - Only log messages were scanned, not their structured fields, and only in one test. Every test now scans every field.
  - The race test did not check that the stream ended properly. It now checks the status, the completion, and a `denied` audit row.
  - Only markers were searched, never Document or Chunk IDs. The IDs of forbidden Documents are now searched too.
  - `truncated` was never tested, because every search returned everything. Each search now also runs with a limit of one.
- **Known limits.** The fake embedding model gives every question the same vector, so only lexical search really depends on the question's language. Conversations are tested at the store, since there is no Conversation endpoint yet. There are no agent tools and no caches yet; each joins the suite as a probe when it exists.

## Walkthrough

Before the integration checks, start PostgreSQL and set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 5 checks the last prediction.

Create `docs/evidence/M07-T15-build-the-authorization-adversarial-suite.md`:

```markdown
# M07-T15 — Build the authorization adversarial suite evidence

## Invariant

For every seeded scenario, every person, and every interface a reader can
reach (retrieval, the Evidence source, the answer stream, the Citation and
Evidence checks, Conversations, and History), the reader gets exactly the
Documents the plain-Python rule allows, and no marker or identifier of any
other Document appears in what they get, in the model's prompt, in the bytes
sent, in any log record, or in any audit row.

## Expected failing check

Before the change, `uv run pytest tests/test_authorization_suite_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_authorization_suite_integration.py`.

## Predictions

- Whether a member of `/engineering/backend` reads a Document granted to `/engineering`:
- What a person gets from the Citation check when they cite another person's Document by its real IDs:
- What alice's History shows after a Document her earlier Answer cited is revoked:
- How many suite tests fail if a group path also grants its parent group:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the scenario generator

**In plain words:** this file invents a small company for each seed: four people with group memberships, and a dozen Documents granted to different people and groups. It always includes the tricky cases: a parent and a child group, groups whose names only look alike, two Documents with the same text, and Documents that are revoked or deleted. It also writes down, in plain Python, who may read what, so tests can compare the real code with it.

| Name | What it does |
|---|---|
| `Person` | A User by token claims; `context()` builds the real Authorization Context |
| `Secret` | One Document: marker, language, audience, fate, and whether it is a twin |
| `Fate` | `kept`, `revoked`, or `deleted` |
| `scenario(seed)` | The reproducible company: a fixed core of risks, plus random extras |
| `may_read` | The access rule in plain Python: the oracle |
| `ScenarioAuthenticator` | Gives each person's token its caller, for HTTP tests |
| `leaked` | Which markers appear anywhere in what was observed |

Create `tests/authorization_scenarios.py`:

```python
"""Seeded authorization scenarios that any interface's tests can reuse.

A scenario is a small company: people with tokens, and Documents with
Access Grants. Every Document is about the same topic and sits exactly on
the query vector, so for every reader the Documents they may not see are as
good a match as the ones they may. Each scenario holds:

- **People from real tokens.** Each person's Authorization Context is built
  by `build_authorization_context` from token claims, as a request's is, so
  group paths become Group IDs exactly as in production.
- **Nested and look-alike groups.** `/engineering` and
  `/engineering/backend` are two groups: a member of one does not read the
  other's Documents. `/Engineering` and `/engineering-ops` only look like
  `/engineering`.
- **Identical text.** A public and a private Document share the same body
  text, word for word. A reader of only the public one may see the text,
  but never the private one's title, identifiers, or provenance.
- **Revocations and deletions.** Some Documents are published to someone
  and then taken away, and some are deleted from the Source, so they are
  tombstoned while their Chunks are still stored; nobody may read either.
- **Both languages.** Documents and probes are in English and Ukrainian.

Every Document carries a unique marker word in its title and, unless it is
one of the twins, in its text. `may_read` states the eligibility rule in
plain Python, independently of the SQL; a test compares what an interface
returns with it, and searches everything a reader or a log could see for the
markers of Documents the reader may not read.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    ContextPolicy,
    Unauthenticated,
    build_authorization_context,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import VerifiedToken
from knowledge_service.roles import ApplicationRole

ISSUER = "https://identity.example/realms/knowledge"
CLIENT = "open-webui"
POLICY = ContextPolicy(trusted_clients=frozenset({CLIENT}))
EXPIRES_AT = datetime(2030, 1, 1, tzinfo=UTC)
# How long after publishing a Document is revoked or deleted.
LATER = timedelta(hours=1)

ENGINEERING = "/engineering"
BACKEND = "/engineering/backend"
GROUP_PATHS = (
    ENGINEERING,
    BACKEND,
    # Look-alikes: other groups whose paths resemble `/engineering`.
    "/Engineering",
    "/engineering-ops",
    "/finance/payroll",
)
PROBES = {
    "en": "How many days of annual leave do I get?",
    "uk": "Скільки днів щорічної відпустки я маю?",
}
TOPIC = {
    "en": "The annual leave policy gives every employee days of annual leave.",
    "uk": "Політика щорічної відпустки дає кожному працівнику дні щорічної відпустки.",
}


class Fate(StrEnum):
    """What happens to a Document after it is published."""

    KEPT = "kept"
    REVOKED = "revoked"
    DELETED = "deleted"


class Audience(StrEnum):
    """Who a Document is granted to."""

    PUBLIC = "public"
    USER = "user"
    GROUP = "group"
    NOBODY = "nobody"


@dataclass(frozen=True, slots=True)
class Person:
    """A User, by the claims of the token they would send."""

    name: str
    groups: tuple[str, ...]

    @property
    def subject(self) -> str:
        return f"sub-{self.name}"

    def token(self) -> VerifiedToken:
        return VerifiedToken(
            subject=self.subject,
            key_id="test-key",
            expires_at=EXPIRES_AT,
            claims={
                "iss": ISSUER,
                "sub": self.subject,
                "azp": CLIENT,
                "groups": list(self.groups),
            },
        )

    def context(self) -> AuthorizationContext:
        """The Authorization Context a request with this token gets."""
        return build_authorization_context(self.token(), POLICY)

    @property
    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.name}"}


@dataclass(frozen=True, slots=True)
class Secret:
    """One Document: its marker, language, audience, and fate."""

    marker: str
    language: str
    audience: Audience
    # The person or group path the Document is granted to, if any.
    grantee: str | None
    fate: Fate
    # Twins share one body text and carry their marker only in the title.
    twin: bool

    @property
    def external_id(self) -> str:
        return f"{self.marker}.txt"

    @property
    def title(self) -> str:
        return f"{self.marker} {PROBES[self.language]}"

    @property
    def text(self) -> str:
        if self.twin:
            return TOPIC[self.language]
        return f"{TOPIC[self.language]} {self.marker}."

    def readers(self) -> list[Subject]:
        """The Access Grants it is published with, before any revocation."""
        if self.audience is Audience.PUBLIC:
            return [Subject.public()]
        if self.audience is Audience.USER:
            assert self.grantee is not None
            return [Subject.user(user_id_for(ISSUER, f"sub-{self.grantee}"))]
        if self.audience is Audience.GROUP:
            assert self.grantee is not None
            return [Subject.group(group_id_for(ISSUER, self.grantee))]
        return []


@dataclass(frozen=True, slots=True)
class Scenario:
    seed: int
    people: tuple[Person, ...]
    secrets: tuple[Secret, ...]

    def person(self, name: str) -> Person:
        return next(person for person in self.people if person.name == name)

    def readable(self, person: Person) -> set[str]:
        """The markers of every Document `person` may read."""
        return {s.marker for s in self.secrets if may_read(person, s)}

    def forbidden(self, person: Person) -> set[str]:
        """The markers of every Document `person` may not read."""
        return {s.marker for s in self.secrets} - self.readable(person)


def may_read(person: Person, secret: Secret) -> bool:
    """The eligibility rule, written out without the SQL.

    A Document is readable when it is kept, not revoked or deleted, and is
    granted to everyone, to this person, or to a group whose path the token
    names exactly. A parent or child group never counts.
    """
    if secret.fate is not Fate.KEPT:
        return False
    if secret.audience is Audience.PUBLIC:
        return True
    if secret.audience is Audience.USER:
        return secret.grantee == person.name
    if secret.audience is Audience.GROUP:
        return secret.grantee in person.groups
    return False


def scenario(seed: int) -> Scenario:
    """A reproducible company of people and Documents for `seed`."""
    rng = random.Random(seed)  # noqa: S311 - a seeded, reproducible scenario
    people = (
        Person("alice", (ENGINEERING,)),
        Person("bob", (BACKEND,)),
        Person("carol", tuple(rng.sample(GROUP_PATHS, rng.randint(1, 3)))),
        Person("dan", ()),
    )
    secrets: list[Secret] = []
    # The fixed core: each risk is present in every scenario.
    fixed = [
        (Audience.GROUP, ENGINEERING),
        (Audience.GROUP, BACKEND),
        (Audience.GROUP, "/Engineering"),
        (Audience.USER, "alice"),
        (Audience.USER, "bob"),
        (Audience.NOBODY, None),
    ]
    for audience, grantee in fixed:
        secrets.append(_secret(rng, len(secrets), audience, grantee, Fate.KEPT))
    for fate in (Fate.REVOKED, Fate.DELETED):
        secrets.append(_secret(rng, len(secrets), Audience.GROUP, ENGINEERING, fate))
    # Twins: one body text, granted to everyone and to bob only.
    language = rng.choice(("en", "uk"))
    for audience, grantee in ((Audience.PUBLIC, None), (Audience.USER, "bob")):
        secrets.append(
            Secret(
                marker=f"twin{len(secrets)}s{seed}",
                language=language,
                audience=audience,
                grantee=grantee,
                fate=Fate.KEPT,
                twin=True,
            )
        )
    # Random extras, so seeds differ beyond the core.
    for _ in range(rng.randint(2, 5)):
        audience = rng.choice(list(Audience))
        grantee = _grantee(rng, audience, people)
        fate = rng.choices(list(Fate), weights=(6, 1, 1))[0]
        secrets.append(_secret(rng, len(secrets), audience, grantee, fate))
    return Scenario(seed=seed, people=people, secrets=tuple(secrets))


def _secret(
    rng: random.Random,
    index: int,
    audience: Audience,
    grantee: str | None,
    fate: Fate,
) -> Secret:
    return Secret(
        marker=f"mark{index}s{rng.randrange(10**6):06d}",
        language=rng.choice(("en", "uk")),
        audience=audience,
        grantee=grantee,
        fate=fate,
        twin=False,
    )


def _grantee(
    rng: random.Random, audience: Audience, people: Sequence[Person]
) -> str | None:
    if audience is Audience.USER:
        return rng.choice(people).name
    if audience is Audience.GROUP:
        return rng.choice(GROUP_PATHS)
    return None


class ScenarioAuthenticator:
    """Gives each person of a scenario the caller their token would give."""

    def __init__(self, people: Sequence[Person]) -> None:
        self._callers = {
            f"Bearer {person.name}": Caller(
                reader=person.context(), roles=frozenset({ApplicationRole.USER})
            )
            for person in people
        }

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in self._callers:
            return self._callers[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


def leaked(markers: set[str], *observed: Any) -> set[str]:
    """The markers that appear anywhere in what was observed."""
    text = "\n".join(
        item.decode() if isinstance(item, bytes) else str(item) for item in observed
    )
    return {marker for marker in markers if marker in text}
```

The fixed core is what makes every seed adversarial: whatever the random extras are, each scenario has the parent and child groups, the look-alike, a revoked and a deleted Document, and the twins.

**Check:** `uv run pyright tests/authorization_scenarios.py` reports 0 errors.

### Step 3 — Coding step: test the generator itself

**In plain words:** a test suite is only as strong as its scenarios. If some seed had no nested group, the suite would pass on it while proving nothing about nested groups. These quick tests, with no database, check that every seed holds every risk, that the oracle and the real token code agree about nested and look-alike groups, and that an unknown token is refused.

| Test | Shows |
|---|---|
| `test_every_scenario_holds_each_risk` | Fifty seeds, each with every risk, reproducible, and with something readable and something forbidden for every person |
| `test_a_child_groups_member_does_not_read_the_parents_documents` | bob in `/engineering/backend` gets neither the parent's Documents nor its Group ID |
| `test_look_alike_paths_are_other_groups` | `/Engineering`, `/engineering-ops`, and the child are other groups |
| `test_an_ambiguous_group_path_gives_its_member_nothing` | A path with `~` is dropped, as production drops it |
| `test_an_unknown_token_is_refused` | The typed failure: `Unauthenticated` |

Create `tests/test_authorization_scenarios.py`:

```python
"""The scenario generator holds every risk it promises, for every seed.

The suite in `test_authorization_suite_integration.py` is only as strong as
its scenarios: a seed without a nested group or a twin would pass while
proving nothing about them. These tests need no database.
"""

import pytest

from authorization_scenarios import (
    BACKEND,
    ENGINEERING,
    ISSUER,
    Audience,
    Fate,
    Person,
    ScenarioAuthenticator,
    may_read,
    scenario,
)
from knowledge_service.identity.context import Unauthenticated, group_id_for

SEEDS = range(50)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_scenario_holds_each_risk(seed: int) -> None:
    case = scenario(seed)
    groups = {s.grantee for s in case.secrets if s.audience is Audience.GROUP}
    twins = [s for s in case.secrets if s.twin]

    assert {ENGINEERING, BACKEND, "/Engineering"} <= groups
    assert {Fate.REVOKED, Fate.DELETED} <= {s.fate for s in case.secrets}
    assert {s.language for s in case.secrets} == {"en", "uk"}
    # One body text, granted to everyone and to one person.
    assert len({s.text for s in twins}) == 1
    assert {s.audience for s in twins} == {Audience.PUBLIC, Audience.USER}
    for person in case.people:
        assert case.readable(person), person.name
        assert case.forbidden(person), person.name
    assert scenario(seed) == case


def test_a_child_groups_member_does_not_read_the_parents_documents() -> None:
    case = scenario(0)
    bob = case.person("bob")
    parent = next(s for s in case.secrets if s.grantee == ENGINEERING)

    assert not may_read(bob, parent)
    assert group_id_for(ISSUER, ENGINEERING) not in bob.context().group_ids


def test_look_alike_paths_are_other_groups() -> None:
    alice = scenario(0).person("alice")

    assert alice.context().group_ids == frozenset({group_id_for(ISSUER, ENGINEERING)})
    for other in ("/Engineering", "/engineering-ops", BACKEND):
        assert group_id_for(ISSUER, other) not in alice.context().group_ids


def test_an_ambiguous_group_path_gives_its_member_nothing() -> None:
    # Keycloak writes `/` inside a name as `~/`, so `~` makes a path ambiguous.
    eve = Person("eve", ("/engineering~/backend",))

    assert eve.context().group_ids == frozenset()


async def test_an_unknown_token_is_refused() -> None:
    authenticator = ScenarioAuthenticator(scenario(0).people)

    with pytest.raises(Unauthenticated):
        await authenticator.authenticate(["Bearer mallory"])
```

**Check:** `uv run pytest tests/test_authorization_scenarios.py -q` gives `54 passed`.

### Step 4 — Coding step: the suite across every interface

**In plain words:** this file stores each scenario in real PostgreSQL and then lets every person try every door, in both languages. For each door it checks two things: the person got exactly what they may read, and nothing of anything else, not a marker and not an ID, shows up anywhere. At the end of each test it searches every log record and every audit row for every marker.

| Part | What it does |
|---|---|
| `publish` | Stores each Document on the query vector, captures its real IDs through the snapshot reader, then gives it its real readers and revokes or deletes it |
| `exposed`, `logged`, `audit_rows`, `nothing_recorded` | Gather everything a door returned, every log field, and every audit row, and check them for markers |
| `test_every_retrieval_returns_exactly_what_each_person_may_read` | Vector, lexical, hybrid, and the Evidence source, at a full limit and cut to one |
| `test_the_answer_stream_shows_each_person_only_what_they_may_read` | Over HTTP; the model's prompt holds exactly the readable markers, and the stream repeats them |
| `test_a_documents_real_ids_never_let_another_person_read_it` | Direct IDs of every Document for every person; refused ones are `WITHDRAWN`, and every fate is refused at least once |
| `test_a_conversation_is_only_its_owners_and_forgets_what_they_lose` | Another person's Conversation and History read as missing; the owner's History withholds an Answer whose Document was revoked |
| `test_a_document_taken_away_while_the_model_writes_is_withdrawn` | The race, for a revocation and a deletion: no Citation, no ID, and a `denied` audit row |

Create `tests/test_authorization_suite_integration.py`:

```python
"""The authorization adversarial suite: one set of scenarios, every interface.

Each seed of `authorization_scenarios.scenario` is published into real
PostgreSQL, then every person probes every interface a reader can reach,
in English and in Ukrainian:

- **retrieval:** vector search, lexical search, and hybrid retrieval, also
  cut to one result, so `truncated` must count only eligible Chunks;
- **answer:** the Evidence source and the answer stream over HTTP, with a
  model that cites everything it is shown and repeats every marker in its
  prompt, so anything it was shown would reach the client;
- **citation:** the Citation and Evidence checks, given the real
  identifiers of every Document, captured while it could still be read:
  Documents of other people, revoked ones, deleted ones (direct IDs);
- **Conversation:** another person's Conversation and History by ID, and
  the owner's own History once a Document it cited is revoked;
- **revocation race:** a grant taken away, or a Document deleted, while the
  model writes.

Each probe must return exactly the Documents `may_read` allows, where it
returns Documents at all. No marker of a Document the reader may not see,
and none of its identifiers, may appear in what a probe returns, in the
model's prompt, or in the bytes sent; no marker may appear in any field of
any log record or in any audit row. Agent tools do not exist yet; when they
do (M11), they join this suite as another probe.
"""

import dataclasses
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import (
    ENGINEERING,
    LATER,
    PROBES,
    Fate,
    Person,
    Scenario,
    ScenarioAuthenticator,
    Secret,
    leaked,
    scenario,
)
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.audit_trail import DatabaseAuditTrail
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.current_citations import (
    CurrentCitations,
    confirm_citations,
    evidence_still_visible,
)
from knowledge_service.answering.evidence import Evidence, EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelReply,
)
from knowledge_service.answering.history import load_history
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction, AuditDecision
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ConversationId, DocumentId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.audit import load_request_audit_events
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.conversations import (
    add_turn,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.retrieval.hybrid import (
    RetrievalBranch,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_streaming import USAGE, Model, streamer
from test_vector_evidence_integration import FixedModel
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_space,
)

SEEDS = range(6)
LIMIT = 20
LIMITS = ConversationLimits()
REQUEST_ID = UUID("00000000-0000-0000-0000-0000000000f1")
EVIDENCE_ID = re.compile(r'"id": "(E[0-9a-f]{8})"')
MARKER = re.compile(r"(?:mark|twin)\d+s\d+")
# A reader no person is: it sees every Document once, to capture its IDs.
SNAPSHOT = UserId(UUID(int=0x5AA9))
GRANTED_AT = OBSERVED_AT + timedelta(minutes=1)


@dataclasses.dataclass(frozen=True, slots=True)
class Published:
    """A scenario as stored: each marker's Document and captured Evidence."""

    scenario: Scenario
    documents: dict[str, DocumentId]
    evidence: dict[str, Evidence]

    def markers(self, documents: set[DocumentId]) -> set[str]:
        by_id = {document: marker for marker, document in self.documents.items()}
        return {by_id[document] for document in documents}

    def secrets_of(self, markers: set[str]) -> set[str]:
        """Markers, and the Document and Chunk IDs behind them, as text."""
        found = set(markers)
        for marker in markers:
            found.add(str(self.documents[marker].value))
            found.add(str(self.evidence[marker].chunk_id.value))
        return found


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


def normalized(secret: Secret) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=secret.external_id,
            version="v1",
            media_type="text/plain",
            content=secret.text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title=secret.title, modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(runtime: DatabaseRuntime, case: Scenario) -> Published:
    """Store every Document and capture its Evidence while it can be read.

    Each Document is first readable by `SNAPSHOT` too, so its real
    identifiers can be captured, as a client that once read it holds them.
    Then it gets only its real readers, and is revoked or deleted as the
    scenario says.
    """
    documents: dict[str, DocumentId] = {}
    for secret in case.secrets:
        reconciled = await reconcile_document(
            runtime,
            normalized(secret),
            readers=[*secret.readers(), Subject.user(SNAPSHOT)],
            observed_at=OBSERVED_AT,
        )
        document_id = reconciled.document.document_id
        async with runtime.transaction() as connection:
            space_id = await load_embedding_space_id(connection, IDENTITY)
            assert space_id is not None
            stored = await load_chunks(connection, document_id=document_id)
            await insert_chunk_embeddings(
                connection,
                space_id=space_id,
                dimensions=IDENTITY.dimensions,
                vectors={chunk.chunk_id: QUERY for chunk in stored},
                created_at=OBSERVED_AT,
            )
        documents[secret.marker] = document_id
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=AuthorizationContext(user_id=SNAPSHOT),
            limit=LIMIT,
        )
    captured = {
        item.document_id: item for item in build_evidence(found.candidates).items
    }
    evidence = {marker: captured[document] for marker, document in documents.items()}
    for secret in case.secrets:
        revoked = secret.fate is Fate.REVOKED
        await reconcile_document(
            runtime,
            normalized(secret),
            readers=[] if revoked else secret.readers(),
            observed_at=OBSERVED_AT + LATER if revoked else GRANTED_AT,
        )
    # A pass that no longer sees the deleted ones tombstones them; their
    # Chunks stay stored until a purge Job runs, which never runs here.
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={s.external_id for s in case.secrets if s.fate is not Fate.DELETED},
        observed_at=OBSERVED_AT + LATER,
    )
    return Published(case, documents, evidence)


def exposed(*items: Any) -> list[object]:
    """Every field of every returned item, so nothing can hide a marker."""
    seen: list[object] = []
    for item in items:
        if dataclasses.is_dataclass(item):
            for field in dataclasses.fields(item):
                seen.extend(exposed(getattr(item, field.name)))
        elif isinstance(item, (tuple, list)):
            seen.extend(exposed(*cast(tuple[Any, ...], item)))
        else:
            seen.append(item)
    return seen


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every field of every log record, the `extra` ones included."""
    return json.dumps([record.__dict__ for record in caplog.records], default=str)


async def audit_rows(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(sa.text("SELECT to_jsonb(a) FROM audit_events a"))
        ).scalars()
        return json.dumps(list(rows))


async def nothing_recorded(
    runtime: DatabaseRuntime, published: Published, caplog: pytest.LogCaptureFixture
) -> None:
    """No marker in any log record or audit row, whoever may read it."""
    markers = {secret.marker for secret in published.scenario.secrets}
    assert leaked(markers, logged(caplog)) == set()
    assert leaked(markers, await audit_rows(runtime)) == set()


# --- Retrieval -------------------------------------------------------------------

Found = tuple[set[DocumentId], bool | None, list[object]]
Probe = Callable[[DatabaseRuntime, AuthorizationContext, str, int], Awaitable[Found]]


async def vector(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection, identity=IDENTITY, query=QUERY, context=reader, limit=limit
        )
    return {c.document_id for c in found.candidates}, found.truncated, exposed(found)


async def lexical(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    async with runtime.transaction() as connection:
        found = await search_lexical_chunks(
            connection, text=question, context=reader, limit=limit
        )
    return {c.document_id for c in found.candidates}, found.truncated, exposed(found)


async def hybrid(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    found = await retrieve_hybrid(
        question,
        context=reader,
        branches={
            RetrievalBranch.DENSE: dense_branch(runtime, FixedModel()),
            RetrievalBranch.LEXICAL: lexical_branch(runtime),
        },
        limit=limit,
        timeout_seconds=10,
        request_id=REQUEST_ID,
    )
    return {c.document_id for c in found.candidates}, None, exposed(found)


async def evidence(
    runtime: DatabaseRuntime, reader: AuthorizationContext, question: str, limit: int
) -> Found:
    source = VectorEvidenceSource(
        runtime, FixedModel(), limit=limit, embed_timeout_seconds=5
    )
    found = await source.find(question, reader=reader, request_id=REQUEST_ID)
    items = found.evidence.items
    return {item.document_id for item in items}, None, exposed(found)


PROBES_BY_NAME: dict[str, Probe] = {
    "vector": vector,
    "lexical": lexical,
    "hybrid": hybrid,
    "evidence": evidence,
}
# Lexical search reads the words of the question's language only; the fake
# embedding model gives every question the same vector.
SAME_LANGUAGE_ONLY = {"lexical"}


def wanted(published: Published, person: Person, name: str, language: str) -> set[str]:
    readable = published.scenario.readable(person)
    if name not in SAME_LANGUAGE_ONLY:
        return readable
    return {
        s.marker
        for s in published.scenario.secrets
        if s.marker in readable and s.language == language
    }


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_every_retrieval_returns_exactly_what_each_person_may_read(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))

    for person in published.scenario.people:
        reader = person.context()
        hidden = published.secrets_of(published.scenario.forbidden(person))
        for name, probe in PROBES_BY_NAME.items():
            for language, question in PROBES.items():
                where = f"{name}/{language}/{person.name}/seed {seed}"
                expected = wanted(published, person, name, language)
                documents, _, seen = await probe(runtime, reader, question, LIMIT)
                assert published.markers(documents) == expected, where
                assert leaked(hidden, *seen) == set(), where
                # Cut to one: the one is readable, and `truncated` counts
                # only eligible Chunks, so it says nothing about the rest.
                top, truncated, seen = await probe(runtime, reader, question, 1)
                assert published.markers(top) <= expected, where
                assert len(top) == min(1, len(expected)), where
                if truncated is not None:
                    assert truncated is (len(expected) > 1), where
                assert leaked(hidden, *seen) == set(), where
    await nothing_recorded(runtime, published, caplog)


# --- The answer stream over HTTP -----------------------------------------------


class CitingEverything(Model):
    """Cites every Evidence it is shown; keeps each prompt it was sent.

    With `echo`, its answer repeats every marker in its prompt, so the text
    it streams would show anything it was shown.
    """

    def __init__(self, *, echo: bool = True) -> None:
        super().__init__()
        self.prompts: list[str] = []
        self.echo = echo

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.calls += 1
        self.prompts.append(prompt.input)
        markers = sorted(set(MARKER.findall(prompt.input)))
        answer = "The annual leave policy"
        if self.echo and markers:
            answer += " from " + " ".join(markers)
        final = json.dumps(
            {
                "answer": answer + ".",
                "citations": EVIDENCE_ID.findall(prompt.input),
                "abstained": False,
                "reason": None,
            }
        )
        yield final
        yield ModelReply(text=final, refused=False, usage=USAGE)


async def ask(
    runtime: DatabaseRuntime,
    people: list[Person],
    person: Person,
    question: str,
    model: CitingEverything,
) -> bytes:
    """Ask over HTTP as `person`; the stream must end with a completion."""
    source: Any = VectorEvidenceSource(
        runtime, FixedModel(), limit=LIMIT, embed_timeout_seconds=5
    )
    app = create_app(
        streamer=streamer(
            model,
            source=source,
            citations=CurrentCitations(runtime),
            audit=DatabaseAuditTrail(runtime),
        ),
        authenticator=ScenarioAuthenticator(people),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": question},
            headers={**person.authorization, "X-Request-ID": str(REQUEST_ID)},
        )
    assert response.status_code == 200
    assert b"event: completion" in response.content
    return response.content


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_the_answer_stream_shows_each_person_only_what_they_may_read(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    people = list(published.scenario.people)

    for person in people:
        expected = published.scenario.readable(person)
        forbidden = published.scenario.forbidden(person)
        hidden = published.secrets_of(forbidden)
        markers = expected | forbidden
        for language, question in PROBES.items():
            where = f"answer/{language}/{person.name}/seed {seed}"
            caplog.clear()
            model = CitingEverything()
            sent = await ask(runtime, people, person, question, model)
            # What reached the model is all the person may read, and only it;
            # the model repeated it, so the stream carried it too.
            assert leaked(markers, *model.prompts) == expected, where
            assert leaked(expected, sent) == expected, where
            assert leaked(hidden, sent, *model.prompts) == set(), where
            # caplog is cleared each pass, so check every marker each pass.
            assert leaked(markers, logged(caplog)) == set(), where
            assert leaked(hidden, logged(caplog)) == set(), where
    await nothing_recorded(runtime, published, caplog)


# --- Direct IDs: the real identifiers of Documents the reader may not see ----


def answer_citing(item: Evidence) -> AnswerResult:
    citation = Citation(
        evidence_id=item.evidence_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        source_id=item.source_id,
        content_version=item.content_version,
        title=item.title,
        heading_path=item.heading_path,
        source_link=item.source_link,
        excerpt=item.excerpt,
    )
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text="The annual leave policy.",
        reason=None,
        citations=(citation,),
        defect=None,
        usage=USAGE,
        context=cast(Any, None),
    )


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_a_documents_real_ids_never_let_another_person_read_it(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    denied: set[Fate] = set()

    for secret in published.scenario.secrets:
        item = published.evidence[secret.marker]
        for person in published.scenario.people:
            reader = person.context()
            allowed = secret.marker in published.scenario.readable(person)
            async with runtime.transaction() as connection:
                visible = await evidence_still_visible(
                    connection, reader, EvidenceSet((item,)), request_id=REQUEST_ID
                )
                confirmed = await confirm_citations(
                    connection, reader, answer_citing(item), request_id=REQUEST_ID
                )
            where = (secret.marker, secret.fate, person.name)
            assert visible is allowed, where
            if allowed:
                assert confirmed.outcome is AnswerOutcome.ANSWERED, where
            else:
                assert confirmed.outcome is AnswerOutcome.WITHDRAWN, where
                assert confirmed.citations == (), where
                denied.add(secret.fate)
    # Other people's, revoked, and deleted Documents were all refused.
    assert denied == set(Fate)
    await nothing_recorded(runtime, published, caplog)


# --- Conversations ---------------------------------------------------------------


async def cited(
    runtime: DatabaseRuntime, published: Published, person: Person
) -> tuple[tuple[MessageReference, ...], str]:
    """References to every readable Chunk, and a reply naming their markers."""
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=person.context(),
            limit=LIMIT,
        )
    references = tuple(
        MessageReference(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            content_version=c.content_version,
        )
        for c in found.candidates
    )
    markers = published.markers({c.document_id for c in found.candidates})
    return references, "The annual leave policy from " + " ".join(sorted(markers))


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_a_conversation_is_only_its_owners_and_forgets_what_they_lose(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    case = published.scenario
    owned: dict[str, ConversationId] = {}
    for person in case.people:
        reader = person.context()
        references, reply = await cited(runtime, published, person)
        async with runtime.transaction() as connection:
            conversation = await start_conversation(
                connection, owner_id=reader.user_id, now=OBSERVED_AT, limits=LIMITS
            )
            await add_turn(
                connection,
                owner_id=reader.user_id,
                conversation_id=conversation.conversation_id,
                question=PROBES["en"],
                reply=reply,
                outcome=MessageOutcome.ANSWERED,
                references=references,
                now=OBSERVED_AT,
                limits=LIMITS,
            )
        owned[person.name] = conversation.conversation_id

    now = OBSERVED_AT + 2 * LATER
    for person in case.people:
        reader = person.context()
        for owner, conversation_id in owned.items():
            async with runtime.transaction() as connection:
                read = await read_conversation(
                    connection,
                    owner_id=reader.user_id,
                    conversation_id=conversation_id,
                    now=now,
                )
                history = await load_history(
                    connection,
                    reader,
                    conversation_id=conversation_id,
                    now=now,
                    budget_tokens=4_000,
                )
            mine = owner == person.name
            assert (read is not None) is mine, (person.name, owner)
            assert (history is not None) is mine, (person.name, owner)

    # alice loses her group's Document; her own History withholds the
    # Answer that cited it, and shows none of its markers.
    alice = case.person("alice")
    target = next(
        s for s in case.secrets if s.grantee == ENGINEERING and s.fate is Fate.KEPT
    )
    await reconcile_document(runtime, normalized(target), readers=[], observed_at=now)
    async with runtime.transaction() as connection:
        history = await load_history(
            connection,
            alice.context(),
            conversation_id=owned["alice"],
            now=now,
            budget_tokens=4_000,
        )
    assert history is not None
    (turn,) = history.turns
    assert (turn.answer, history.withheld) == (None, 1)
    shown = [turn.question, turn.answer, *history.earlier_questions]
    assert leaked({target.marker}, *shown) == set()
    await nothing_recorded(runtime, published, caplog)


# --- A revocation race -----------------------------------------------------------


class ChangingWhileWriting(CitingEverything):
    """Runs `change` after its prompt is built, before it replies."""

    def __init__(self, change: Callable[[], Awaitable[None]]) -> None:
        # No echo: text streamed before a withdrawal cannot be unsent.
        super().__init__(echo=False)
        self.change = change

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        await self.change()
        async for part in super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        ):
            yield part


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fate", [Fate.REVOKED, Fate.DELETED])
async def test_a_document_taken_away_while_the_model_writes_is_withdrawn(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, seed: int, fate: Fate
) -> None:
    caplog.set_level(logging.DEBUG)
    published = await publish(runtime, scenario(seed))
    case = published.scenario
    alice = case.person("alice")
    # alice's group Document: readable when she asks, gone as it is written.
    target = next(
        s for s in case.secrets if s.grantee == ENGINEERING and s.fate is Fate.KEPT
    )
    later = OBSERVED_AT + 2 * LATER

    async def take_away() -> None:
        if fate is Fate.REVOKED:
            await reconcile_document(
                runtime, normalized(target), readers=[], observed_at=later
            )
        else:
            await tombstone_missing(
                runtime,
                source_id=SOURCE_ID,
                seen={s.external_id for s in case.secrets if s is not target},
                observed_at=later,
            )

    model = ChangingWhileWriting(take_away)
    sent = await ask(runtime, list(case.people), alice, PROBES["en"], model)

    assert target.marker in model.prompts[0]
    # Withdrawn: no Citation, so not even the title or IDs leave.
    assert b"event: citation" not in sent
    assert leaked(published.secrets_of({target.marker}), sent) == set()
    async with runtime.transaction() as connection:
        trail = await load_request_audit_events(
            connection, request_id=REQUEST_ID, limit=50
        )
    reads = {
        event.target_id: event.decision
        for event in trail
        if event.action is AuditAction.DOCUMENT_READ
    }
    assert reads[published.documents[target.marker].value] is AuditDecision.DENIED
    await nothing_recorded(runtime, published, caplog)
```

Each probe returns three things: the Documents it gave back, its `truncated` flag if it has one, and every field it exposed. Adding a new door later, such as an agent tool, means writing one more probe of the same shape.

**Check:** `uv run pytest -m integration tests/test_authorization_suite_integration.py -q` gives `36 passed`.

### Step 5 — No coding in this step: break it on purpose

This step makes a group path also grant its parent group, the classic nested-group mistake, and shows how loudly the suite fails.

**Action 1:** in `build_authorization_context` in `src/knowledge_service/identity/context.py`, find:

```python
            group_id_for(issuer, path) for path in _group_paths(claims, policy)
```

and replace it with:

```python
            group_id_for(issuer, prefix)
            for path in _group_paths(claims, policy)
            for prefix in {path, path.rsplit("/", 1)[0] or path}
```

**Action 2:** run `uv run pytest -m integration tests/test_authorization_suite_integration.py -q`. Expected: `18 failed, 18 passed`. bob, in `/engineering/backend`, now reads `/engineering` Documents through every door: retrieval, the answer stream, and direct IDs.

**Action 3:** put the original line back and run the same command: `36 passed`. Compare with your last prediction in the evidence file.

The reference run tried fifteen such mutants. The suite failed on every one: any group matching, a user grant matching anyone, a tombstoned Document still readable, an unchecked Conversation owner, Citations or Evidence not rechecked, a deleted Document citable by ID, the owner's History not withholding, titles in log messages or log fields, History logging its text, group paths ignoring case, parent-group inheritance, and `truncated` counting the limit.

### Step 6 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model named M07-T15 as the owner of five abuse cases and one invariant; it now names the suite's tests and what is still to come.

**Action 1:** update one document.

In `docs/threat-model.md`:

**1.** Find:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same; black-box tests show that another User's best-matching Document changes nothing in the response bytes, headers, log records, or the model's prompt, and that a forbidden Chunk, Evidence, or Conversation ID gives the same result and log as a missing one | Authorization adversarial suite (M07-T15) |
| The time a search takes tells a reader how many hidden Chunks match their question | Vector search settles the reader's Chunks in a `MATERIALIZED` step before ranking any, so its work does not grow with hidden near Chunks: with 5,000 hidden exact matches the gap fell from about 10 ms to within noise, at the cost of about twice the time for a reader who can see 20,000 Documents | Lexical search still filters after its index, about 5 to 28 ms slower per 5,000 hidden matches; it reaches no reader yet, and must be fixed or bounded before lexical or hybrid retrieval joins a served path (M09); a hybrid branch deadline would turn that gap into a timed-out branch |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt. Whatever the model writes, `checked_answer` refuses an Answer or reason holding a Markdown image, an HTML tag that loads or runs something, or a mermaid block, and any link that no cited Evidence contains, after undoing escapes and character references; an abstention holds no link. The stream holds text back from the first character that could start one until the check, and a reply holding a tool call is not an answer. Tests seed injection Documents and a model that obeys them | Authorization adversarial suite (M07-T15) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | The answer stream passes every Answer through its `CitationCheck`, `CurrentCitations` over PostgreSQL, before the decision: `confirm_citations` rereads each cited Chunk under the same permission check and its current version, and one that fails withdraws the Answer, which ends with the same decision and message as an empty search; Citations carry the provenance read then. Text streamed before a revocation was written from Evidence the reader could see when the model was asked; the abstention replaces it but cannot unsend it | None |
| A valid ID of another User's Chunk reaches the stream, through a retrieval bug or a crafted Evidence set | Before the model is asked, `still_visible` rereads every Evidence as the request's reader; one the reader may not see makes the stream abstain like an empty search, so the model never sees it and none of its text, title, or link is sent; the stream refuses a check that answers with other text, or cites another Chunk, Document, Source, or version | Read-only agent tools with the reader (M11-T01) |
```

Replace with:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT; `tests/test_authorization_suite_integration.py` runs seeded people and Documents (nested and look-alike groups, identical public and private text, revoked and deleted Documents, English and Ukrainian) through vector, lexical, and hybrid retrieval, the Evidence source, and the answer stream, and each must return exactly what `may_read` allows | Agent tools join the suite as a probe (M11-T01) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | None |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same; black-box tests show that another User's best-matching Document changes nothing in the response bytes, headers, log records, or the model's prompt, and that a forbidden Chunk, Evidence, or Conversation ID gives the same result and log as a missing one; `tests/test_authorization_suite_integration.py` gives the Citation and Evidence checks the real IDs of every Document, captured while it could be read, and every person but its readers is refused | None |
| The time a search takes tells a reader how many hidden Chunks match their question | Vector search settles the reader's Chunks in a `MATERIALIZED` step before ranking any, so its work does not grow with hidden near Chunks: with 5,000 hidden exact matches the gap fell from about 10 ms to within noise, at the cost of about twice the time for a reader who can see 20,000 Documents | Lexical search still filters after its index, about 5 to 28 ms slower per 5,000 hidden matches; it reaches no reader yet, and must be fixed or bounded before lexical or hybrid retrieval joins a served path (M09); a hybrid branch deadline would turn that gap into a timed-out branch |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | A cache, when one is added, joins the authorization suite as a probe |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt. Whatever the model writes, `checked_answer` refuses an Answer or reason holding a Markdown image, an HTML tag that loads or runs something, or a mermaid block, and any link that no cited Evidence contains, after undoing escapes and character references; an abstention holds no link. The stream holds text back from the first character that could start one until the check, and a reply holding a tool call is not an answer. Tests seed injection Documents and a model that obeys them | Agent tools re-authorize every call server-side (M11-T01) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | The answer stream passes every Answer through its `CitationCheck`, `CurrentCitations` over PostgreSQL, before the decision: `confirm_citations` rereads each cited Chunk under the same permission check and its current version, and one that fails withdraws the Answer, which ends with the same decision and message as an empty search; Citations carry the provenance read then. Text streamed before a revocation was written from Evidence the reader could see when the model was asked; the abstention replaces it but cannot unsend it | None |
| A valid ID of another User's Chunk reaches the stream, through a retrieval bug or a crafted Evidence set | Before the model is asked, `still_visible` rereads every Evidence as the request's reader; one the reader may not see makes the stream abstain like an empty search, so the model never sees it and none of its text, title, or link is sent; the stream refuses a check that answers with other text, or cites another Chunk, Document, Source, or version | Read-only agent tools with the reader (M11-T01) |
```

**2.** Find:

```markdown

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `tests/test_retrieved_instructions.py::test_an_obeyed_injection_never_reaches_the_client`, `tests/test_retrieved_instructions.py::test_the_model_is_offered_no_tool_and_a_tool_call_is_no_answer` |
| 5 | Log records hold counts, names, and outcomes only; the telemetry handler exports only allowlisted plain fields, and raw text only under development diagnostics, never in production; audit events hold identifiers, fixed labels, and one-word metadata only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_query_audit.py::test_the_record_holds_no_question_or_answer_text`, `tests/test_audit_events_integration.py::test_a_question_and_the_documents_it_cites_are_recorded`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `tests/test_telemetry.py::test_an_answer_exports_its_counts_and_none_of_its_text`, `tests/test_telemetry.py::test_text_in_fields_and_arguments_is_dropped_or_redacted`, `tests/test_telemetry.py::test_production_refuses_development_diagnostics`, `M10-T14` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

## Known gaps

```

Replace with:

```markdown

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants`, `tests/test_authorization_suite_integration.py::test_every_retrieval_returns_exactly_what_each_person_may_read` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed`, `tests/test_authorization_suite_integration.py::test_a_document_taken_away_while_the_model_writes_is_withdrawn` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `tests/test_retrieved_instructions.py::test_an_obeyed_injection_never_reaches_the_client`, `tests/test_retrieved_instructions.py::test_the_model_is_offered_no_tool_and_a_tool_call_is_no_answer` |
| 5 | Log records hold counts, names, and outcomes only; the telemetry handler exports only allowlisted plain fields, and raw text only under development diagnostics, never in production; audit events hold identifiers, fixed labels, and one-word metadata only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_query_audit.py::test_the_record_holds_no_question_or_answer_text`, `tests/test_audit_events_integration.py::test_a_question_and_the_documents_it_cites_are_recorded`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `tests/test_telemetry.py::test_an_answer_exports_its_counts_and_none_of_its_text`, `tests/test_telemetry.py::test_text_in_fields_and_arguments_is_dropped_or_redacted`, `tests/test_telemetry.py::test_production_refuses_development_diagnostics`, `M10-T14` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `tests/test_authorization_suite_integration.py::test_the_answer_stream_shows_each_person_only_what_they_may_read`, `tests/test_authorization_suite_integration.py::test_a_documents_real_ids_never_let_another_person_read_it`, `tests/test_authorization_suite_integration.py::test_a_conversation_is_only_its_owners_and_forgets_what_they_lose` |

## Known gaps

```

**Action 2:** run the gates in order, with PostgreSQL and Keycloak running (`make keycloak-up`):

```bash
uv run pytest -m integration tests/test_authorization_suite_integration.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2950 passed for `make test-unit`, 54 more than M07-T14, and 690 passed for `make test-integration`, 36 more.

**Action 3:** complete the evidence file:

- **Design:** the generator and its fixed core, people from token claims, the oracle, markers and IDs, the snapshot reader, the repeating model, and which doors the suite covers.
- **Verification:** the commands and counts, the deliberate break, the fifteen mutants, and the known limits from "Before you begin".
- **Review:** the review findings from "Before you begin".
- **Decision:** why one generator for every door, why people come from token claims, why positive checks, and why mutants prove the suite; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T15 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T16, and commit with `m07-t15: build the authorization adversarial suite`.

## Reflection questions

1. The suite never looks at SQL; it only compares what each door returns with `may_read`. What does that let you change freely inside the search code, and what would make the suite pass even though the code is wrong?
2. Someone speeds up search by caching results per Question, shared between users. Which test in the suite would fail first, and why would it fail even though every single search is still correct on its own?
3. The twins share their text. Why is it fine for dan to read the private twin's words, and what exactly must he still never see?
4. Before the review, all seven new mutants passed the suite. What do they have in common, and what does that teach you about writing "zero leakage" tests?
5. Agent tools arrive in M11. What would one probe for a "search the knowledge base" tool look like in this suite, and which of the existing checks would it reuse?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `tests/authorization_scenarios.py` generates people from token claims and every risk in every seed.
- [ ] `tests/test_authorization_scenarios.py` passes (54).
- [ ] `tests/test_authorization_suite_integration.py` passes (36) across retrieval, answer, citation, Conversation, and the race.
- [ ] Every test scans every log field and audit row for markers.
- [ ] You ran the deliberate break, saw 18 tests fail, and undid it.
- [ ] `docs/threat-model.md` names the suite's tests instead of M07-T15.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
