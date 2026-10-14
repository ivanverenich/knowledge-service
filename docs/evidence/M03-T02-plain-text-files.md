# M03-T02 — Plain-text directory Source evidence

Completed: 2026-10-14

## Invariant

The same folder contents always produce the same Source Records, in the same
order, with the same `external_id`s. An `external_id` is the file's path
relative to the root, written with `/`, so it survives a re-read and does not
depend on the machine or the operating system's listing order. No record ever
holds bytes from outside the root: symbolic links are never followed, and a
checkpoint is only compared, never opened as a path.

This refines the M03-T01 invariant about deletion. A Source that keeps a change
feed reports a deletion as an explicit `DELETED` record. A local directory keeps
no history, so `DirectorySource` never emits one; a deleted file is simply
absent from the next complete read, and M03-T13 turns that absence into a
tombstone.

## Prediction

Written before the adapter and its tests exist. This is what I expect a caller
reading through `SourcePort` to see in each case.

- **Nested folders.** A file in a subfolder comes back with its relative path,
  such as `a/z.txt`. Records are sorted by that path, so `a.txt` comes before
  `a/z.txt` because `.` sorts before `/`. Files that are not `.txt` are not
  returned.
- **Modified file.** The record keeps the same `external_id`, but its `version`
  changes because the version is a hash of the content. Touching a file without
  changing its bytes leaves the version unchanged.
- **Deleted file.** No `DELETED` record appears. The file is missing from the
  next complete read, and the other files come back unchanged.
- **File that is not valid UTF-8.** Reading raises `UndecodableSourceContent`,
  a `SourceError`, whose `external_id` names the file. The error message holds
  the path only, never the file's bytes.
- **Symlink pointing outside the root.** It is skipped, whether it points to a
  file or a folder. Only files that physically live under the root come back.
- **Checkpoint of `../secret.txt`.** It is treated as a sort position, not a
  path. It sorts before every normal file name, so the caller gets the files
  under the root and nothing from the parent folder.

## Design

Changed files:

- `src/knowledge_service/sources.py`: added `UndecodableSourceContent`, a
  `SourceError` that names the file and never its bytes, and `SourceMetadata`
  (title, UTC modified time, non-negative size). `SourceRecord` gained an
  optional `metadata` field that a deleted record must not carry. Existing
  callers such as `FakeSource` are unchanged.
- `src/knowledge_service/directory_source.py`: new `DirectorySource`, the
  first real `SourcePort` adapter.
- `tests/test_directory_source.py`: 13 tests on real files in `tmp_path`.

How `DirectorySource` meets each requirement:

| Requirement | Decision |
|---|---|
| Deterministic ordering | Every `.txt` path under the root is sorted by Python string order, so the order does not depend on the operating system's listing. |
| Stable identity | `external_id` is `path.relative_to(root).as_posix()`, the same on every machine and unchanged if the whole root moves. |
| Pagination | The checkpoint is the last delivered `external_id`. The next page holds the paths that sort after it, so adding or deleting a file between pages never shifts the position. |
| Version | SHA-256 of the decoded content: editing a file changes it, touching a file does not. |
| Metadata | `SourceMetadata(title=<stem, or external_id when the stem is blank>, modified_at=<mtime in UTC>, size_bytes=<raw length>)`. |
| Safe decoding | Strict `utf-8-sig`: the byte-order mark is removed and invalid bytes raise `UndecodableSourceContent`. A NUL character is rejected too, because PostgreSQL `text` cannot store it. |
| Symlink policy | Never followed. `Path.walk()` does not descend into linked folders, and symlinked files are skipped. As a backup check, `_read` also refuses any path that resolves outside the root; only a race can reach it. |
| Deletion | No `DELETED` record: a removed file is absent from the next complete read, for M03-T13 to reconcile. |
| Failures | A missing root or a folder that cannot be listed raises `SourceUnavailable`, and other OS read errors are wrapped in it. A file deleted between listing and reading is skipped. `page_size < 1` raises `InvalidSource`. |

Blocking file work runs in `asyncio.to_thread`, so `fetch_page` never stalls
the event loop. The adapter keeps no state between calls: every page lists the
tree again, which keeps each page current and lets any process resume from a
stored checkpoint.

A read either completes or raises. An unreadable folder raises instead of being
skipped, because skipping it would make its files look deleted. An undecodable
file also stops the whole read, on purpose. M03-T13 must therefore reconcile
deletions only after a complete, successful read, and M03-T06 may later prefer
to quarantine a single bad record instead of stopping the run.

Access is passed in as configuration because a local folder
has no ACL of its own. The public contract names no provider or storage
detail, and the test fixtures contain only synthetic text.

## Verification

Final run on 2026-10-14, after the review fixes, with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_directory_source.py -q` | 13 passed |
| `make format-check lint typecheck` | 78 files already formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 311 passed, 20 deselected |
| `make check` | 312 passed, 17 skipped, 2 deselected; `directory_source.py` 89% covered |
| `make docs-check` | exit 0 |
| `git diff --check` | silent |
| `pre-commit run` on the changed files | all hooks passed |

Acceptance cases and the tests that prove them:

- **Nested paths:** `test_nested_files_come_back_sorted_by_relative_path`.
- **Encoding failure:** `test_invalid_utf8_raises_a_typed_error_naming_the_file`. The success side is `test_utf8_text_is_kept_and_a_byte_order_mark_removed`, with Ukrainian text.
- **Modification:** `test_a_modified_file_keeps_its_identity_and_changes_version`.
- **Deletion:** `test_a_deleted_file_is_absent_from_the_next_read`.
- **Traversal attempts:** `test_symbolic_links_are_never_followed` (a file link and a folder link) and `test_a_checkpoint_is_never_opened_as_a_path`.
- **Also covered:** metadata, resume after a checkpoint, a missing root, and an invalid page size.
- **Unreadable folder:** `test_an_unreadable_folder_is_unavailable_not_deleted`.
- **Blank file name:** `test_a_blank_file_name_falls_back_to_the_path_as_title`.

The review found two defects, and each was fixed with a regression test:

- **Unreadable folders were skipped.** `Path.walk()` silently skips folders it
  cannot read. A chmod-000 subfolder made its files vanish with no error, which
  deletion-by-absence would have turned into tombstones. It now uses
  `walk(on_error=_raise)`, and the listing error becomes `SourceUnavailable`.
- **A blank file name stopped the read.** A file named `" .txt"` has a blank
  stem, which made `SourceMetadata` raise `InvalidSource` and abort every
  remaining page. The title now falls back to `external_id`.

With each fix temporarily reverted, its test failed, and the other 12 passed.

Not exercised by tests: the backup `resolve()` check for paths outside the root,
the race where a file disappears between listing and reading, the wrapped
`OSError` branch for file reads, and the NUL-character rejection.

## Reflection

1. **What does `DirectorySource` hide from the code that reads it?** It hides
   walking nested folders and putting them in a fixed order, turning absolute
   paths into portable IDs, decoding text and removing the byte-order mark,
   computing a version, and the symlink and traversal checks. The caller sees
   only pages of records, a checkpoint, and typed errors.
2. **What if the checkpoint were a page number?** If a new file sorting before
   the checkpoint were added between pages, every later file would shift by
   one. Page 2 would then start one file too early and repeat a record. A
   deletion would shift them back and silently skip a file, which is the worse
   case. A skipped file never reaches M03-T12's reconciliation, and M03-T13
   could treat it as missing and tombstone a file that still exists. A
   path checkpoint avoids both, because "sorts after `a/z.txt`" stays true
   whatever else changes.
3. **What if the symlink test were deleted and links were followed?** A link
   inside the root could point at any file the service can read, such as SSH
   keys, `.env` files, or another team's folder. Its content would be stored as
   a Document under the root's access, which is often `public`, so retrieval
   could quote it to every User. Without the test, nothing would fail when that
   change was made.
