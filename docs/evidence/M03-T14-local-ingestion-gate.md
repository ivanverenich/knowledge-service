# M03-T14 — Local-ingestion gate evidence

Completed: 2026-10-22

## Invariant

M03 turns a folder into searchable state with these guarantees. A connector
only lists and reads; it never decodes or interprets content. A file that
cannot be parsed is quarantined, not deleted. Each Document keeps its ID and
provenance for life. Re-reading an unchanged item writes nothing, and only a
change to the text produces new Chunks. The new title must keep all of that:
the same title writes nothing, and a new title changes only the row.

## Prediction

Written before running the diagnosis or adding the title.

Malformed PDFs, each passed through `normalize`:

- **A valid PDF.** A `NormalizedDocument` with one paragraph, "Vacation
  policy".
- **A PDF cut in half.** `Quarantined` with reason `corrupt`: pypdf cannot
  find the end of the file, `parse_pdf` raises `CorruptDocument`, and
  `normalize` maps it to `QuarantineReason.CORRUPT`.
- **A file that starts like a PDF but is not one.** Also `corrupt`. It passes
  the header check but has no real objects, so pypdf fails while reading it.
- **A file with no PDF header.** Also `corrupt`. The parser does not tell a
  missing header apart from a broken body, so the operator sees the same
  reason and the same fixed detail for all three.
- **A PDF whose only page has no text.** `Quarantined` with reason
  `no_text_layer`. The file is valid, but there is nothing to extract, and
  OCR is not supported.

In a sync, each quarantined file still counts as seen, so an older good
version of it stays searchable and is not tombstoned.

The title:

- **After syncing `handbook.md`.** The stored title is `handbook`:
  `DirectorySource` uses the file name without its extension, and
  `normalize` keeps it as it is.
- **A second sync of the same folder.** `Change.UNCHANGED`. Nothing is
  written: the row, its `updated_at`, and every Chunk ID stay the same.
- **A rename of the title, with the same text and readers.** `Change.TITLE`.
  The row stores the new title and a later `updated_at`. The Document ID, the
  external ID, the source version, both versions, and every Chunk ID stay
  the same.
- **An existing row when the migration runs.** It gets the placeholder title
  `Untitled`, which loads as a valid Document. The next sync replaces it with
  the real title and reports `Change.TITLE`.

## Connector versus parser

**1. What the connector promises, and what it refuses to do.**
`DirectorySource.fetch_page` returns one `SourcePage`: up to `page_size`
`SourceRecord`s and the checkpoint to resume after them. The files are
listed by `_list_files` in sorted order of their relative POSIX path, and the
checkpoint is the last path delivered, so the next page starts after it. For
each file, `_read` delivers:

- the raw bytes, unchanged;
- a `version` (the SHA-256 of those bytes);
- a `media_type` chosen from the file extension;
- the `SourceAccess` the Source was configured with;
- `SourceMetadata` (the file name without extension as the title, the
  modification time, and the size).

It never follows a symbolic link, and it never decodes, cleans, or
interprets content. It delivers bytes rather than text because deciding
whether bytes are valid UTF-8, a PDF, or garbage is a per-file judgement. If
the connector decoded, one undecodable file would either fail the whole page
or be silently dropped, and a dropped file looks deleted. Delivering bytes
moves that judgement to `normalize`, where a bad file becomes one
`Quarantined` outcome. The connector also stays the same for every format:
adding a parser never touches it.

**2. What a parser promises.** A parser turns one document into a
`ParsedDocument`: its reading text plus `StructuralBlock`s (headings,
paragraphs, list items, tables, code), each with offsets, and pages for a PDF.
`parse_pdf` takes bytes. `parse_text`, `parse_markdown`, and `parse_html`
take a string; `normalize` wraps them in `_decoded`, which decodes UTF-8 and
raises `_Undecodable` for invalid bytes or a NUL character. A parser knows
nothing about folders, checkpoints, versions, or access, because none of that
changes how a document's structure is read. The same `parse_html` will read a
Confluence page in M08 exactly as it reads a local file. A parser is a pure
function from content to blocks, so it can be tested with nothing but bytes.

**3. Which errors belong to each side.** The connector raises
`SourceUnavailable` when the Source itself cannot be read: the root folder is
missing, a folder cannot be listed, or a file cannot be read. That error
stops the whole run, on purpose. If an unreadable folder were skipped
instead, its files would be missing from `seen`, and `tombstone_missing`
would make them disappear from answers. M03-T02's review fixed exactly that.

A parser raises a subclass of `DocumentParseError` when one document's
content is bad: `CorruptDocument`, `EncryptedDocument`, `NoTextLayer`, or
`DocumentTooLarge`. `normalize` turns each one into a `Quarantined` outcome
with a `QuarantineReason` (`_reason` does the mapping), and
`synchronize_source` counts the file as seen. One bad file therefore never
stops the run and never deletes the file's last good version. The difference
matters for deletions: "the Source could not tell us" must never look like
"the Source says it is gone".

**4. What changes for Confluence in M08.** A new connector, a
`SourcePort` adapter, that pages the Confluence API, maps page IDs to external
IDs, page versions to `version`, and Confluence permissions to
`SourceAccess`, and translates API failures into `SourceUnavailable` or
`SourceUnauthorized`. The `SourceRecord` and `SourcePage` contract stays the
same. So do the parsers, which receive the page's HTML as bytes with media
type `text/html`, and `normalize`, chunking, `reconcile_document`,
`tombstone_missing`, and `synchronize_source`. Mapping Confluence principals
to users and groups is identity work for M07, behind `source_readers`.

## Synchronization

`docs/architecture.md` now has a second diagram, "Synchronization as built in
M03", after the target-design one. Every arrow was traced to
`synchronize_source` and the functions it calls. Tracing showed three places
where the first draft of the diagram was wrong, and the version in the
architecture file fixes them:

- a `Quarantined` record never reaches `reconcile_document`; it only stays in
  `seen`;
- a record the Source reports deleted is skipped and removed from `seen`, so
  the last report of an item in a pass wins;
- `requeue_unfinished_purges` also writes to the database.

**How the two diagrams differ.** The target diagram is a scheduled, durable
process: a scheduler queues work, a worker receives it at least once, and it
saves the checkpoint and the run's outcome so a run can resume. The as-built
one is a single in-process call with no queue, no Synchronization Run record,
and no saved checkpoint. A pass that fails starts again from the first page.
In both, the Source is paged, each item is compared by version before
anything is written, and the current state is published in one transaction
per Document. The as-built diagram also shows two things the target leaves
out: quarantine, and tombstones with their delayed purge.

**What comes next.** M04 adds the embedding step inside reconciliation's
flow: embed only the changed Chunks, before the publishing transaction, so a
slow provider never holds row locks. M08 turns the one call into the target
process: Synchronization Runs, the "one running run per Source" rule, saved
checkpoints, scheduling and at-least-once delivery through the queue, and a
worker that runs the purge Jobs.

## Malformed PDF diagnosis

The diagnosis command from the lesson guide builds five PDFs in memory with
`tests/pdf_builder.py` and passes each through `normalize`. It wrote nothing
to disk. Output on 2026-10-22:

```text
EOF marker not found
invalid pdf header: b'hello'
EOF marker not found
valid            NormalizedDocument
truncated        Quarantined        corrupt        pdf could not be read
garbage header   Quarantined        corrupt        pdf could not be read
no header        Quarantined        corrupt        pdf could not be read
image-only page  Quarantined        no_text_layer  pdf has no extractable text; OCR is not supported
```

Every prediction held. The first three lines are not ours: they are warnings
from the logger `pypdf._reader`, written to standard error.

**1. Which layer decided.** For the truncated PDF:

1. `parse_pdf` calls `_open_pages`, which opens the file with pypdf's
   `PdfReader` inside `_pdf_errors()`.
2. pypdf raises `PdfStreamError` ("Stream has ended unexpectedly").
3. `_pdf_errors` turns any library exception that is not already a
   `DocumentParseError` into `CorruptDocument("pdf could not be read")`, and
   keeps the original on `__cause__`. Calling `parse_pdf` directly confirmed
   `CorruptDocument` with the cause `PdfStreamError`.
4. `normalize` catches the `DocumentParseError`, and `_reason` maps
   `CorruptDocument` to `QuarantineReason.CORRUPT`. `_quarantine` builds a
   `Quarantined` outcome with the error's message as the detail.
5. `synchronize_source` has already added the file to `seen`, records the
   quarantine in the summary, and does not call `reconcile_document`.

So an older, good version of the same file stays stored and searchable: it is
neither replaced nor tombstoned.
`test_a_file_that_stops_parsing_keeps_its_last_good_version` proves this for
a file that stops decoding.

The parser decides *that* the content is bad, normalization decides *what
that means* (a quarantine reason), and synchronization decides *what happens
to stored state* (nothing).

**2. What an operator sees.** Only the reason `corrupt` and the fixed detail
`pdf could not be read`. A truncated upload, a file that merely starts with
`%PDF-`, and a text file renamed to `.pdf` all look the same, because
`_pdf_errors` turns every pypdf failure into one message. That is acceptable
now. The detail is safe to store and show, and the remedy for an operator is
the same in all three cases: replace the file. In M10, a short error category
taken from the exception class on `__cause__` (for example `stream_ended` or
`bad_header`) would help sort failures without exposing content. It belongs in
telemetry counters, not in the stored detail.

**3. What leaks.** pypdf's warning `invalid pdf header: b'hello'` contains the
first bytes of the file's content, and it is emitted through Python's
`logging` as `pypdf._reader` at `WARNING`. Today it only reaches standard
error. Once the worker ships logs, it would copy document content into
telemetry, which ADR 0010 ("Redact production telemetry by default") forbids:
logs carry identifiers, counts, timings, and outcomes, never retrieved text.
**Follow-up for M10, not fixed here:** the worker should raise the `pypdf`
logger's level, or drop its records through a filter, so library warnings
cannot carry content into telemetry. Our own log record would then say only
"quarantined, reason=corrupt" with the Document's identifiers.

**4. Why the image-only page is different.** It is a valid PDF: pypdf opens it
and reads its one page without error. Extraction simply finds no text, so
`parse_pdf` ends with no blocks and raises `NoTextLayer`. `normalize` maps
that to `no_text_layer`, with the detail "pdf has no extractable text; OCR is
not supported". This is not damage; the file needs OCR, which this project
does not do. An operator can act on it differently, by supplying a text
version, so it deserves its own reason.

## Design

The metadata field is the Document's **title**. It already travelled from
`SourceMetadata.title` (the file name without its extension in
`DirectorySource`) to `NormalizedDocument.title` (cleaned, NFC, cut to 200
characters by `normalize`), but `reconcile_document` dropped it. M05-T01's
title-weighted full-text search and citations both need it.

Changed files:

- `src/knowledge_service/documents.py`: `MAX_TITLE_CHARACTERS` (200) moves
  here. `Document.title` must be non-blank and at most 200 characters, or
  `InvalidDocument`. `register` and `reconcile` take `title`. A changed title
  counts as a change but raises neither version.
- `src/knowledge_service/normalization.py`: imports `MAX_TITLE_CHARACTERS`
  from `documents.py`, so it never produces a title the Document refuses.
- `src/knowledge_service/persistence.py`: a `title` column (`Text`,
  `NOT NULL`), written by `as_row`, read by `as_document`, and updated by
  `upsert_document`'s `ON CONFLICT ... DO UPDATE SET`.
- New `migrations/versions/d2e3f4a5b6c7_document_title.py`: adds the column as
  nullable, fills existing rows with `Untitled`, then sets `NOT NULL`. The
  downgrade drops the column.
- `src/knowledge_service/reconciliation.py`: passes `normalized.title` to
  `Document.register` and `Document.reconcile`. The new `Change.TITLE` is
  reported when only the title changed; `Change.SOURCE_VERSION` when the
  source version changed.
- `docs/architecture.md`: the as-built synchronization diagram (Step 3).
- Tests: `title="Handbook"` added to 19 calls in nine existing test files;
  `tests/test_indexes_integration.py`'s raw-SQL seed now gives a title; four
  new cases in `tests/test_documents.py`; new
  `tests/test_document_title_integration.py` with 3 tests.

**How the title keeps provenance and idempotency intact,** which is this
task's acceptance criterion:

| Guarantee | Proved by |
|---|---|
| Re-observing the same item writes nothing: `UNCHANGED`, an identical row (same `updated_at`), the same Chunks | `test_a_synchronized_file_keeps_its_title_and_a_second_pass_writes_nothing` |
| A new title changes only the row: `TITLE`, same Document ID, same provenance (external ID and source version), same content and authorization versions, same Chunk IDs | `test_a_new_title_updates_only_the_row`, `test_a_title_only_change_keeps_identity_and_both_versions` |
| A title the Document cannot hold is refused with a typed error | `test_a_blank_or_overlong_title_is_refused` (empty, blank, 201 characters) |
| Rows stored before the change still load | `test_documents_stored_before_titles_get_a_valid_placeholder` |

Decisions:

- **Neither version moves for a title.** The content version means "the text
  changed, so the Chunks must be rebuilt"; the authorization version means
  "the readers changed". A rename is neither. Raising the content version
  would re-chunk, and later re-embed, a whole document for a new name.
- **One limit, owned by the Document.** The Document enforces the 200
  characters, and normalization uses the same constant, so the two cannot
  drift apart.
- **A placeholder for old rows.** A non-null column needs a value for every
  existing row. A value computed in SQL from the external ID can be blank by
  Python's rules (PostgreSQL and Python disagree on what whitespace is), and
  one unloadable row would fail every sync of its Source. `Untitled` is always
  valid, and the next sync replaces it.

**Two bugs found before the guide was written,** both caught only by
real-database tests:

- **A rename that was not stored.** The first version passed every domain
  test, but `upsert_document` updates only the columns listed in its `set_`,
  and `title` was not there, so the row kept the old title while
  `reconcile_document` reported `Change.TITLE`.
- **A backfill that could make a Source unusable.** An adversarial review
  showed that `left(external_id, 200)` gives a blank title for an external ID
  that starts with 200 spaces, ideographic spaces (U+3000), or `\x1f`
  characters. `DirectorySource` can produce such an ID from a file name.
  Loading that row raised `InvalidDocument`, and because
  `load_available_documents` loads every row of the Source, every later
  sync failed. The placeholder fixes it, and the migration test loads rows
  with all three ID shapes.

The review also fuzzed 30,000 hostile titles and external IDs through
`normalize`. Every result was deterministic, at most 200 characters, NFC, and
accepted by `Document`, and stored titles round-tripped unchanged. It checked
title changes combined with content, access, restore, and source-version
changes; each returned the expected `Change` and stored the title.

When the learner's code was finished, five deliberate breaks were run against
it, one at a time, and each was caught: removing `title` from `upsert_document`
failed 1 test, removing `title_changed` from the change check failed 5,
raising the content version on a title change failed 2, the old backfill
failed 1, and leaving `title=title` out of `reconcile`'s `replace` failed 5.
Each file was restored afterwards.

**Notes:**

- **The first sync after the migration reports `Change.TITLE` for nearly
  every document,** as the placeholder is replaced. It rewrites rows only.
- **A title change combined with another change is not named.** A rename
  with new text returns `CONTENT`; the title is still stored.
- **The 200-character cut counts code points,** so a title cut inside an
  accented letter keeps the base letter. The result is the same every time,
  so idempotency holds.
- **Two constants share a name:** `chunking.MAX_TITLE_CHARACTERS` (100, for
  heading paths) and `documents.MAX_TITLE_CHARACTERS` (200).
- **Follow-up for M10:** pypdf logs file bytes (see the diagnosis above).

The integration tests ran against the local disposable PostgreSQL 17 test
server. Its password was read from the container's settings for each run and
never written to any file. The fixtures hold only synthetic text and
identifiers, and the public types name no provider.

## Verification

Final run on 2026-10-22 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project` and
`KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set to the local test server:

| Command | Result |
|---|---|
| `uv run pytest tests/test_documents.py tests/test_document_title_integration.py -q` | 14 passed |
| `make format-check lint typecheck` | 110 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 533 passed, 53 deselected |
| `make test-integration` | 51 passed, 535 deselected |
| `make check` (with the database) | 584 passed, 2 deselected; total coverage 93% |
| `make check` (without the database) | 534 passed, 50 skipped, 2 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held. Along the way:

- `reconcile`'s final `replace(...)` was first written without `title=title`,
  so a rename would have been detected but never applied. The 19 test calls
  and the Step 8 tests had not been added yet; Pyright listed 66 errors in
  nine test files. Both were completed before the run above.
- `ruff check --fix` sorted the `normalization.py` import and removed extra
  blank lines in the migration; `make format` changed nothing else.

## Reflection

1. **Why a title-only change raises neither version.** Each version promises
   something. A higher content version means the text changed and the Chunks
   must be rebuilt, and a higher authorization version means the readers
   changed and the grants must be replaced. A new title changes neither the
   text nor the readers. If it raised the content version, every rename
   would re-chunk the document, and once Chunks have embeddings, it would pay
   for an embedding call per chunk just to change a name.
2. **Why the rename bug passed every unit test.** The unit tests checked the
   `Document` value, and that value was correct. The bug was in the SQL that
   writes it: `upsert_document` lists the columns it updates, and the new one
   was missing. Only a test that writes through the real database and reads
   the row back could see it. An idempotency or update rule must be tested at
   the boundary where the state is stored, not only on the object that
   describes it.
3. **Why the migration writes `Untitled`.** A value derived from the external
   ID looked more useful, but it was computed by SQL and checked by Python,
   and the two disagree on whitespace, so one odd file name could produce a
   row that never loads and stop every sync of its Source. A fixed placeholder
   is always valid, and the next sync replaces it. Leaving the column
   nullable for good would push the problem to every reader: search,
   citations, and the API would all need a "no title" branch forever.
4. **What the pipeline hides from the caller of `synchronize_source`.** The
   caller still passes a Source, its ID, and a time. It never chooses a title,
   cleans or cuts it, decides whether a rename is a change, keeps the versions
   steady, or writes the column. The title arrives in the database, and the
   summary says `TITLE` when it is the only thing that changed.
5. **How `Change.TITLE` can help M05-T01.** If the lexical search vectors are
   built from the title as well as the text, a rename must update them even
   though the Chunks did not change. `Change.TITLE` tells the indexing step
   exactly that: rebuild the title part of the lexical index for this
   document, but do not re-chunk or re-embed.
6. **A test whose removal would let a real bug through.**
   `test_a_new_title_updates_only_the_row`. Without it, the `upsert_document`
   bug would ship: every rename would report `TITLE` and store nothing, and
   search and citations would show the old name for good.

The answers in "Connector versus parser", "Synchronization", "Malformed PDF
diagnosis", and this reflection were drafted by Claude at the learner's request
and have not yet been rewritten in the learner's own words.
