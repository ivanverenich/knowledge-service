# M07-T10 — Prioritize permission revocation evidence

## Invariant

On every page of a Synchronization Run, each Document that loses a reader
is narrowed to the readers its old and new access both name, committed with
a new Authorization Version, before any record of the page is parsed,
chunked, or reconciled; readers the Source adds wait for the record's own
reconciliation. Once a narrowing commits, every consistency point of a
request, search, the Evidence check before the model, the Citation check
before the decision, and History, gives the removed reader nothing. A file
offered twice on a page counts once, as last offered, and a pass that
removes no reader locks no Document.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_revocation_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_revocation_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- On a page with `notes.md` (new text, engineering) first and `roadmap.md` (engineering removed, carol added) second, what alice's question about the roadmap gets while the run is still working on `notes.md`, before and after this change: before, an answer from `roadmap.md`, because the run has not reached it and engineering can still read it; after, the empty-search abstention, because the first phase already narrowed `roadmap.md` to carol before `notes.md` was parsed.
- How many Documents a pass that removes no reader locks, and how many queries it adds per page: none; it adds one `load_readers` query per page, and `narrow_access` is never called.
- What a page offering `b.md` for carol and then for engineering does to carol, and what the summary counts: only the last offer counts, so carol is removed and never sees `b.md`, not even between two reconciliations; the summary counts one `ACCESS` change.
- Which tests fail if `synchronize_source` stops calling `_revoke_first`: 6, with 20 passing: the event-order test, the question asked while the run still parses, the broken file naming only unknown readers, both cases of the reader removed while the file is broken, and the restore after narrowing.

## Design

- **Two phases per page.** `synchronize_source` first collects the page's present records in a dict by external ID, so a file offered twice counts once, as last offered, and a later deletion on the page removes it. `_revoke_first` then reads every Document's current readers with one `load_readers` query (`persistence/access.py`), and calls `narrow_access` (M07-T06) only for files where `removes_readers` (`ingestion/reconciliation.py`) says the new access leaves someone out. Each narrowing is its own short transaction, raising the Authorization Version. Only then does phase 2 parse, chunk, and reconcile every planned file.
- **Additions wait.** A narrowing keeps only readers both old and new access name; readers the Source adds arrive with the file's own reconciliation and its new content.
- **Counting.** A quarantined file counts `ACCESS` if it was narrowed; `_counted` reports `ACCESS` or `CONTENT_AND_ACCESS` for a narrowed file that reconciliation alone would report as unchanged or content-only.
- **Consistency points.** No new one: search filters by grants, `still_visible` rereads Evidence before the model, `confirm_citations` rereads Citations before the decision (M07-T09), and `load_history` rereads every cited Chunk. Once a narrowing commits, each of them gives the removed reader nothing.

## Verification

- Expected failing check: before the change, `uv run pytest -m integration tests/test_revocation_integration.py -q` failed with `ERROR: file or directory not found: tests/test_revocation_integration.py`.
- After Steps 2 and 3, `uv run pyright` on the changed modules reported 0 errors; after Step 4, the existing synchronization and title tests passed unchanged (18).
- `uv run pytest -m integration tests/test_synchronization_integration.py tests/test_revocation_integration.py -q`: 26 passed, including the event-order test (`narrow b.md True` before `parse a.md`, alice already without `b.md` when reconciliation starts, carol with it after), three change-count cases, a broken file naming only `group:contractors`, a file offered twice, a pass that locks nothing, a file offered then deleted, a grant revoked while the model writes (Answer withdrawn, History withheld, search empty, a new question abstaining without the model), the control case, and a question asked while the run still parses an earlier file, which is answered before the revocation and abstains during it.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2784 passed (unchanged); `make test-integration` 636 passed (+11); `make docs-check` passed.
- Deliberate break: with `narrowed: set[str] = set()` instead of the `_revoke_first` call, 6 failed, 20 passed: the event-order test, the question asked while the run still parses, the broken file with unknown readers, both cases of a reader removed while the file is broken, and the restore after narrowing. Restored: 26 passed. The prediction was right.
- Known limits: a deleted or unlisted Document is tombstoned only at the end of the pass; revocation is first per page, not per pass; text streamed between a revocation and the Citation check is replaced, not unsent; runs of one Source are not locked against each other; a revocation applies only when a run reaches it (scheduled runs in M08-T06).

## Review

An independent adversarial review tried page orders, explicit deletions, exceptions in either phase, duplicate records, public and empty readers, unresolved principals, 300-file passes, markers in text and principals at DEBUG, and 13 mutations. Nothing widened access or leaked. It found these gaps, fixed with tests that failed before:

- The mid-Conversation test passed without this task's change, because one file was revoked before the model replied anyway. Now `test_a_question_asked_while_the_run_still_parses_already_fails_closed` asks during an earlier file's work; it fails on the old synchronization. Its control assertion (answered before the revocation) also caught an earlier draft in which the search never found the file, so the test passed for the wrong reason.
- `_counted` was never checked (four mutants survived). Now `test_a_narrowed_record_reports_the_access_change_it_made` covers three cases.
- Skipping reconciliation for narrowed files, and skipping the narrowing for a broken file with only unknown readers, survived. Now the event-order test requires carol to gain `b.md`, and `test_a_broken_file_naming_only_unknown_readers_is_narrowed_to_nobody` covers the second.
- Narrowing every file took a row lock each, 2 to 8 times slower on an unchanged pass of 300 files. Now `load_readers` reads a page once and only losing files are locked (`test_a_pass_that_removes_no_reader_locks_no_document`).
- A file offered twice on a page could give a removed reader access back until its second reconciliation. Now each file counts once per page (`test_a_record_offered_twice_in_a_page_counts_once_as_last_offered`, `test_a_record_offered_then_deleted_in_one_page_is_not_reconciled`).

Documented, not fixed: deletion not prioritized, per-page scope, the window before the Citation check, and unlocked concurrent runs.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Removals first, additions later.** A late addition is a delay; a late removal is a leak. Applying only the intersection first can never show anyone something they were not already allowed to see, and additions arrive together with the content they are meant for.
- **Per page.** A run already reads the Source a page at a time; a first phase over the whole pass would mean reading every page twice or holding them all in memory. Per page bounds the wait to one page's work, and the threat model says what remains.
- **Read once, lock only when needed.** Calling `narrow_access` for every file made every pass pay a row lock per file even when nothing was revoked. One read per page and an in-memory comparison keep the common case as cheap as before.
- **Left for later.** Prioritized tombstones, scheduled runs (M08-T06), existence leaks (M07-T11), and the full adversarial suite (M07-T15).

**Falsified assumption.** I assumed that a test revoking a grant during a Conversation proved that revocation goes first. The review shows that the same test passed on the old synchronization, because the consistency points from M07-T09 fail closed whenever the revocation lands before the model replies. So "fails closed during a Conversation" does not prove "revoked first"; only a question asked while the run is still busy with an earlier file shows the difference.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. A caller gets the guarantee that removed readers lose access within the first, fast phase of each page, without knowing about versions, fingerprints, or the order of work; it just calls `synchronize_source`.
2. The narrowing would happen only when the run reached the file, after every earlier file's parsing, so during a long run alice could still get answers from a file engineering had lost; `test_a_revocation_is_published_before_any_record_of_its_page_is_parsed` and `test_a_question_asked_while_the_run_still_parses_already_fails_closed` would fail.
3. Added readers must see the file's new content, not the old one. If a file was "Draft: layoffs planned" for the board and becomes "Announcement: reorganisation" for everyone, adding everyone in the first phase would let them read the old draft until the second phase replaces it.
4. Until the run finishes the first 49 pages' parsing and chunking, which can be minutes or hours. A per-pass first phase would read every page twice from the Source, or keep all of them in memory, before any content work.
5. Search returns nothing from `payroll.md`; an answer stream drops it at `still_visible` before the model, or withdraws an Answer citing it at the Citation check; History withholds earlier Answers that cited it. Text already streamed to him before the narrowing committed cannot be recalled.
