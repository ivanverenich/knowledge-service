# M07-T01 — Write the system threat model

Task: [M07-T01 — Write the system threat model](../curriculum/milestones/M07-identity-and-authorization/M07-T01-write-the-system-threat-model.md)

## What you will do

This lesson opens the identity and authorization milestone by writing down what the service must protect and from whom. You will write a threat model for the whole system, and then make the documentation check prove one thing about it: every security rule has a control and a test that owns it.

- The **threat model** lists the valuable things (assets), who may attack them (actors), where the boundaries of trust are (trust zones), where requests and data come in (entry points), and for ingestion, retrieval, generation, agents, and operations, how someone could abuse the system and what stops them.
- The **trace** is one table at its end: each of the seven invariants in `docs/security.md` gets a row naming its control and its test owners.
- The **check** makes `make docs-check` fail when an invariant has no row, a named test no longer exists, or a named future task has been completed without naming its test. That is the narrow, observable capability the task asks to leave for the next task.

| The task asks for | Where it happens |
|---|---|
| Assets, actors, trust zones, entry points, abuse cases, mitigations for ingestion, retrieval, generation, agents, operations | Step 3: `docs/threat-model.md` |
| Every security invariant traced to a control and a test owner | Step 3: the `## Invariant trace` table; Step 4: the check |
| Tests: success, highest-risk edge, typed failure or denial | Step 5: `tests/test_threat_model.py` |
| Update only what the change made inaccurate | Step 3: one sentence in `docs/security.md` pointing to the model |
| Authorization before data leaves storage; no seeded text in telemetry | Step 2: you check where each holds today and record it in the trace |

This lesson includes coding: Steps 4 and 5 write code. Steps 1, 2, 3, 6, and 7 do not, though Step 3 writes the threat model itself.

Terms used in this lesson:

- **Threat model:** a written answer to "what can go wrong, and what do we do about it", for one system.
- **Asset:** something worth protecting, such as Document text or an API key.
- **Trust zone:** a part of the system where everything shares one level of trust, like the private network around the database. Data crossing from one zone to another must be checked.
- **Entry point:** a place where outside input enters the system: an HTTP route, a Source, a model's reply.
- **Abuse case:** a concrete way someone could misuse the system, written like a user story for an attacker: "a Document hides instructions that steer the model".
- **Control:** the mechanism that stops an abuse case, such as the permission check inside every search query.
- **Test owner:** the test that proves a control works, or the future task that will add it.

## Before you begin

This section explains the choices behind the work, so each step makes sense when you reach it.

- **What already exists.** `docs/security.md` lists seven invariants, the trust-zone diagram, and a table of threats with the evidence each needs. M02 to M06 built most of the controls: the permission check inside every query, tombstones, opaque Evidence IDs, server-side Citations, History that withholds revoked Answers, and log records with no text. What was missing is one place that says which control and which test stand behind each invariant.
- **Why a check, not only a document.** A threat model goes stale the day after it is written. A test gets renamed, a planned task is finished, and nobody updates the table. The check turns the trace into something `make docs-check` and the unit tests enforce.
- **What counts as a test owner.** Either a test pytest collects, written `tests/<file>.py::<test>` or `tests/<file>.py::<Class>::<test>`, or the ID of a task that is not completed yet. When that task is completed, the check fails until its row names the test the task added. This is how the trace keeps up with M07.
- **Only what a reader sees counts.** A row hidden in an HTML comment, a fenced code block, or a second table further down does not trace anything. An invariant must be written `N. text`, numbered from 1, so a new rule in another list shape is reported instead of silently skipped.
- **Where the check lives.** In `src/knowledge_service/documentation.py`, beside the link and task checks, as `validate_threat_model(root)`. `validate_repository` runs it whenever `docs/security.md` exists, so the old validator tests, which use small temporary repositories without it, are unaffected.
- **What the review found.** An independent adversarial review checked every claim in the threat model against the code and attacked the check with hostile Markdown and test files. Every named test and task existed, and the endpoints, the "nobody may read it" default, and the redacted settings were described correctly. It found these problems, each fixed, with a test that fails on the first version where the check was wrong:
  - The model listed `confirm_citations` as a control in place, but nothing in the code calls it yet. Now it is listed as a gap owned by M07-T09.
  - `POST /v1/answer` was described like the grounded path, but it is the M01 skeleton that sends the Question straight to the model. Now it has its own entry point, abuse rows, and a known gap.
  - The model said every parser removes hidden characters, but only the PDF parser does. Now it says the prompt escapes them.
  - Invariant 2 named a benchmark test that does not check the production query. Now it names `test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`.
  - An invariant written `8)`, `8.` with two spaces, or as a bullet passed without a trace row. Now any list item in another shape is reported.
  - A second `## Invariants` heading, and rows in a comment, a code block, or a second table, were counted or ignored silently. Now a duplicate heading is reported, and only the first visible table counts.
  - A "test" that was only text, dead code, a nested function, a method of a class pytest does not collect, or in a file pytest does not collect passed. Now the test file is parsed, and only module-level tests and `Test*` class methods count.
  - A test file that was not UTF-8 or not valid Python crashed the check. Now it is reported.
  - A control of `TBD` or `-` passed. Now it does not.
  - Documented, not fixed: a test marked to be skipped still counts, and CI runs only the unit tests, so integration test owners run locally.
- **Known limits.** The check proves an owner exists, not that it tests the invariant; that is still your judgement and the reviewer's. The threat model describes the code after M06; M07 tasks will change many rows.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write what must be true when the lesson is done. Step 6 checks the predictions.

Create `docs/evidence/M07-T01-system-threat-model.md`:

```markdown
# M07-T01 — System threat model evidence

## Invariant

`docs/threat-model.md` names the system's assets, actors, trust zones,
entry points, and abuse cases with their mitigations for ingestion,
retrieval, generation, agents, and operations. Every numbered invariant in
`docs/security.md` has exactly one row in its trace, with a control and at
least one test owner: a test pytest collects, or a task not yet completed.
`make docs-check` fails when that stops being true.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.documentation import validate_threat_model"`
fails with `ImportError`.

## Predictions

- Which security invariant has no test in the code today, and why: ...
- Which answer endpoint does not use retrieval at all: ...
- What `make docs-check` prints if a test named in the trace is renamed: ...
- How many tests in `tests/test_threat_model.py` fail if the check stops reporting an invariant with no trace row: ...

## Inventory

## Design

## Verification

## Review

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 6 can prove wrong.

### Step 2 — No coding in this step: find the control and test for each invariant

This step is the research behind the threat model: for each invariant, find the code that enforces it and a test that proves it, before you write a word of the model.

**Action 1:** for each of the seven invariants in `docs/security.md`, find the control and a test. These searches are a good start:

```bash
grep -rn "eligible_chunks_predicate" src | head
grep -rn "def test_.*may_not_see" tests
grep -rn "def test_.*revoked\|def test_.*tombstoned" tests
grep -rn "def test_.*injection\|def test_.*cannot_choose" tests
grep -rn "def test_.*no_text\|def test_.*holds_no_text" tests
grep -rn "confirm_citations" src
grep -n "input=" src/knowledge_service/openai_model.py
```

**Action 2:** under `## Inventory` in the evidence file, write one line per invariant: the control, one test, and whether the test runs in CI (`*_integration.py` tests need the database and run under `make test-integration`). Mark an invariant with no code yet, and name the task that will add it.

Check: you can say for each invariant whether authorization happens before data leaves storage, and whether a test plants text that must not reach a log. Expected surprises: nothing calls `confirm_citations` yet, and `POST /v1/answer` sends the Question straight to the model.

### Step 3 — No coding in this step: write the threat model

This step writes the document itself. Draft it from your inventory first, then compare with the reference below and keep whatever is more accurate.

**Action 1:** create `docs/threat-model.md`. It holds, in this order:

- **Assets**, **Actors**, **Trust zones**, and **Entry points**, each as a short table or list;
- **Abuse cases and mitigations**, one table each for ingestion, retrieval, generation, agents, and operations, with "Mitigation now" and "Still to come" (the task that owns the rest);
- **Invariant trace**: columns `Invariant`, `Control`, `Test owners`, one row per invariant, every owner in backticks;
- **Known gaps**.

The reference, to compare with your draft or to use as the file:

Create `docs/threat-model.md`:

```markdown
# Threat model

This model names what the service must protect, who may attack it, where
they can reach it, and what stops them. It covers ingestion, retrieval,
generation, agents, and operations as the code stands after M06, and names
the task that owns each control still to come. The invariants it protects
are the ones in the security model, `docs/security.md`; the trace at the
end is checked by `make docs-check`.

## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, later OIDC client secrets | Environment and settings, redacted in repr |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
|---|---|---|
| User | Authenticated, reads only granted Documents | Read Documents not granted to them; learn that one exists; spend others' budget |
| Content manager, Evaluator, Administrator | Application Roles; never imply Document access | Use a role to read content the role does not grant |
| Document author | Writes into a Source; never trusted | Plant instructions, fake citations, invisible characters, or huge files in a Document |
| Model provider and its replies | Approved, but replies are untrusted data | Return invented Citations, unsupported claims, or malformed JSON; keep what it was sent |
| Unauthenticated client | None | Call the API without an identity; flood it |
| Operator | Reads logs and dashboards | See Question, Evidence, or Answer text in telemetry |
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token (M07-T03).
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |

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

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query | ACLs in vector and lexical search (M07-T07, M07-T08) |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

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

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A tool call runs with more access than the User who started it | No agents or tools exist; the default path answers without them (ADR 0007) | Read-only tools carrying the Authorization Context (M11-T01), MCP server re-authorizing every call (M11-T06) |
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Nothing yet: neither answer endpoint checks a token | Token validation and the Authorization Context (M07-T03, M07-T04) |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants`, `M07-T04` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Action 2:** point to it from the security model.

In `docs/security.md`:

Find:

```markdown
# Security model

## Invariants

1. A Chunk is eligible for retrieval only when its Document is currently available and at least one normalized Access Grant matches the verified Authorization Context.
2. Authorization occurs inside lexical and vector queries. Post-retrieval filtering is defense in depth, never the primary control.
3. Restrictive permission changes make content unavailable before background cleanup completes.
```

Replace with:

```markdown
# Security model

The threat model, `docs/threat-model.md`, names the assets, actors, entry
points, and abuse cases behind these rules, and traces each invariant to a
control and a test owner.

## Invariants

1. A Chunk is eligible for retrieval only when its Document is currently available and at least one normalized Access Grant matches the verified Authorization Context.
2. Authorization occurs inside lexical and vector queries. Post-retrieval filtering is defense in depth, never the primary control.
3. Restrictive permission changes make content unavailable before background cleanup completes.
```

Check: every task ID in the model exists, every abuse row says what stops it today or says "nothing yet", and no row claims a control the code does not have.

### Step 4 — Coding step: make the documentation check trace every invariant

This step adds the check that keeps the trace honest.

**In plain words:** The documentation check now reads the security model's numbered rules and the trace table in the threat model, and compares them. Every rule needs exactly one row with a real control and at least one owner. An owner is either a test that really exists, which it finds by reading the test file as Python, or a future task that is not finished yet. Anything a reader would not see, such as a commented-out row, does not count, and a broken file is reported instead of crashing the check.

| Part | What it does |
|---|---|
| `_visible_lines` | Drops HTML comments and fenced code blocks, so only what a reader sees is checked |
| `_sections` | The lines under a heading, up to the next level-1 or level-2 heading; finds every copy of the heading, so a duplicate is reported |
| `_invariants` | The numbers of `N. text` items; any other list item is an issue, and the numbers must count up from 1 |
| `_first_table` | The data rows of the first table in the trace section |
| `_defined_tests` | The tests pytest would collect from a module, from its syntax tree: module-level functions and methods of `Test*` classes |
| `_Owners` | Checks each owner: a task must exist and not be completed; a test must be in a readable, parsable `tests/.../test_*.py` file |
| `validate_threat_model` | Ties it together and returns `ValidationIssue`s, never raises on bad Markdown or test files |

In `src/knowledge_service/documentation.py`:

**1.** Find:

```python
"""Validation of repository Markdown links and curriculum metadata."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

TASK_FIELDS = {
```

Replace with:

```python
"""Validation of repository Markdown links, curriculum metadata, and the
threat model's trace of every security invariant."""

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

TASK_FIELDS = {
```

**2.** Find:

```python
    "status",
}
TASK_PATTERN = re.compile(r"M\d{2}-T\d{2}-[^/]+\.md$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
TASK_ID_PATTERN = re.compile(r"M\d{2}-T\d{2}")
VALID_STATUSES = {"planned", "in_progress", "completed", "blocked"}


@dataclass(frozen=True)
class ValidationIssue:
    """One documentation validation failure."""

```

Replace with:

```python
    "status",
}
TASK_PATTERN = re.compile(r"M\d{2}-T\d{2}-[^/]+\.md$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
TASK_ID_PATTERN = re.compile(r"M\d{2}-T\d{2}")
VALID_STATUSES = {"planned", "in_progress", "completed", "blocked"}
SECURITY_MODEL = Path("docs/security.md")
THREAT_MODEL = Path("docs/threat-model.md")
INVARIANTS_HEADING = "## Invariants"
TRACE_HEADING = "## Invariant trace"
INVARIANT_PATTERN = re.compile(r"(\d+)\. \S")
LIST_ITEM_PATTERN = re.compile(r"\s*(?:\d+[.)]|[-*+])\s")
HIDDEN_PATTERN = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
OWNER_PATTERN = re.compile(r"`([^`]*)`")
TEST_OWNER_PATTERN = re.compile(
    r"(tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py)"
    r"::((?:Test[A-Za-z0-9_]*::)?test_[A-Za-z0-9_]+)"
)
PLACEHOLDERS = {"-", "?", "tbd", "todo", "n/a", "none"}


@dataclass(frozen=True)
class ValidationIssue:
    """One documentation validation failure."""

```

**3.** Find:

```python
        return None
    if not (source.parent / path).resolve().exists():
        return ValidationIssue(source, f"broken link: {target}")
    return None


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies."""
    issues: list[ValidationIssue] = []
    task_files = sorted(
        path
        for path in (root / "docs/curriculum/milestones").rglob("*.md")
        if TASK_PATTERN.fullmatch(path.name)
    )
```

Replace with:

````python
        return None
    if not (source.parent / path).resolve().exists():
        return ValidationIssue(source, f"broken link: {target}")
    return None


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _visible_lines(text: str) -> list[str]:
    """The lines a reader sees: no HTML comments and no fenced code blocks."""
    lines: list[str] = []
    fence: str | None = None
    for line in HIDDEN_PATTERN.sub("", text).splitlines():
        marker = line.lstrip()[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
        elif fence is not None:
            if marker == fence:
                fence = None
        else:
            lines.append(line)
    return lines


def _sections(lines: list[str], heading: str) -> list[list[str]]:
    """The lines under each `heading`, up to the next heading of level 1 or 2."""
    sections: list[list[str]] = []
    for start, line in enumerate(lines):
        if line.rstrip() != heading:
            continue
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith(("# ", "## "))
            ),
            len(lines),
        )
        sections.append(lines[start + 1 : end])
    return sections


def _invariants(
    source: Path, lines: list[str]
) -> tuple[list[str], list[ValidationIssue]]:
    numbers: list[str] = []
    issues: list[ValidationIssue] = []
    for line in lines:
        if (match := INVARIANT_PATTERN.match(line)) is not None:
            numbers.append(match[1])
        elif LIST_ITEM_PATTERN.match(line):
            issues.append(
                ValidationIssue(
                    source, f"an invariant must be written as `N. text`: {line.strip()}"
                )
            )
    if numbers and numbers != [str(number) for number in range(1, len(numbers) + 1)]:
        issues.append(
            ValidationIssue(source, "invariants must be numbered 1, 2, 3, ... in order")
        )
    return numbers, issues


def _first_table(lines: list[str]) -> list[str]:
    """The data rows of the first table, without its header and the line under it."""
    table: list[str] = []
    for line in lines:
        if line.startswith("|"):
            table.append(line.strip())
        elif table:
            break
    return table[2:]


def _defined_tests(text: str) -> set[str] | None:
    """The tests pytest would collect from a module: `test_x` or `TestX::test_x`."""
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError:
        return None
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, functions):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.update(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, functions)
            )
    return names


def _task_statuses(root: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for path in (root / "docs/curriculum/milestones").rglob("*.md"):
        if TASK_PATTERN.fullmatch(path.name):
            values, _ = _front_matter(path, _text(path) or "")
            if "id" in values:
                statuses[values["id"]] = values.get("status", "")
    return statuses


class _Owners:
    """Checks test owners, reading and parsing each test file once."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._statuses = _task_statuses(root)
        self._tests: dict[str, set[str] | str] = {}

    def problem(self, owner: str) -> str | None:
        if TASK_ID_PATTERN.fullmatch(owner):
            if owner not in self._statuses:
                return f"unknown task: {owner}"
            if self._statuses[owner] == "completed":
                return f"{owner} is completed; name the test it added"
            return None
        test = TEST_OWNER_PATTERN.fullmatch(owner)
        if test is None:
            return f"an owner must be a test or a task: {owner}"
        defined = self._defined(test[1])
        if isinstance(defined, str):
            return defined
        if test[2] not in defined:
            return f"no such test: {owner}"
        return None

    def _defined(self, file: str) -> set[str] | str:
        if file not in self._tests:
            path = self._root / file
            text = _text(path) if path.is_file() else None
            if text is None:
                self._tests[file] = f"no readable test file: {file}"
            else:
                defined = _defined_tests(text)
                self._tests[file] = (
                    f"cannot parse test file: {file}" if defined is None else defined
                )
        return self._tests[file]


def validate_threat_model(root: Path) -> list[ValidationIssue]:
    """Check that the threat model traces every security invariant.

    Each invariant under "## Invariants" in `docs/security.md`, written
    `N. text` and numbered from 1, must have exactly one row in the first
    table under "## Invariant trace" in `docs/threat-model.md`, naming a
    control and at least one test owner: a test pytest would collect,
    written `tests/<file>.py::<test>` or `tests/<file>.py::<Class>::<test>`,
    or a task that is not yet completed and will add one. Only what a reader
    sees counts: HTML comments and fenced code blocks are skipped.
    """
    security, threat = root / SECURITY_MODEL, root / THREAT_MODEL
    security_text = _text(security)
    if security_text is None:
        return [ValidationIssue(security, "cannot read the security model")]
    found = _sections(_visible_lines(security_text), INVARIANTS_HEADING)
    if len(found) > 1:
        return [ValidationIssue(security, f"{INVARIANTS_HEADING} appears twice")]
    numbers, issues = _invariants(security, found[0] if found else [])
    if not numbers:
        return [*issues, ValidationIssue(security, "no numbered invariants to trace")]
    threat_text = _text(threat) if threat.is_file() else None
    if threat_text is None:
        return [*issues, ValidationIssue(threat, "missing threat model")]
    traces = _sections(_visible_lines(threat_text), TRACE_HEADING)
    if len(traces) != 1:
        problem = "appears twice" if traces else "is missing"
        return [*issues, ValidationIssue(threat, f"{TRACE_HEADING} {problem}")]

    owners = _Owners(root)
    traced: set[str] = set()
    for row in _first_table(traces[0]):
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != 3 or not cells[0].isdecimal() or not cells[0].isascii():
            issues.append(ValidationIssue(threat, f"malformed trace row: {row}"))
            continue
        number, control, named = cells[0], cells[1], OWNER_PATTERN.findall(cells[2])
        if number in traced:
            issues.append(
                ValidationIssue(threat, f"invariant {number} is traced twice")
            )
        traced.add(number)
        if number not in numbers:
            issues.append(ValidationIssue(threat, f"no invariant {number} to trace"))
        if control.lower() in PLACEHOLDERS or not any(c.isalpha() for c in control):
            issues.append(ValidationIssue(threat, f"invariant {number} has no control"))
        if not named:
            issues.append(
                ValidationIssue(threat, f"invariant {number} has no test owner")
            )
        for owner in named:
            problem = owners.problem(owner)
            if problem is not None:
                issues.append(ValidationIssue(threat, f"invariant {number}: {problem}"))
    issues.extend(
        ValidationIssue(threat, f"invariant {number} is not traced")
        for number in numbers
        if number not in traced
    )
    return issues


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies,
    and, when there is a security model, every gap in its trace."""
    issues: list[ValidationIssue] = []
    task_files = sorted(
        path
        for path in (root / "docs/curriculum/milestones").rglob("*.md")
        if TASK_PATTERN.fullmatch(path.name)
    )
````

**4.** Find:

```python
        values, _ = _front_matter(source, source.read_text())
        for dependency in TASK_ID_PATTERN.findall(values.get("depends_on", "")):
            if dependency not in task_ids:
                issues.append(
                    ValidationIssue(source, f"broken dependency: {dependency}")
                )
    return issues
```

Replace with:

```python
        values, _ = _front_matter(source, source.read_text())
        for dependency in TASK_ID_PATTERN.findall(values.get("depends_on", "")):
            if dependency not in task_ids:
                issues.append(
                    ValidationIssue(source, f"broken dependency: {dependency}")
                )
    if (root / SECURITY_MODEL).is_file():
        issues.extend(validate_threat_model(root))
    return issues
```

Why the syntax tree: matching `def test_x(` as text also matches a test in a string, a dead `if False:` branch, or a nested helper, none of which pytest runs. `ast.parse` sees what Python sees.

Check: `uv run python scripts/validate_docs.py` prints nothing and exits 0.

### Step 5 — Coding step: tests for the check

This step proves the check on small temporary repositories and on the real one.

**In plain words:** One test runs the check on this repository and expects no issues. The others build a tiny repository with two invariants, a trace, a test file, and a task, change one thing, and expect exactly the issue that change should cause: a missing row, a renamed test, a finished task, a hidden row, a rule in the wrong list shape, an unreadable test file.

| Test | What it proves |
|---|---|
| `test_the_repository_threat_model_traces_every_security_invariant` | Success: the real trace is complete; this also runs in CI's unit job |
| `test_an_invariant_added_without_a_trace_row_is_reported` | The highest-risk edge: a new rule nobody traced |
| `test_an_owner_that_names_no_test_or_open_task_is_reported`, `test_a_completed_task_cannot_stay_a_test_owner` | The typed denial: each bogus owner gives one named issue |
| `test_a_test_owner_must_be_a_test_pytest_collects`, `test_a_test_file_that_cannot_be_read_is_reported_not_raised` | Review regressions: text that only looks like a test, and broken files |
| `test_a_row_outside_the_visible_trace_table_traces_nothing`, `test_an_invariant_in_another_list_shape_is_reported`, `test_a_second_invariants_or_trace_heading_is_reported` | Review regressions: hidden rows and rules in other shapes |

Create `tests/test_threat_model.py`:

````python
"""The threat model traces every security invariant to a control and a test owner."""

from pathlib import Path

import pytest

from knowledge_service.documentation import (
    ValidationIssue,
    validate_repository,
    validate_threat_model,
)

ROOT = Path(__file__).resolve().parents[1]

SECURITY = """# Security model

## Invariants

1. A Chunk is eligible only when a grant matches.
2. Authorization occurs inside the queries.

## Trust zones
"""

TESTS = """
def test_eligibility() -> None: ...


class TestQueries:
    async def test_in_the_query(self) -> None: ...
"""

TASK = """---
id: {task_id}
title: Example
milestone: M07
track: critical
depends_on: []
estimate: 10m
topics: [security]
deliverables: [tests]
status: {status}
---
"""

ROWS = [
    "| 1 | the predicate | `tests/test_access.py::test_eligibility`, `M07-T07` |",
    "| 2 | in the query | `tests/test_access.py::TestQueries::test_in_the_query` |",
]


def repository(
    root: Path,
    rows: list[str] | None = None,
    *,
    security: str = SECURITY,
    statuses: dict[str, str] | None = None,
) -> Path:
    """A small repository with a security model, a trace, a test, and tasks."""
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs/security.md").write_text(security)
    table = ["| Invariant | Control | Test owners |", "|---|---|---|"]
    (root / "docs/threat-model.md").write_text(
        "# Threat model\n\n## Invariant trace\n\n"
        + "\n".join([*table, *(ROWS if rows is None else rows)])
        + "\n\n## Known gaps\n\n| 9 | not | a trace row |\n"
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_access.py").write_text(TESTS)
    milestone = root / "docs/curriculum/milestones/M07-identity"
    milestone.mkdir(parents=True, exist_ok=True)
    for task_id, status in (statuses or {"M07-T07": "planned"}).items():
        (milestone / f"{task_id}-example.md").write_text(
            TASK.format(task_id=task_id, status=status)
        )
    return root


def messages(issues: list[ValidationIssue]) -> list[str]:
    return [issue.message for issue in issues]


def with_invariant(line: str) -> str:
    """The security model with one more line at the end of its invariants."""
    return SECURITY.replace("\n## Trust zones", f"{line}\n\n## Trust zones")


def test_the_repository_threat_model_traces_every_security_invariant() -> None:
    assert validate_threat_model(ROOT) == []


def test_a_complete_trace_has_no_issues(tmp_path: Path) -> None:
    assert validate_threat_model(repository(tmp_path)) == []


def test_an_invariant_added_without_a_trace_row_is_reported(tmp_path: Path) -> None:
    security = with_invariant("3. Restrictive changes apply at once.")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["invariant 3 is not traced"]
    assert issues[0].source == tmp_path / "docs/threat-model.md"


@pytest.mark.parametrize(
    ("owner", "problem"),
    [
        (
            "`tests/test_access.py::test_renamed`",
            "no such test: tests/test_access.py::test_renamed",
        ),
        (
            "`tests/test_gone.py::test_eligibility`",
            "no readable test file: tests/test_gone.py",
        ),
        ("`M07-T99`", "unknown task: M07-T99"),
        (
            "`tests/test_access.py::test_eligibility[case]`",
            "an owner must be a test or a task: "
            "tests/test_access.py::test_eligibility[case]",
        ),
        (
            "`tests/../src/test_x.py::test_eligibility`",
            "an owner must be a test or a task: "
            "tests/../src/test_x.py::test_eligibility",
        ),
        (
            "`tests/helpers.py::test_eligibility`",
            "an owner must be a test or a task: tests/helpers.py::test_eligibility",
        ),
        ("`the security team`", "an owner must be a test or a task: the security team"),
    ],
    ids=[
        "renamed-test",
        "missing-file",
        "unknown-task",
        "parametrized",
        "outside",
        "not-collected-file",
        "prose",
    ],
)
def test_an_owner_that_names_no_test_or_open_task_is_reported(
    tmp_path: Path, owner: str, problem: str
) -> None:
    rows = [f"| 1 | the predicate | {owner} |", ROWS[1]]

    assert messages(validate_threat_model(repository(tmp_path, rows))) == [
        f"invariant 1: {problem}"
    ]


def test_a_completed_task_cannot_stay_a_test_owner(tmp_path: Path) -> None:
    root = repository(tmp_path, statuses={"M07-T07": "completed"})

    assert messages(validate_threat_model(root)) == [
        "invariant 1: M07-T07 is completed; name the test it added"
    ]


@pytest.mark.parametrize(
    "module",
    [
        "# def test_eligibility() -> None: ...\n",
        "def test_eligibility_later() -> None: ...\n",
        'TEXT = """\ndef test_eligibility():\n"""\n',
        "if False:\n    def test_eligibility() -> None: ...\n",
        "def helper() -> None:\n    def test_eligibility() -> None: ...\n",
        "class Helper:\n    def test_eligibility(self) -> None: ...\n",
    ],
    ids=[
        "comment",
        "longer-name",
        "string",
        "dead-branch",
        "nested",
        "not-a-test-class",
    ],
)
def test_a_test_owner_must_be_a_test_pytest_collects(
    tmp_path: Path, module: str
) -> None:
    root = repository(tmp_path)
    tests = TESTS.replace("def test_eligibility", "def other")
    (root / "tests/test_access.py").write_text(tests + module)

    assert messages(validate_threat_model(root)) == [
        "invariant 1: no such test: tests/test_access.py::test_eligibility"
    ]


def test_a_test_written_with_a_space_before_its_parameters_counts(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_text(
        TESTS.replace("def test_eligibility()", "def test_eligibility ()")
    )

    assert validate_threat_model(root) == []


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"\xff\ndef test_eligibility(): pass\n", "no readable test file"),
        (b"def test_eligibility(:\n", "cannot parse test file"),
        (b"def test_eligibility(): pass\x00\n", "cannot parse test file"),
    ],
    ids=["not-utf-8", "syntax-error", "null-byte"],
)
def test_a_test_file_that_cannot_be_read_is_reported_not_raised(
    tmp_path: Path, content: bytes, problem: str
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_bytes(content)

    assert messages(validate_threat_model(root)) == [
        f"invariant 1: {problem}: tests/test_access.py",
        f"invariant 2: {problem}: tests/test_access.py",
    ]


@pytest.mark.parametrize(
    ("rows", "problems"),
    [
        (["| 1 |  | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | TBD | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | - | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (
            ["| 1 | the predicate | nobody yet |", ROWS[1]],
            ["invariant 1 has no test owner"],
        ),
        ([*ROWS, ROWS[0]], ["invariant 1 is traced twice"]),
        ([*ROWS, "| 7 | something | `M07-T07` |"], ["no invariant 7 to trace"]),
        (
            [ROWS[0], "| two | in the query | `M07-T07` |"],
            [
                "malformed trace row: | two | in the query | `M07-T07` |",
                "invariant 2 is not traced",
            ],
        ),
    ],
    ids=[
        "no-control",
        "placeholder",
        "dash",
        "no-owner",
        "twice",
        "unknown",
        "malformed",
    ],
)
def test_a_row_that_traces_nothing_is_reported(
    tmp_path: Path, rows: list[str], problems: list[str]
) -> None:
    assert messages(validate_threat_model(repository(tmp_path, rows))) == problems


@pytest.mark.parametrize(
    "hidden",
    [
        f"<!--\n{ROWS[1]}\n-->",
        f"```\n{ROWS[1]}\n```",
        f"### Archived\n\n{'| Invariant | Control | Test owners |'}\n|---|---|---|\n"
        f"{ROWS[1]}",
    ],
    ids=["comment", "fence", "second-table"],
)
def test_a_row_outside_the_visible_trace_table_traces_nothing(
    tmp_path: Path, hidden: str
) -> None:
    root = repository(tmp_path, [ROWS[0], "", hidden])

    assert messages(validate_threat_model(root)) == ["invariant 2 is not traced"]


@pytest.mark.parametrize(
    "line",
    [
        "3.  Two spaces.",
        "3.\tA tab.",
        "3) A parenthesis.",
        "   3. Indented.",
        "- A bullet.",
    ],
    ids=["two-spaces", "tab", "parenthesis", "indented", "bullet"],
)
def test_an_invariant_in_another_list_shape_is_reported(
    tmp_path: Path, line: str
) -> None:
    issues = validate_threat_model(repository(tmp_path, security=with_invariant(line)))

    assert messages(issues) == [
        f"an invariant must be written as `N. text`: {line.strip()}"
    ]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_invariants_must_count_up_from_one(tmp_path: Path) -> None:
    security = SECURITY.replace("2. Authorization", "1. Authorization")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == [
        "invariants must be numbered 1, 2, 3, ... in order",
        "no invariant 2 to trace",
    ]


def test_an_example_in_a_fenced_block_is_not_an_invariant(tmp_path: Path) -> None:
    security = with_invariant("\n```\n3. An example, not a rule.\n```")

    assert validate_threat_model(repository(tmp_path, security=security)) == []


def test_a_second_invariants_or_trace_heading_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path, security=f"{SECURITY}\n## Invariants\n\n3. More.\n")

    assert messages(validate_threat_model(root)) == ["## Invariants appears twice"]

    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text(f"{threat.read_text()}\n## Invariant trace\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace appears twice"]


def test_a_missing_threat_model_or_trace_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text("# Threat model\n\n## Abuse cases\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace is missing"]

    threat.unlink()

    assert messages(validate_threat_model(root)) == ["missing threat model"]


def test_a_security_model_with_no_invariants_cannot_pass(tmp_path: Path) -> None:
    security = SECURITY.replace("## Invariants", "## Rules")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["no numbered invariants to trace"]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_the_documentation_check_runs_the_trace(tmp_path: Path) -> None:
    root = repository(tmp_path, [ROWS[0]])

    assert messages(validate_repository(root)) == ["invariant 2 is not traced"]
````

Run:

```bash
uv run pytest tests/test_threat_model.py tests/test_documentation_validator.py -q
```

Expected: 45 passed; `tests/test_threat_model.py` alone gives 42 passed.

### Step 6 — No coding in this step: break it and decide

This step shows what the check protects, and records what you learned.

**Action 1:** break the check on purpose. In `src/knowledge_service/documentation.py`, near the end of `validate_threat_model`, delete the block that reports untraced invariants:

```python
    issues.extend(
        ValidationIssue(threat, f"invariant {number} is not traced")
        for number in numbers
        if number not in traced
    )
```

Run:

```bash
uv run pytest tests/test_threat_model.py -q
```

Expected: 6 failed, 36 passed, among them `test_an_invariant_added_without_a_trace_row_is_reported` and the three cases of `test_a_row_outside_the_visible_trace_table_traces_nothing`. Without the block, a new invariant with no row passes in silence, which is exactly the drift the check exists to stop. Put the block back and run again until all pass.

**Action 2:** see the check fail for real. In `docs/threat-model.md`, change `test_the_stream_record_holds_no_text` to `test_the_stream_record_holds_no_texts` and run `make docs-check`. Expected: it exits with an error and prints:

```text
docs/threat-model.md: invariant 5: no such test: tests/test_streaming.py::test_the_stream_record_holds_no_texts
```

The printed path starts with your repository's absolute path. Undo the change.

**Action 3:** under `## Decision`, write:

- why the trace is checked by code and not only reviewed;
- why a completed task may not stay a test owner;
- why only what a reader sees counts;
- which invariants have no test in the code today, and which task owns each;
- at least one **falsified assumption** in the shape "I assumed X. The review shows Y. So Z is wrong."

### Step 7 — No coding in this step: gates, evidence, and progress

This step proves everything still works and records the result.

**Action 1:** run the gates in order:

```bash
uv run pytest tests/test_threat_model.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2362 passed for `make test-unit`, 42 more than M06-T14, and 378 passed for `make test-integration`, the same as M06-T14. If it says there is no test database, start Docker and then `docker start knowledge-service-postgres`.

**Action 2:** complete the evidence file:

- **Design:** what the threat model holds, the trace format, what counts as a test owner, and where the check runs.
- **Verification:** the commands and counts, the deliberate break, the real `make docs-check` failure, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T01 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T02, and commit with `m07-t01: write the system threat model`.

## Reflection questions

1. The check proves that a test named in the trace exists. What does it not prove about that test, and who has to judge it?
2. The threat model says nothing calls `confirm_citations` yet. Which abuse case does that leave open today, and what would a User see if it happened?
3. Which invariant would be easiest to break by a later speed-up, such as caching search results, and which test in the trace would catch it?
4. When M07-T04 is finished, the check will fail until invariant 1 names a new test. Is that helpful or annoying? What would you lose if completed tasks were allowed to stay owners?
5. Which entry point in the model worries you most today, and what is the smallest change that would close it?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `## Inventory` names the control and a test, or the owning task, for each of the seven invariants.
- [ ] `docs/threat-model.md` covers assets, actors, trust zones, entry points, and abuse cases with mitigations for ingestion, retrieval, generation, agents, and operations.
- [ ] Its trace has one row per invariant, each with a control and at least one test owner, and no row claims a control the code does not have.
- [ ] `docs/security.md` points to the threat model.
- [ ] `make docs-check` fails when a trace row is missing or names a test that does not exist.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
