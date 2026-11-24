# M08-T16 — Jobs-and-Confluence gate evidence

M08-T11 to M08-T15 (Confluence) are deferred: there is no Confluence access
yet. This gate was passed without Confluence, on the local folder Source and
the fake test Source.

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
Observed on 2026-11-24 at `df93a49`: `no tests ran in 0.11s`.

## Predictions

Claude wrote these while preparing the lesson, after the prototype's drill;
they are not blind predictions by the learner.

- The worker is killed before `doc-120.md` of 150 files, in pages of 100.
  What do attempt 2's `processed` and `changed` say: 50 and 31; it rereads
  page 2, and the 19 records stored before the kill compare equal.
- Right after the kill, what does the Job's row say, and where is its
  message: `running`, attempt 1 with `processed` 100; the message sits
  unacknowledged in the dead worker's `__acks__` set in Redis.
- A page's web URL moves and its text stays. Which of the Document's two
  versions change: only the content version (1 to 2); the authorization
  version stays 1.
- How many tests in the gate suite fail if a page commit stores no seen
  IDs: 11 (observed, see Verification).

## Delivery versus business state

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

| | Redis (delivery) | PostgreSQL (business state) |
|---|---|---|
| What it holds | a message with a Job's ID, and whether a worker acknowledged it | the Job's status and attempts, the Source's checkpoint, pass, and seen IDs, the Documents, Chunks, and grants |
| What it can answer | "has some worker said it is done with this message?" | "is this Job queued, running, or finished, and how far did its pass get?" |
| When a worker dies mid-page | the message stays unacknowledged in that worker's `__acks__` set | the Job stays `running`; committed pages and records stay; the open record's transaction is gone |
| Who reads it before running anything | nobody decides from it | every handler: `run_stored_job` and the claim run only a `queued` Job |

A redelivered message runs nothing because the claim reads the Job's status
in PostgreSQL first, under its row lock, and only `queued` may become
`running`. The leftover message is harmless even after the Job succeeded:
when Dramatiq hands it back, the worker finds `succeeded`, logs a duplicate
message, and acknowledges it.

## Sync diagrams

The old "Synchronization" diagram in `docs/architecture.md` was the course's
first target. It was wrong in three ways: the queue carried "source +
checkpoint", while a message carries only the Job's ID and the checkpoint
lives in PostgreSQL; it had no relay, recovery, lease, claim, or progress;
and it embedded Chunks inside the pass, while `publish_document_embeddings`
runs separately. It is replaced by the pass as built: relay, PostgreSQL,
Redis, worker, and Source, with each arrow saying what crosses. A new state
diagram shows a Job's statuses; every arrow is a move `_ALLOWED` in
`src/knowledge_service/jobs.py` permits, and every permitted move has an
arrow (failed to queued by the relay's retry or an operator's reopen;
queued or failed to cancelled by an operator). The paragraph "The diagram
above is the target" is replaced.

## Injected crash

DRILL_PLACEHOLDER

## New Source field

`SourceMetadata.web_url`: where a person opens the item in its Source, such
as a wiki page. Its path:

| Stage | File | What happens |
|---|---|---|
| Source reports it | `ingestion/sources.py` | refused with `InvalidSource` if it is not a `str`, blank, or over `MAX_WEB_URL_CHARACTERS` (2048) |
| Normalization | `ingestion/normalization.py` | `_shown_url` keeps `display_url`'s safe form (`http`/`https`, no credentials, query, or fragment) only if `display_text` shows it unchanged; else `None` |
| Fingerprint | `ingestion/fingerprints.py` | `content_fingerprint(parsed, web_url=...)` adds the key only when there is a URL |
| Chunks | `ingestion/reconciliation.py` | `build_chunks` passes it as `source_url`, so anchors read `<url>#offset=START-END` or `#page=N` |
| Citation | `ingestion/provenance.py` | `display_text` keeps an anchor's own `#offset=`/`#page=` fragment and cuts any other |

The URL is in the content fingerprint because Chunks store their anchors and
are rewritten only when that fingerprint changes. It is there only when
present, so every Document stored before keeps its fingerprint byte for byte
(`test_the_canonical_form_does_not_drift` fails if the key is always added):
a Source that does not send the field sees no change. A moved page costs one
rewrite of its Chunks, which then need their embeddings published again; its
content version goes to 2, and old Answers citing it are withdrawn by the
Citation check.

## Design

Changed files:

- `src/knowledge_service/ingestion/sources.py`: `MAX_WEB_URL_CHARACTERS`,
  `SourceMetadata.web_url` and its three checks.
- `src/knowledge_service/ingestion/normalization.py`:
  `NormalizedDocument.web_url` (default `None`), `_shown_url`.
- `src/knowledge_service/ingestion/fingerprints.py`: `content_fingerprint`
  takes `web_url`.
- `src/knowledge_service/ingestion/reconciliation.py`: the fingerprint and
  `build_chunks` use it.
- `src/knowledge_service/ingestion/provenance.py`: `_ANCHOR_FRAGMENT`;
  `_shown_url` keeps an anchor's own fragment.
- `tests/test_source_web_url.py` (new, 21 tests).
- `tests/test_synchronization_gate_integration.py` (new, 16 tests): a worker
  killed before each of nine steps of a pass (five records, three page
  commits, the finish), with redelivery, recovery, and retry, compared with
  an uninterrupted pass of an identical Source and with the expected result;
  seven `web_url` tests through the Job handler.
- `docs/architecture.md` (the pass as built, the Job state diagram, the
  `web_url` paragraph), `docs/threat-model.md` (one row),
  `docs/operations.md` (killed worker recovery, a moved page's cost).

Replay and cancellation: a killed or cancelled attempt records nothing; a
redelivered message runs nothing while the Job runs or after it finished;
recovery decides after five silent minutes. External calls stay bounded as
in M08-T10 (page size, lease per page, 30-minute message limit); this change
adds no network, model, or paid call.

## Verification

Narrow checks (2026-11-24): `uv run pytest tests/test_source_web_url.py -q`:
21 passed.
`uv run pytest tests/test_synchronization_gate_integration.py -q -m integration`:
16 passed.

Deliberate break (guide Step 7): with `offered=planned.keys(),` changed to
`offered=(),` in `_publish`, the gate suite gave `11 failed, 5 passed`: all
nine kill points, plus `test_the_same_web_url_again_changes_nothing` and
`test_a_new_token_in_the_same_link_changes_nothing`. Restored: 16 passed.
While preparing the lesson, other mutations each failed at least one test:
the URL left out of the fingerprint (2), the URL not passed to the anchors
(4), the raw URL kept (7), the anchor fragment cut (2), stored-equals-shown
removed (1), the type check removed (1), never resuming (6), and both claim
checks accepting a running Job (5). Adding the key to every fingerprint
failed `test_the_canonical_form_does_not_drift`.

Gates (2026-11-24), one after another on one test database:
`uv run ruff format --check .` (359 files already formatted),
`uv run ruff check .` (all checks passed), `uv run pyright` (0 errors),
`make docs-check` (exit 0), `make test-unit` (3299 passed),
`make test-integration` (909 passed), `git diff --check` (clean),
`uv run pre-commit run --all-files` (all passed).

Security and privacy: a URL's credentials, query, fragment, and invisible
characters never reach an anchor; a token marker and a password marker are
absent from stored anchors (`test_a_secret_in_a_web_url_never_reaches_a_chunk`).
No new log line. The drill used its own database and Redis database, both
removed afterwards.

## Review

An adversarial review of the prototype found these, each fixed with a test
that fails without the fix:

1. A Citation cut off the place in the page: `display_text` removed
   everything after `#`, so every Chunk of a page with a web URL got the same
   link. Now `_shown_url` keeps an anchor's own `#offset=`/`#page=` fragment
   and still cuts any other.
2. What was stored was not what was shown: `https://h/x:SECRET@y` was kept,
   but a Citation shows `https://h/y`. Now normalization keeps a URL only if
   `display_text` leaves it unchanged.
3. A `bytes` web URL passed the checks and crashed normalization with a
   `TypeError`. Now `SourceMetadata` refuses anything but `str` with
   `InvalidSource`.
4. The crash test compared with a clean pass only, so a bug both passes share
   went unseen (with no seen IDs stored, both tombstoned the same Documents).
   Now it also checks the expected availability, content versions, and
   readers, and compares chunk text, titles, fingerprints, readers, every
   audit event per Document, the Source's checkpoint and
   `synchronization.finished` events, purge Jobs per Source, and attempts;
   kill points were added at page commits and the finish, and one item's
   readers narrow in the second pass.

REVIEW_PLACEHOLDER

Known limits:

- A secret written into the URL's path itself is kept; paths are kept on
  purpose.
- Two spellings of one page, such as an explicit `:443`, count as a move and
  rewrite its Chunks once.
- Nothing in the worker publishes embeddings yet, so a moved page's new
  Chunks wait for that.
- The test kills with a cancellation, not a process kill; the drill covers a
  real kill.
- Dramatiq returns a dead worker's message only during occasional
  maintenance; the drill did not wait for it.
- M08-T11 to M08-T15 are deferred; only the test Source sends a web URL.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

PostgreSQL decides and Redis only delivers, because a message cannot know
what its work did: a worker can die after storing part of a page and before
acknowledging. Every handler therefore claims the Job in PostgreSQL first,
and recovery decides from the attempt's last report, not from Redis. Records
are idempotent and the checkpoint moves only after a whole page, so a retry
may replay part of a page safely. The new field joins the content
fingerprint only when present, so it changes nothing for Sources that do not
send it.

What would falsify the design: a kill point after which the result differs
from an uninterrupted pass, a redelivered message that runs a pass, or an
item without a web URL whose fingerprint changes.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in
their own words.

- Which failure can occur after the caller believes this work succeeded?
  The dead worker's leftover message comes back later; it finds the Job
  succeeded and runs nothing, so no harm, but a dashboard reading Redis
  alone would still show it in flight. A page whose URL changed is stored
  with new Chunks that have no embeddings until they are published.
- How does the design behave under replay, cancellation, or authorization
  change? Replay runs nothing while the Job runs or after it ends.
  Cancellation or a kill records nothing; recovery fails the attempt after
  five silent minutes and the retry resumes. A narrowed reader (pay.txt in
  the test) loses access on the page that reports it, also across a kill.
- Which metric or contract test protects future refactoring?
  `test_a_worker_killed_mid_pass_ends_as_if_nothing_happened` (nine kill
  points, compared with a clean pass and the expected result), and
  `test_the_canonical_form_does_not_drift` for stored fingerprints; the
  attempt's `processed` and `changed` show how much a retry replayed.
