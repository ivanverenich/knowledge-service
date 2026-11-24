# M08-T16 — Pass the jobs-and-Confluence understanding gate

Task: [M08-T16 — Pass the jobs-and-Confluence understanding gate](../curriculum/milestones/M08-jobs-and-confluence/M08-T16-pass-the-jobs-and-confluence-gate.md)

## What you will do

This lesson closes milestone M08. You prove that you understand how a synchronization survives a crash. You draw it correctly, break it on purpose, and watch it recover. Then you add one new field to the Source interface on your own. The Confluence lessons M08-T11 to M08-T15 are deferred because there is no Confluence access yet. Everything here runs on the local folder Source and the fake test Source.

A concrete example, taken from the drill in Step 8. A folder holds 150 files, `doc-001.md` to `doc-150.md`, so a pass reads two pages of 100 and 50. The worker process is killed just before it stores `doc-120.md`.

| Time | Worker | Relay (`make worker-relay`) | PostgreSQL | Redis |
|---|---|---|---|---|
| 17:07:47 | claims the Job, reads page 1, commits it, reports `processed` 100 | | Job `running`; checkpoint `doc-100.md` | the message is with the worker, not acknowledged |
| 17:07:48 | stores `doc-101.md` to `doc-119.md`, then dies | | 119 Documents; the Job still says `running` | the message is still not acknowledged |
| 17:08:27 | a new worker starts and waits | | nothing changes | |
| 17:13:47 | | five minutes of silence: fails the attempt as `worker_lost` and plans a retry | Job `failed`, retry in about 15 seconds | |
| 17:14:05 | | the retry is due: queues the Job again and publishes it | Job `queued` | a new message |
| 17:14:07 | runs attempt 2 from `doc-100.md`: `processed` 50, `changed` 31 | | Job `succeeded`; 150 Documents | the new message is acknowledged; the dead worker's message is still listed |

| What the task asks | Where it is done |
|---|---|
| Explain delivery versus business state | Block 1; Step 2 |
| Update the sync diagrams | Step 3 |
| Recover an injected mid-page crash | Blocks 2 and 3; Step 6 (tests), Step 8 (the real worker) |
| Add one Source field independently | Block 4; Steps 4 and 5 |
| Evidence proves replay safety and stable Source semantics | Steps 6, 7, and 10 |
| Success, boundary, and typed failure or denial cases, on real infrastructure | Steps 5 and 6 (PostgreSQL), Step 8 (worker, relay, Redis) |
| Replay and cancellation behaviour explicit | Blocks 1 to 3 |
| Explain what would falsify the design | Step 7 and the reflection questions |
| Update only affected documentation | Steps 3 and 9 |

This lesson includes coding: Steps 4, 5, and 6 change code and add tests. Steps 1, 2, 3, 7, 8, 9, and 10 need no code, although Steps 3 and 9 edit documents and Step 8 adds two temporary lines that you remove again.

Terms used in this lesson:

- **Delivery:** a message in Redis that says "run Job J", and whether a worker has acknowledged it. It only carries the question.
- **Business state:** what PostgreSQL records as true: the Job's status and attempts, the Source's checkpoint, and the Documents. Every decision is made from it.
- **Acknowledge (ack):** a worker telling Redis "I am done with this message". Until then Redis keeps the message, so it can give it to another worker if this one dies.
- **Redelivery:** the same message given to a worker a second time. It is normal for a queue that delivers **at least once**.
- **Recovery:** the relay's check, from M08-T08, that fails an attempt whose worker has said nothing for five minutes.
- **Web URL:** the address where a person opens an item in its Source, such as a wiki page. It is the new field of this lesson.
- **Anchor:** the text stored with each Chunk that says where it came from, such as `leave.md#offset=0-21`. A Citation shows it as its link.

## Before you begin

This section explains every choice the lesson's code depends on. Read it first; the steps assume it.

### 1. Delivery is a question; PostgreSQL holds the answer

The drill above has two records of the same work. Redis has a message for the Job, and PostgreSQL has the Job's row. After the crash they disagree, and only one of them may decide.

| Moment | What Redis says | What PostgreSQL says | Who is right |
|---|---|---|---|
| The worker dies mid-page | the message is still with that worker | the Job is `running`, checkpoint `doc-100.md` | PostgreSQL: page 1 is done, page 2 is not |
| Redis gives the message to a new worker | "run this Job" | the Job is `running` | PostgreSQL: the claim refuses, nothing runs |
| Recovery fails the silent attempt | nothing changed | `failed`, `worker_lost`, retry planned | PostgreSQL |
| Attempt 2 succeeds | the dead worker's message is still listed | `succeeded`, 150 Documents | PostgreSQL |

The hidden basic fact: Redis does not know what the work did. It only knows whether a worker said "done with this message". A worker can die after storing half a page and before saying anything. So the message can never tell whether the work happened. Only the rows the work wrote can.

How bad would it be to trust the message? Very bad. A worker that ran a Job again because its message came back would read the whole Source twice. Worse, an older message arriving after a newer pass would undo the newer pass's work. This is why every handler first claims the Job in PostgreSQL. The claim moves `queued` to `running` under the row's lock, and any other status means "someone else has it, or it is done".

```python
# run_stored_job, in src/knowledge_service/worker/jobs.py
if job.status is not JobStatus.QUEUED:
    return _duplicate(job)
```

An everyday analogy: a delivery note says "deliver parcel 42". The warehouse ledger says whether parcel 42 already left. A second copy of the note does not send a second parcel, because the clerk checks the ledger first.

### 2. Why a kill in the middle of a page loses nothing

A pass stores records one by one, each in its own short transaction. It moves the checkpoint only after a whole page is stored (M08-T09). A kill can land anywhere in that sequence.

| Where the worker dies | What is already committed | What attempt 2 does |
|---|---|---|
| before `doc-101.md` | page 1 and its checkpoint | reads page 2 from `doc-100.md` |
| before `doc-120.md` | page 1, its checkpoint, and `doc-101.md` to `doc-119.md` | reads page 2 again; 101 to 119 compare equal and change nothing; 120 to 150 are new |
| before page 2's commit | every record of page 2, but not its checkpoint | reads page 2 again; all 50 compare equal |
| inside the finish | every page, but no tombstones | reads the last page again, then tombstones |

The hidden basic fact: storing a record is **idempotent**, which means doing it twice has the same effect as once, like pressing a lift button twice. Reconciliation compares the record's fingerprints with what is stored, and writes nothing when they match. So replaying a page is safe, and the checkpoint only has to be "no later than the truth", never exact.

That is why the drill's attempt 2 reports `processed` 50 and `changed` 31. It read all 50 files of page 2, but 19 of them were already stored.

What would break it: a checkpoint moved before the page is stored, or a list of "seen" items kept only in memory. In Step 7 you break the second one on purpose, and 11 tests fail.

### 3. The redelivered message and the leftover one

After the kill, two different messages exist. Look at them one at a time.

| Message | When it arrives | What the Job says then | What the worker does |
|---|---|---|---|
| The dead worker's original | any time Redis returns it | `running`, then later `succeeded` | claims nothing; logs `duplicate message` |
| The retry's new message | after the relay requeues the Job | `queued` | claims it and runs attempt 2 |

Dramatiq returns a dead worker's unacknowledged messages only during its occasional maintenance, so the original may come back minutes later, or not during the drill at all. It does not matter when. Whatever it finds in PostgreSQL decides what it does.

The test in Step 6 cannot kill a real process. It stops the pass with a cancellation, which records nothing, just like a kill. It then delivers the same message again and checks that it runs nothing. Step 8 does the real kill with `make worker`.

### 4. The new field: where a person opens the item

The task asks you to add one field to the Source interface "independently". It should travel from the Source to where a user sees it, without breaking anything stored before. We add `web_url`: the address of the item's page in its Source. A local folder has none. Confluence will have one for every page. Today a Citation's link for a folder item is just its file name, such as `leave.md#offset=0-21`. With a web URL it becomes `https://wiki.example.com/spaces/HR/pages/42#offset=0-21`.

Here is the path the field takes:

| Stage | File | What happens |
|---|---|---|
| Source reports it | `ingestion/sources.py` | `SourceMetadata.web_url`; refused if it is not text, is blank, or is longer than 2048 characters |
| Normalization | `ingestion/normalization.py` | kept only in its safe form, and only if a Citation would show it unchanged; otherwise `None` |
| Fingerprint | `ingestion/fingerprints.py` | counted only when present |
| Chunks | `ingestion/reconciliation.py` | passed to the anchors, so each Chunk links to the page |
| Citation | `ingestion/provenance.py` | keeps the anchor's own `#offset=` or `#page=` part |

Why must the fingerprint count it? A Chunk stores its anchor. Chunks are rewritten only when the content fingerprint changes. Without the URL in the fingerprint, a page that moved would keep its old links forever.

| Before | After |
|---|---|
| `{"text": ..., "blocks": ...}` | `{"text": ..., "blocks": ..., "web_url": ...}`, the last key only when there is a URL |

Why only when present? The fingerprint of every Document already stored was computed without the key. Adding `"web_url": None` everywhere would change every fingerprint. The next pass would then rewrite every Chunk of every Document, and they would all need new embeddings. Leaving the key out when there is no URL keeps every old fingerprint byte for byte. The existing test `test_the_canonical_form_does_not_drift` pins one such fingerprint, and it fails if you add the key always. This is what "stable Source semantics" means here: a Source that does not send the field sees no change at all.

The cost: when a page moves, its Chunks are rewritten once and need their embeddings published again. Its content version goes from 1 to 2. Old Answers that cited it are withdrawn by the Citation check, which is right, because their links are stale.

### 5. Review finding: a Citation cut off the place in the page

An adversarial review of the first version stored the anchor `https://wiki.example.com/p/42#offset=0-10` correctly. But a Citation showed only `https://wiki.example.com/p/42`. The function that cleans text for display, `display_text`, cuts everything after `?` or `#` from every web address, so every Chunk of the page got the same link.

The fix keeps exactly one kind of fragment: the anchor's own `#offset=START-END` or `#page=N`. Anything else after `#` is still cut, so `#comments` or `#offset=0-21&token=SECRET` never reach a user.

```python
# before
return display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0]) or "[link]"
```

```python
# after (shortened)
fragment = url[url.find("#") :] if "#" in url else ""
return shown + fragment if _ANCHOR_FRAGMENT.fullmatch(fragment) else shown
```

### 6. Review finding: what is stored must be what is shown

The review found a URL such as `https://wiki.example.com/x:SECRET@y`. The safe-form function kept it, because it keeps paths. But a Citation cleans `x:SECRET@` away as if it were a password, so a user would see `https://wiki.example.com/y`, a different page. The stored anchor would still hold `SECRET`.

Now normalization keeps a URL only if cleaning it for display changes nothing. Such a URL is dropped, and the item falls back to its external ID, as if it had no URL.

### 7. Review finding: a web URL that is not text

The review passed bytes, `b"https://..."`, as the web URL. Bytes have `strip()` and a length, so the checks passed. Normalization then crashed with a `TypeError`, an error nobody had planned for. Now `SourceMetadata` refuses anything that is not a `str` with `InvalidSource`, the Source contract's own error.

### 8. Review finding: comparing with a clean run is not enough

The crash test runs two identical Sources: one is interrupted, one is not. It then compares everything stored. The review asked what happens when a bug breaks both runs the same way. We tried it: with the "seen" list emptied, both runs tombstoned the same wrong Documents, and the comparison passed.

So the test now also checks the expected result itself: which Documents are available, their content versions, and who may read them. It also compares more than the first version did: chunk text and anchors, titles, fingerprints, readers, every audit event of each Document, the Source's checkpoint and `synchronization.finished` events, purge Jobs per Source, and the number of attempts.

### 9. Known limits

- A secret written into the URL's path itself is kept, because paths are kept on purpose.
- Two spellings of one page, such as an explicit `:443` port, count as a move and rewrite its Chunks once.
- Nothing in the worker publishes embeddings yet, so a moved page's new Chunks wait for that.
- The test kills with a cancellation, not a real process kill. The drill in Step 8 covers the real kill.
- M08-T11 to M08-T15 (Confluence) are deferred. No Source sends a web URL yet except the test Source.

## Step-by-step

Start PostgreSQL and Redis (`make redis-up`) before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true and what you expect to see. You check the predictions in Steps 7, 8, and 10.

Create `docs/evidence/M08-T16-jobs-and-confluence-gate.md`:

```markdown
# M08-T16 — Jobs-and-Confluence gate evidence

## Invariant

A synchronization's truth lives only in PostgreSQL: the Job's status and
attempts, the Source's checkpoint and seen IDs, and the Documents. A Redis
message only asks a worker to look. A worker killed at any point of a pass
loses at most its uncommitted work: a redelivered message finds the Job
running and runs nothing, recovery fails the silent attempt, and the retry
resumes from the last committed page and ends with exactly what an
uninterrupted pass stores. A Source item's `web_url` reaches every Chunk's
anchor and Citation only in a safe form that a Citation shows unchanged;
items without one keep their stored fingerprints.

## Owners

- Business state and every decision: PostgreSQL.
- Delivery: Redis and Dramatiq, at least once.
- Retries and recovery of a lost worker: the stored Job (M08-T04) and
  `make worker-relay` (M08-T08).
- Resume point: `ingestion.checkpoints` (M08-T09).
- What a Citation may show: `ingestion.provenance` and normalization.

## Expected failing check

Before the change,
`uv run pytest tests/test_synchronization_gate_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- The worker is killed before `doc-120.md` of 150 files, in pages of 100. What do attempt 2's `processed` and `changed` say:
- Right after the kill, what does the Job's row say, and where is its message:
- A page's web URL moves and its text stays. Which of the Document's two versions change:
- How many tests in the gate suite fail if a page commit stores no seen IDs:

## Delivery versus business state

## Sync diagrams

## Injected crash

## New Source field

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** run the expected failing check and see `no tests ran`. Each prediction has your answer after the colon.

### Step 2 — No coding in this step: explain delivery versus business state

This step puts Block 1 into your own words in the evidence file. It is the core idea the gate tests.

Under `## Delivery versus business state`, write:

- one table with two columns, Redis and PostgreSQL, and these rows: what it holds, what it can answer, what happens to it when a worker dies mid-page, and who reads it before running anything;
- one sentence on why the redelivered message runs nothing;
- one sentence on why the leftover message in Redis is harmless even after the Job succeeded.

Use the drill's numbers from "What you will do" if they help.

**Check:** a reader who knows nothing about Dramatiq can tell from your text which system to ask "did the pass finish?" and why.

### Step 3 — No coding in this step: update the sync diagrams

The first diagram in `docs/architecture.md` under "Synchronization" is the target drawn at the start of the course. It is wrong now: the queue carries only a Job's ID, not "source + checkpoint", and no embedding happens in the pass. This step replaces it with what is built, and adds a diagram of a Job's statuses.

**Action 1:** in `docs/architecture.md`, replace everything from `### Synchronization` up to and including the paragraph that starts "The diagram above is the target." with:

````markdown
### Synchronization

Two systems carry a synchronization, and only one of them holds the truth.
Redis holds **delivery**: a message that says "run Job J" and whether a
worker has acknowledged it. PostgreSQL holds **business state**: the Job's
status and attempts, the Source's checkpoint and seen IDs, and the
Documents. A message can arrive twice, late, or after its worker died; every
decision is made from PostgreSQL, so a message only ever asks a question.

```mermaid
sequenceDiagram
    participant Relay as worker-relay
    participant DB as PostgreSQL (business state)
    participant Redis as Redis (delivery)
    participant Worker
    participant Source
    Relay->>DB: fail attempts silent for five minutes and queued Jobs whose message is 30 minutes old, requeue due retries, store due synchronize_source Jobs
    Relay->>Redis: publish one message per unpublished Job: its ID only
    Redis->>Worker: deliver at least once; again if a worker died before acknowledging
    Worker->>DB: claim: queued to running with an attempt row; any other status runs nothing
    Worker->>Redis: take the Source's lease
    loop each page, from the stored checkpoint
        Worker->>Source: fetch_page, or fetch_changes in an incremental pass
        Worker->>DB: narrow removed readers, then reconcile each record, one transaction each
        Worker->>DB: commit_page: checkpoint and seen IDs, one fenced transaction
        Worker->>DB: report the attempt's progress, its sign of life
    end
    Worker->>DB: finish_pass: tombstones, purge Jobs, cursor
    Worker->>DB: store the end only if the Job still runs this attempt
    Worker->>Redis: acknowledge the message
```

A Job's status moves only in PostgreSQL, and every move is checked under
the Job's row lock:

```mermaid
stateDiagram-v2
    [*] --> queued: stored, then published
    queued --> running: a worker claims it
    running --> succeeded: the pass ended
    running --> failed: a typed failure, or recovery after five silent minutes
    failed --> queued: retry time reached with attempts left (worker-relay), or an operator reopens it
    queued --> cancelled: an operator cancels it
    failed --> cancelled: an operator cancels it
    succeeded --> [*]
    failed --> [*]: permanent, or no attempts left, until an operator reopens it
    cancelled --> [*]
```

A worker killed in the middle of a page loses only that page's uncommitted
work. Its message stays unacknowledged in Redis, and a redelivery finds the
Job running and runs nothing. Recovery fails the silent attempt as
`worker_lost`, the relay requeues it at its retry time, and the next attempt
resumes from the last committed page; records of the killed page that had
already landed compare equal and change nothing.
`tests/test_synchronization_gate_integration.py` stops a worker for good,
by cancelling it, at every step of a pass and compares the result with a
pass nobody interrupted; a cancellation still runs cleanup that a killed
process skips, so the real kill is a drill (`docs/operations.md`).
Embeddings are not part of the pass: `publish_document_embeddings` runs
separately.

#### Synchronization as built in M03

One call, `synchronize_source`, runs a complete pass; since M08-T10 the
worker runs it for every `synchronize_source` Job, as drawn above.
````

**Action 2:** check each status move in the second diagram against `_ALLOWED` in `src/knowledge_service/jobs.py`. Every arrow must be a move `_ALLOWED` permits, and every move it permits must have an arrow.

**Check:** `make docs-check` passes. Under `## Sync diagrams` in the evidence file, list what the old diagram got wrong.

### Step 4 — Coding step: the new field, from the Source to the Citation

**In plain words:** a Source can now say where a person opens each item, such as a wiki page's address. The service checks it, keeps only a version that is safe to show, and makes every stored piece of the item's text link to that page. If the address changes, the pieces are rewritten once. If a Source never sends an address, nothing about its stored items changes.

| File | Change |
|---|---|
| `ingestion/sources.py` | `MAX_WEB_URL_CHARACTERS` and `SourceMetadata.web_url`, with three checks |
| `ingestion/normalization.py` | `NormalizedDocument.web_url` and `_shown_url` |
| `ingestion/fingerprints.py` | `content_fingerprint` takes `web_url` |
| `ingestion/reconciliation.py` | passes it to the fingerprint and to the anchors |
| `ingestion/provenance.py` | a Citation keeps an anchor's own fragment |

**Action 1:** in `src/knowledge_service/ingestion/sources.py`, replace the `SourceMetadata` class with:

```python
# Longer links are refused: no real page address comes near it.
MAX_WEB_URL_CHARACTERS = 2048


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Descriptive facts a Source reports about one item, before normalization.

    `web_url` is where a person opens the item in its Source, such as a wiki
    page's address, or None when the Source has no such page. It is taken
    as the Source gives it; normalization keeps it only if it is safe to show.
    """

    title: str
    modified_at: datetime
    size_bytes: int
    web_url: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvalidSource("source metadata title must not be empty")
        _require_utc(self.modified_at, "modified_at")
        if self.size_bytes < 0:
            raise InvalidSource("source metadata size must not be negative")
        if self.web_url is not None:
            if type(self.web_url) is not str:
                raise InvalidSource("a web URL must be text")
            if not self.web_url.strip():
                raise InvalidSource("a web URL must not be empty when provided")
            if len(self.web_url) > MAX_WEB_URL_CHARACTERS:
                raise InvalidSource(
                    f"a web URL must be at most {MAX_WEB_URL_CHARACTERS} characters"
                )
```

`type(...) is not str` comes first, because bytes would pass the other two checks (Block 7).

**Action 2:** in `src/knowledge_service/ingestion/normalization.py`, add this import after the `text_parser` import:

```python
from knowledge_service.ingestion.provenance import display_text, display_url
```

Add the field at the end of `NormalizedDocument`, after `parsed`:

```python
    # The item's page in its Source, already safe to show, or None.
    web_url: str | None = None
```

It has a default, so every other place that builds a `NormalizedDocument` keeps working. In `normalize`, add the last argument of the `NormalizedDocument(...)` call, and add `_shown_url` right after `normalize`:

```python
        parsed=parsed,
        web_url=_shown_url(metadata.web_url if metadata is not None else None),
    )


def _shown_url(web_url: str | None) -> str | None:
    """Keep a web URL only in the safe form a Citation shows unchanged.

    A link a Citation would show differently, such as a path segment shaped
    like `name:password@`, is dropped, so what is stored is what is shown.
    """
    shown = None if web_url is None else display_url(web_url)
    return shown if shown is not None and display_text(shown) == shown else None
```

`display_url` gives the safe form, or `None` for anything that is not a plain `http` or `https` page. The second check, `display_text(shown) == shown`, is Block 6: what is stored must be what a Citation shows.

**Action 3:** in `src/knowledge_service/ingestion/fingerprints.py`, replace `content_fingerprint` with:

```python
def content_fingerprint(parsed: ParsedDocument, *, web_url: str | None = None) -> str:
    """Return the fingerprint of a document's text and structure.

    Everything the chunker reads counts: the parsed text and every field of
    every block, including offsets, because citations point into the text.
    So does `web_url`, the page every Chunk's anchor links to; without one
    the fingerprint is the same as before the field existed.
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
    value: dict[str, object] = {"text": parsed.text, "blocks": blocks}
    if web_url is not None:
        value["web_url"] = web_url
    return canonical_hash(_CONTENT_LABEL, value)
```

The key is added only when there is a URL (Block 4).

**Action 4:** in `src/knowledge_service/ingestion/reconciliation.py`, change the first line of `reconcile_document`'s body after `provenance = ...`:

```python
    content = content_fingerprint(normalized.parsed, web_url=normalized.web_url)
```

In `build_chunks`, pass the URL to `chunk_sources`, which already had a `source_url` parameter waiting for it:

```python
    sources = chunk_sources(
        normalized.parsed,
        drafts,
        external_id=normalized.provenance.external_id,
        source_url=normalized.web_url,
    )
```

**Action 5:** in `src/knowledge_service/ingestion/provenance.py`, add this pattern after `_QUERY_OR_FRAGMENT`:

```python
# The fragment an anchor adds after its location: where the Chunk starts.
_ANCHOR_FRAGMENT = re.compile(r"#(?:offset=\d+-\d+|page=\d+)")
```

and replace `_shown_url` with:

```python
def _shown_url(url: str) -> str:
    """Show an address found in text; its query and fragment are cut off first.

    Only an anchor's own fragment, `#offset=START-END` or `#page=N`, is kept,
    so a Citation of a page with a web URL still leads to its place in it.
    """
    shown = display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0])
    if shown is None:
        return "[link]"
    fragment = url[url.find("#") :] if "#" in url else ""
    return shown + fragment if _ANCHOR_FRAGMENT.fullmatch(fragment) else shown
```

`fullmatch` accepts the fragment only when it is exactly the anchor's own form, so `#offset=0-21&token=SECRET` is still cut (Block 5).

**Check:** `uv run ruff format . && uv run ruff check . && uv run pyright` report no errors, and `make test-unit` still passes, including `test_the_canonical_form_does_not_drift`.

### Step 5 — Coding step: unit tests for the field

**In plain words:** these tests check the field before anything touches the database. They try empty, too long, and non-text addresses; addresses with passwords, tokens, wrong schemes, and invisible characters; and they check that a Citation keeps the place in the page but nothing else after `#`.

Create `tests/test_source_web_url.py`:

```python
"""The `web_url` a Source reports for an item, before anything is stored."""

from datetime import UTC, datetime

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.fingerprints import content_fingerprint
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.provenance import display_text
from knowledge_service.ingestion.sources import (
    MAX_WEB_URL_CHARACTERS,
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-0000000016c3")
WIKI = "https://wiki.example.com/spaces/HR/pages/42"


def metadata(web_url: str | None) -> SourceMetadata:
    return SourceMetadata(
        title="Leave",
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        size_bytes=0,
        web_url=web_url,
    )


def normalized(web_url: str | None) -> NormalizedDocument:
    record = SourceRecord.present(
        external_id="leave.txt",
        version="v1",
        media_type="text/plain",
        content=b"Twenty days a year.",
        access=SourceAccess(public=True),
        metadata=metadata(web_url),
    )
    outcome = normalize(record, source_id=SOURCE_ID)
    assert isinstance(outcome, NormalizedDocument)
    return outcome


@pytest.mark.parametrize("web_url", ["", "   "])
def test_an_empty_web_url_is_refused(web_url: str) -> None:
    with pytest.raises(InvalidSource, match="must not be empty"):
        metadata(web_url)


def test_a_web_url_longer_than_the_limit_is_refused() -> None:
    too_long = WIKI + "/" + "x" * MAX_WEB_URL_CHARACTERS

    with pytest.raises(InvalidSource, match="at most"):
        metadata(too_long)


def test_metadata_without_a_web_url_still_works() -> None:
    assert (
        SourceMetadata(
            title="Leave", modified_at=datetime(2026, 1, 1, tzinfo=UTC), size_bytes=0
        ).web_url
        is None
    )


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (WIKI, WIKI),
        (f"{WIKI}?token=SECRET#comments", WIKI),
        ("https://reader:hunter2@wiki.example.com/x", "https://wiki.example.com/x"),
        ("HTTPS://Wiki.Example.com/x", "https://wiki.example.com/x"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("https://wiki.example.com/x\u202e", None),
        # A Citation would show this as another page, so it is not kept.
        ("https://wiki.example.com/x:SECRET@y", None),
        (None, None),
    ],
)
def test_normalization_keeps_only_the_safe_form_of_a_web_url(
    given: str | None, kept: str | None
) -> None:
    assert normalized(given).web_url == kept


def test_a_web_url_changes_the_fingerprint() -> None:
    parsed = normalized(None).parsed

    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(parsed)
    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(
        parsed, web_url=WIKI + "/moved"
    )


def test_a_web_url_that_is_not_text_is_refused() -> None:
    with pytest.raises(InvalidSource, match="must be text"):
        metadata(b"https://wiki.example.com/x")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("fragment", ["#offset=0-21", "#page=3"])
def test_a_citation_keeps_the_place_in_the_page(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI + fragment


@pytest.mark.parametrize(
    "fragment", ["#comments", "#offset=0-21&token=SECRET", "#page=3?x", "#"]
)
def test_any_other_fragment_is_still_cut(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI
```

The line `"https://wiki.example.com/x\u202e"` must contain the six characters backslash, `u`, `2`, `0`, `2`, `e`, not the invisible character they stand for. Some editors turn the escape into the character. Check it with `grep -n 'u202e' tests/test_source_web_url.py`.

**Check:** `uv run pytest tests/test_source_web_url.py -q` gives `21 passed`.

### Step 6 — Coding step: the gate suite on real PostgreSQL

**In plain words:** this file runs real synchronization Jobs against PostgreSQL. In the first part, a worker is stopped for good at one point of a pass, and the test lets everything that happens in production happen next: the message comes back, recovery gives up on the worker, the retry runs. Then it compares what is stored with a pass nobody interrupted, and with the result we expect. It does this for nine different points. The second part sends web URLs through a whole Job and checks where every stored piece of text leads.

| Test | What it proves |
|---|---|
| `test_a_worker_killed_mid_pass_ends_as_if_nothing_happened`, 9 kill points | replay safety: redelivery runs nothing, recovery fails `worker_lost`, the retry resumes from the stored checkpoint, the result equals a clean pass and the expected one |
| `test_a_web_url_becomes_where_every_chunk_leads` | the field reaches the anchors |
| `test_a_moved_page_rewrites_its_chunks_and_nothing_else` | a move: content version 2, authorization version 1 |
| `test_the_same_web_url_again_changes_nothing` | idempotent |
| `test_a_new_token_in_the_same_link_changes_nothing` | only the safe form counts |
| `test_a_secret_in_a_web_url_never_reaches_a_chunk` | no password or token in an anchor |
| `test_an_unsafe_web_url_falls_back_to_the_external_id` | `javascript:` is dropped |
| `test_a_source_that_starts_sending_links_rewrites_the_chunks_once` | a Source that adds the field later |

How the kill works: the test replaces one step of the pass, such as `reconcile_document`, with a version that hangs forever at its n-th call. The test then cancels the worker's task. Nothing after that point runs, so nothing more is recorded, as in a killed process. `pay.txt` is public in the first pass and names an unknown principal in the second, so the second pass takes it away from everyone. That exercises the narrowing that runs before reconciliation.

Create `tests/test_synchronization_gate_integration.py`:

```python
"""The M08 gate on real PostgreSQL: a worker killed mid-page, and a new field.

The first part kills a synchronization Job's worker before each record of a
pass in turn, redelivers its message, lets recovery and the retry finish
the pass, and compares everything stored with a pass nobody interrupted.
The second part sends a Source item's web address, `web_url`, through the
same Job and checks where every Chunk's anchor leads.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

import knowledge_service.ingestion.synchronization as synchronization
from fakes import FakeSource
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourcePort,
    SourceRecord,
)
from knowledge_service.ingestion.synchronization_jobs import SynchronizationJobs
from knowledge_service.job_dispatch import requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.job_recovery import recover_stuck_jobs
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.leases import Lease, LeaseStore
from knowledge_service.persistence.jobs import enqueue_job, load_job
from knowledge_service.persistence.schema import (
    access_grants,
    audit_events,
    chunks,
    documents,
    job_attempts,
    jobs,
    sources,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JobOutcome, run_stored_job

pytestmark = pytest.mark.integration

CLEAN = SourceId.parse("00000000-0000-0000-0000-0000000016c1")
CRASHED = SourceId.parse("00000000-0000-0000-0000-0000000016c2")
RETRIES = RetryPolicy(
    base_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=1),
    jitter=lambda: 0.0,
)


class MemoryLeases:
    """A lease store that always grants and renews; it holds no state."""

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return Lease(name=name, token=name, ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        return True

    async def release(self, lease: Lease) -> bool:
        return True


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def register(runtime: DatabaseRuntime, source_id: SourceId) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=source_id.value,
                kind="local_directory",
                location=f"/srv/{source_id}",
                display_name="Handbook",
                enabled=True,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                default_public=True,
            )
        )


async def queue(runtime: DatabaseRuntime, source_id: SourceId, at: datetime) -> JobId:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=at,
    )
    async with runtime.transaction() as connection:
        return (await enqueue_job(connection, job)).job.job_id


def handler(source: SourcePort, at: datetime) -> SynchronizationJobs:
    """The real handler, opening `source` for every stored Source."""

    @asynccontextmanager
    async def memory() -> AsyncGenerator[LeaseStore]:
        yield MemoryLeases()

    return SynchronizationJobs(
        open_source=lambda _settings: source,
        leases=memory,
        principal_directory=None,
        full_pass_interval=timedelta(hours=24),
        retry_policy=RETRIES,
        clock=lambda: at,
    )


async def deliver(
    runtime: DatabaseRuntime, job_id: JobId, source: SourcePort, at: datetime
) -> JobOutcome:
    """Deliver the Job's message once, the way a worker does."""
    return await run_stored_job(
        runtime,
        job_id,
        now=at,
        handlers={JobKind.SYNCHRONIZE_SOURCE: handler(source, at)},
    )


async def sync(
    runtime: DatabaseRuntime, source_id: SourceId, source: SourcePort, at: datetime
) -> Job:
    """Queue one Job at `at` and run it to its end."""
    job_id = await queue(runtime, source_id, at)
    return (await deliver(runtime, job_id, source, at)).job


def item(
    name: str,
    text: str = "",
    *,
    web_url: str | None = None,
    principals: tuple[str, ...] = (),
) -> SourceRecord:
    """A text item; public unless it names principals."""
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=not principals, principals=principals),
        metadata=SourceMetadata(
            title=name,
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            size_bytes=0,
            web_url=web_url,
        ),
    )


@dataclass(frozen=True)
class Stored:
    """Everything a pass leaves behind for one Document, without its IDs."""

    title: str
    content_fingerprint: str
    content_version: int
    authorization_version: int
    availability: str
    chunks: tuple[tuple[str, str], ...]
    readers: tuple[str, ...]
    events: tuple[str, ...]

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(anchor for _, anchor in self.chunks)


async def snapshot(runtime: DatabaseRuntime, source_id: SourceId) -> dict[str, Stored]:
    """What the Source's Documents look like, keyed by their external ID."""
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(documents).where(documents.c.source_id == source_id.value)
            )
        ).mappings()
        stored: dict[str, Stored] = {}
        for row in rows:
            document_id = row["document_id"]
            texts = await connection.execute(
                sa.select(chunks.c.text, chunks.c.source_anchor)
                .where(chunks.c.document_id == document_id)
                .order_by(chunks.c.ordinal)
            )
            readers = await connection.scalars(
                sa.select(access_grants.c.subject_kind).where(
                    access_grants.c.document_id == document_id
                )
            )
            events = await connection.scalars(
                sa.select(audit_events.c.action).where(
                    audit_events.c.target_id == document_id
                )
            )
            stored[row["external_id"]] = Stored(
                title=row["title"],
                content_fingerprint=row["content_fingerprint"],
                content_version=row["content_version"],
                authorization_version=row["authorization_version"],
                availability=row["availability"],
                chunks=tuple((text, anchor or "") for text, anchor in texts),
                readers=tuple(sorted(readers)),
                events=tuple(sorted(events)),
            )
    return stored


@dataclass(frozen=True)
class SourceState:
    """What a pass leaves on the Source itself."""

    checkpoint: str | None
    pass_mode: str | None
    finished_events: int
    purges: int


async def source_state(runtime: DatabaseRuntime, source_id: SourceId) -> SourceState:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(sources.c.checkpoint, sources.c.pass_mode).where(
                    sources.c.source_id == source_id.value
                )
            )
        ).one()
        finished = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(audit_events)
            .where(
                audit_events.c.action == AuditAction.SYNCHRONIZATION_FINISHED.value,
                audit_events.c.target_id == source_id.value,
            )
        )
        purges = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
                jobs.c.target_id.in_(
                    sa.select(documents.c.document_id).where(
                        documents.c.source_id == source_id.value
                    )
                ),
            )
        )
    return SourceState(row.checkpoint, row.pass_mode, finished or 0, purges or 0)


async def attempts(runtime: DatabaseRuntime, job_id: JobId) -> int:
    async with runtime.transaction() as connection:
        return (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(job_attempts)
                .where(job_attempts.c.job_id == job_id.value)
            )
        ) or 0


async def fall_silent(runtime: DatabaseRuntime, job_id: JobId) -> None:
    """Make the database's last word from the Job's worker six minutes old."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(job_attempts.c.job_id == job_id.value)
            .values(heard_at=job_attempts.c.heard_at - timedelta(minutes=6))
        )


# --- A worker killed mid-page

FIRST: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt")],
    [item("pay.txt"), item("old.txt")],
]
# Pass two edits b.txt, takes pay.txt away from everyone it could resolve,
# adds c and e, and no longer lists old.txt.
SECOND: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt", "The b.txt policy, rewritten.")],
    [item("c.txt"), item("pay.txt", principals=("payroll",))],
    [item("e.txt")],
]
# After pass two: old.txt is tombstoned, b.txt has new text, and nobody
# this service knows may read pay.txt any more.
EXPECTED = {
    "a.txt": ("available", 1, ("public",)),
    "b.txt": ("available", 2, ("public",)),
    "c.txt": ("available", 1, ("public",)),
    "e.txt": ("available", 1, ("public",)),
    "old.txt": ("tombstoned", 1, ("public",)),
    "pay.txt": ("available", 1, ()),
}
# Where the worker dies: before the n-th call of one step of the pass.
KILL_POINTS = [
    *(("reconcile_document", n) for n in range(1, 6)),
    *(("commit_page", n) for n in range(1, 4)),
    ("finish_pass", 1),
]


@pytest.mark.parametrize(("step", "call"), KILL_POINTS)
async def test_a_worker_killed_mid_pass_ends_as_if_nothing_happened(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    call: int,
) -> None:
    """Kill the worker before call `call` of `step` in the second pass."""
    earlier = datetime.now(UTC) - timedelta(hours=2)
    for source_id in (CLEAN, CRASHED):
        await register(runtime, source_id)
        await sync(runtime, source_id, FakeSource(FIRST), earlier)
    now = datetime.now(UTC)
    clean = await sync(runtime, CLEAN, FakeSource(SECOND), now)
    assert clean.status is JobStatus.SUCCEEDED

    # The injected crash: the worker stops for good at one exact point and
    # is cancelled, so it records nothing more. Unlike a killed process, a
    # cancellation still rolls back and releases; a real kill is a drill.
    real = getattr(synchronization, step)
    reached = asyncio.Event()
    calls = 0

    async def dies_here(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == call:
            reached.set()
            await asyncio.Event().wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(synchronization, step, dies_here)
    job_id = await queue(runtime, CRASHED, now)
    worker = asyncio.create_task(deliver(runtime, job_id, FakeSource(SECOND), now))
    # A kill point the pass never reaches fails here instead of hanging.
    await asyncio.wait_for(reached.wait(), timeout=30)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    monkeypatch.setattr(synchronization, step, real)

    # Delivery: the broker gives the message to another worker. The Job is
    # still running in PostgreSQL, so that delivery runs nothing.
    redelivered = FakeSource(SECOND)
    duplicate = await deliver(runtime, job_id, redelivered, now)
    assert duplicate.duplicate is True
    assert redelivered.checkpoints == []

    # Business state: only recovery, five silent minutes later, may decide
    # the worker is gone and plan the retry.
    await fall_silent(runtime, job_id)
    recovered = await recover_stuck_jobs(
        runtime, now=datetime.now(UTC), retry_policy=RETRIES
    )
    assert recovered.stale == (job_id,)
    async with runtime.transaction() as connection:
        lost = await load_job(connection, job_id=job_id)
    assert lost is not None
    assert lost.last_error_code == JobFailure.WORKER_LOST.value
    resume_from = (await source_state(runtime, CRASHED)).checkpoint

    later = datetime.now(UTC) + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(SECOND)
    outcome = await deliver(runtime, job_id, retried, later)

    assert outcome.job.status is JobStatus.SUCCEEDED
    assert await attempts(runtime, job_id) == 2
    # The retry starts where the last committed page left off.
    assert retried.checkpoints[0] == resume_from
    stored = await snapshot(runtime, CRASHED)
    assert stored == await snapshot(runtime, CLEAN)
    assert await source_state(runtime, CRASHED) == await source_state(runtime, CLEAN)
    # Both are right, not merely the same.
    assert {
        name: (document.availability, document.content_version, document.readers)
        for name, document in stored.items()
    } == EXPECTED
    assert (await source_state(runtime, CRASHED)).purges == 1


# --- A new Source field: web_url

WIKI = "https://wiki.example.com/spaces/HR/pages/42"


async def anchors(runtime: DatabaseRuntime, name: str) -> tuple[str, ...]:
    return (await snapshot(runtime, CRASHED))[name].anchors


async def test_a_web_url_becomes_where_every_chunk_leads(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_moved_page_rewrites_its_chunks_and_nothing_else(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt", web_url=WIKI)]]), start)
    moved = "https://wiki.example.com/spaces/HR/pages/43"

    job = await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=moved)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{moved}#offset=0-21",)
    # The text is the same, but every Chunk stores the link, so they change.
    assert (stored.content_version, stored.authorization_version) == (2, 1)
    assert job.status is JobStatus.SUCCEEDED


async def test_the_same_web_url_again_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    pages = [[item("leave.txt", web_url=WIKI)]]
    await sync(runtime, CRASHED, FakeSource(pages), start)
    before = await snapshot(runtime, CRASHED)

    await sync(runtime, CRASHED, FakeSource(pages), start + timedelta(minutes=5))

    assert await snapshot(runtime, CRASHED) == before


async def test_a_secret_in_a_web_url_never_reaches_a_chunk(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    leaky = "https://reader:hunter2@wiki.example.com/spaces/HR/pages/42?token=SECRET#x"

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=leaky)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_new_token_in_the_same_link_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=one")]]),
        start,
    )
    before = await snapshot(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=two")]]),
        start + timedelta(minutes=5),
    )

    # Only the safe form counts, so a new session token rewrites nothing.
    assert await snapshot(runtime, CRASHED) == before


async def test_an_unsafe_web_url_falls_back_to_the_external_id(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url="javascript:alert(1)")]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == ("leave.txt#offset=0-21",)


async def test_a_source_that_starts_sending_links_rewrites_the_chunks_once(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt")]]), start)

    # A Source that later starts sending links rewrites the Chunks once.
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{WIKI}#offset=0-21",)
    assert stored.content_version == 2
```

**Check:** `uv run pytest tests/test_synchronization_gate_integration.py -q -m integration` gives `16 passed`.

### Step 7 — No coding in this step: break it on purpose

This step shows which test protects the most important line of M08-T09, and what would falsify the design.

**Action 1:** in `_publish` in `src/knowledge_service/ingestion/synchronization.py`, in the `commit_page(...)` call, change:

```python
            offered=planned.keys(),
```

to:

```python
            offered=(),
```

Now a page commit stores no seen IDs, so the finish forgets what earlier pages offered.

**Action 2:** run:

```bash
uv run pytest tests/test_synchronization_gate_integration.py -q -m integration
```

You see `11 failed, 5 passed`. All nine kill points fail: the retry's finish tombstones Documents that earlier pages offered. Two web URL tests also fail, because their second pass tombstones the only item. Without the absolute expectations from Block 8, the kill tests would pass, because the clean pass makes the same mistake.

**Action 3:** undo the change and run the command again: `16 passed`.

**Check:** compare the count with your Step 1 prediction. Under `## Verification`, note the failed and passed counts.

### Step 8 — No coding in this step: kill a real worker mid-page

This step does the crash for real: a `make worker` process dies in the middle of page 2, and the relay and a new worker recover it. It uses a separate database, `knowledge_service_drill`, so the test database is untouched. It takes about seven minutes, most of it waiting for recovery's five silent minutes.

**Action 1:** in one terminal, from the repository root, set the drill's environment and make a folder of 150 files:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_DATABASE_URL="${KNOWLEDGE_SERVICE_TEST_DATABASE_URL%/*}/knowledge_service_drill"
export KNOWLEDGE_SERVICE_REDIS_URL="redis://127.0.0.1:6380/12"
export DRILL_DOCS="$(mktemp -d)"
for i in $(seq -f "%03g" 1 150); do printf "# Policy $i\n\nRule number $i.\n" > "$DRILL_DOCS/doc-$i.md"; done
```

**Action 2:** create and migrate the drill database, and register the folder as a public Source:

```bash
uv run python - "$KNOWLEDGE_SERVICE_DATABASE_URL" "$DRILL_DOCS" <<'EOF'
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url

sys.path.insert(0, "tests")
from database_fixtures import create_database, migrate

url = make_url(sys.argv[1])
create_database(url)
migrate(url)
engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
with engine.begin() as connection:
    connection.execute(
        sa.text(
            "insert into sources (source_id, kind, location, display_name,"
            " enabled, created_at, updated_at, default_public)"
            " values ('00000000-0000-0000-0000-00000000d016', 'local_directory',"
            " :location, 'Drill policies', true, now(), now(), true)"
        ),
        {"location": sys.argv[2]},
    )
engine.dispose()
print("drill database ready")
EOF
```

Save this script as `drill_show.py` next to the files' folder, not inside it, so you can look at the state at any moment:

```bash
cat > "$DRILL_DOCS/../drill_show.py" <<'EOF'
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url

engine = sa.create_engine(make_url(sys.argv[1]).set(drivername="postgresql+psycopg"))
with engine.connect() as connection:
    for label, query in (
        ("jobs", "select kind, status, attempt, last_error_code from jobs order by queued_at"),
        ("attempts", "select attempt, processed, changed, outcome from job_attempts order by attempt"),
        ("source", "select checkpoint, pass_mode from sources"),
        ("documents", "select availability, count(*) from documents group by 1"),
    ):
        print(label, connection.execute(sa.text(query)).all())
engine.dispose()
EOF
```

**Action 3:** inject the crash. In `_publish` in `src/knowledge_service/ingestion/synchronization.py`, add `import os` after `import logging`, and add these two lines right before `result = await reconcile_document(`:

```python
            if os.environ.get("DRILL_CRASH_BEFORE") == record.external_id:
                os._exit(1)
```

`os._exit(1)` ends the process at once, without running any `finally` block or telling Redis anything, as a kill would.

**Action 4:** queue the Job and start the worker with the crash armed:

```bash
make worker-relay
DRILL_CRASH_BEFORE=doc-120.md make worker
```

The relay prints `scheduled 1, published 1`. The worker boots and exits by itself; `make` prints `make: *** [worker] Error 1`. Look at the state:

```bash
uv run python "$DRILL_DOCS/../drill_show.py" "$KNOWLEDGE_SERVICE_DATABASE_URL"
```

```text
jobs [('synchronize_source', 'running', 0, None)]
attempts [(1, 100, 100, None)]
source [('doc-100.md', 'full')]
documents [('available', 119)]
```

Page 1 is committed, 19 records of page 2 are stored, and the Job still says `running`. The `attempt` column counts finished attempts, so it is still 0.

**Action 5:** remove the two injected lines and `import os`, so `git diff src/knowledge_service/ingestion/synchronization.py` shows nothing. Start a normal worker in a second terminal with the same three `export` lines, and leave it running:

```bash
make worker
```

**Action 6:** wait until five minutes have passed since the crash. Then, in the first terminal:

```bash
make worker-relay
uv run python "$DRILL_DOCS/../drill_show.py" "$KNOWLEDGE_SERVICE_DATABASE_URL"
```

The relay prints `stale 1`. The Job is `failed` with `worker_lost`, and attempt 1's outcome is `retry_scheduled`. Run `make worker-relay` again every few seconds until it prints `requeued 1, ... published 1`; the first retry waits about 15 seconds. The worker in the second terminal logs `(synchronize_source) ran: succeeded`. Look again:

```text
jobs [('synchronize_source', 'succeeded', 1, None)]
attempts [(1, 100, 100, 'retry_scheduled'), (2, 50, 31, 'succeeded')]
source [(None, None)]
documents [('available', 150)]
```

**Action 7:** see the leftover delivery, then stop the worker with Ctrl+C:

```bash
docker exec knowledge-service-redis-redis-1 redis-cli -n 12 keys '*__acks__*'
```

You may see one `__acks__` key of the dead worker: its message, never acknowledged. If Dramatiq hands it back while the worker runs, the worker logs `is succeeded; duplicate message` and does nothing.

**Action 8:** clean up:

```bash
docker exec knowledge-service-postgres psql -U test_user -d postgres -c "DROP DATABASE knowledge_service_drill WITH (FORCE)"
docker exec knowledge-service-redis-redis-1 redis-cli -n 12 flushdb
unset KNOWLEDGE_SERVICE_DATABASE_URL KNOWLEDGE_SERVICE_REDIS_URL
```

**Check:** attempt 2 reported `processed` 50 and `changed` 31, as Block 2 explains. Under `## Injected crash` in the evidence file, write the timeline with your own times, the relay and worker lines, and the two state dumps.

### Step 9 — No coding in this step: update the documents for the new field

This step records the field where readers of the architecture, the threat model, and operations look for it.

**Action 1:** in `docs/architecture.md`, add this paragraph right before `#### Synchronization Jobs as built in M08-T10`:

```markdown
A Source may report an item's `web_url` (since M08-T16): where a person
opens the item in the Source, such as a wiki page. Normalization keeps only
its safe form, without credentials, query, or fragment, and only when a
Citation would show it unchanged; otherwise the item has none. Every Chunk's
anchor then leads to it, as `<web_url>#offset=START-END` or `#page=N`, and
a Citation keeps that fragment. The link counts in the content fingerprint
only when present, so items without one keep their fingerprints; a moved
page rewrites its Chunks once.
```

**Action 2:** in `docs/threat-model.md`, add this row right before the row that starts "A reader the Source removed keeps reading":

```markdown
| A Source's `web_url` carries a secret or a misleading link into a Citation | Normalization keeps only the safe `http`/`https` form: no credentials, query, fragment, or invisible character, and only when the Citation would show it unchanged; anything else falls back to the external ID; a Citation keeps only an anchor's own `#offset=` or `#page=` fragment | A secret written into the URL's path itself is kept; spellings of one page that differ, such as an explicit default port, count as a move and rewrite its Chunks |
```

**Action 3:** in `docs/operations.md`, after the sentence that ends "and incremental passes between.", add:

```markdown
A worker killed mid-pass is recovered without help (drilled in M08-T16 by ending `make worker` with `os._exit` before one record): after five silent minutes `make worker-relay` fails the attempt as `worker_lost`, requeues it at its retry time, and the retry resumes from the last committed page; Redis may still hold the dead worker's message, and its redelivery runs nothing. An item whose `web_url` changes gets new Chunks, which need their embeddings published again.
```

**Check:** `make docs-check` passes.

### Step 10 — No coding in this step: gates, evidence, and progress

This step proves the whole repository still works, then records the gate.

**Action 1:** run every gate, one after another:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
git diff --check
```

**Action 2:** fill in the rest of the evidence file:

- `## New Source field`: the path of `web_url` (Block 4's table), why it is in the fingerprint only when present, and the cost of a move.
- `## Design`: the changed files.
- `## Verification`: the exact commands and counts from Steps 5 to 8 and Action 1.
- `## Review`: Blocks 5 to 8, each as "what broke, what we do now", and the known limits from Block 9.
- `## Decision`: why PostgreSQL decides and Redis only delivers, and what would falsify the design: a kill point after which the result differs from a clean pass, a redelivered message that runs a pass, or an item without a web URL whose fingerprint changes.
- `## Reflection`: your answers to the reflection questions below.

**Action 3:** in `docs/curriculum/PROGRESS.md`, add rows for M08-T11 to M08-T15 with the status `deferred` and the note "no Confluence access yet", then a `completed` row for M08-T16 with the evidence link. Set the next unblocked task to M09-T01. In the M08-T16 task file, set `status: completed`.

**Check:** all gates pass, and `make docs-check` passes after the record changes.

## Reflection questions

1. The worker died after storing 19 records of page 2. Which system could tell you that, Redis or PostgreSQL, and what exactly would you look at?
2. Suppose the claim did not check the Job's status and simply set it to `running`. What would the dead worker's leftover message do when Dramatiq hands it back after attempt 2 has succeeded?
3. A page commit moves the checkpoint only after every record of the page is stored. What would go wrong in the drill if it moved the checkpoint first, before storing the records?
4. Why does the fingerprint include `web_url` only when there is one? What would happen to all the Documents already stored if it always included it?
5. A Confluence page is renamed, and its address changes. What does the next pass do to its Chunks, and what happens to an old Answer that cited it?
6. Which test in this lesson would you run first after changing how pages are committed, and why that one?

## Completion checklist

- [ ] The evidence file has the invariant, owners, expected failing check, and your predictions, written before any change.
- [ ] `## Delivery versus business state` explains which system to ask and why.
- [ ] The architecture diagrams show the relay, Redis, the worker, and PostgreSQL as built, and a Job's statuses match `_ALLOWED`.
- [ ] `web_url` travels from `SourceMetadata` to the anchors and Citations; `tests/test_source_web_url.py` gives `21 passed`.
- [ ] `tests/test_synchronization_gate_integration.py` gives `16 passed`.
- [ ] The deliberate break gave `11 failed, 5 passed`, and the restored code `16 passed`.
- [ ] The drill: a real worker killed mid-page, recovered as `worker_lost`, attempt 2 with `processed` 50 and `changed` 31, 150 Documents; drill data removed.
- [ ] The architecture, threat-model, and operations documents describe the new field.
- [ ] All gates pass.
- [ ] M08-T11 to M08-T15 are recorded as deferred, M08-T16 as completed, and the next task is M09-T01.

When you have done a step, share your code, output, or errors, and I will review them.
