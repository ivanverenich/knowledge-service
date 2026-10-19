# M03-T06 — Normalize Source Records evidence

Completed: 2026-10-19

## Invariant

The same Source Record and Source always produce an equal outcome. A present
record never makes `normalize` raise: it becomes a `NormalizedDocument` with at
least one block, or a `Quarantined` with a `QuarantineReason`. Either way the
outcome keeps the record's identity: `source_id`, `external_id`, and source
version. A title is never empty, is one NFC line, and holds no control or
invisible characters. A quarantine detail never repeats the record's content.
Only a deleted record makes `normalize` raise, with `InvalidSource`, because
deletions never reach normalization.

## Prediction

Written before `normalize` and its tests exist. This is what I expect for each
record.

- **A Markdown file.** A `NormalizedDocument` whose blocks are the heading and
  paragraphs, with the media type `text/markdown`, the record's title, and a
  parser version that names both the parser and `markdown-it-py`'s version.
- **The same record normalized twice.** Two outcomes that compare equal, field
  by field, because nothing in normalization depends on time or randomness.
- **`Text/HTML; charset=utf-8`.** The parameter is dropped and the type
  lowercased, so the HTML parser is chosen and the document's media type is
  `text/html`.
- **A PDF.** A `NormalizedDocument` from the PDF parser, whose blocks carry
  page numbers and whose parser version names `pypdf` and its version.
- **A `.txt` file that is not UTF-8.** `DirectorySource` still returns it,
  unchanged, and `normalize` quarantines it as `undecodable_text`. The other
  files read in the same page are not affected.
- **A file of only spaces.** Quarantined as `empty_content` before any parser
  runs.
- **An HTML page that is only a navigation bar.** The HTML parser drops the
  navigation and returns no blocks, so it is quarantined as `empty_content`
  after parsing.
- **A scanned PDF.** The PDF parser raises `NoTextLayer`, which becomes a
  quarantine with the reason `no_text_layer`.
- **An `image/png` record.** No parser matches, so it is quarantined as
  `unsupported_media_type`, with no parser version.
- **A title made of zero-width characters.** Cleaning removes every
  character, so the title falls back to the external ID.
- **A deleted record.** `normalize` raises `InvalidSource`, because a deleted
  record carries no content to normalize.

## Design

Changed files:

- `src/knowledge_service/directory_source.py`: `_MEDIA_TYPES` adds `.md`
  (`text/markdown`), `.html` (`text/html`), and `.pdf` (`application/pdf`).
  Each record carries the file's bytes unchanged, and the version hashes those
  raw bytes. `_decode` is gone.
- `src/knowledge_service/sources.py`: `UndecodableSourceContent` is removed;
  nothing raises it any more.
- New `src/knowledge_service/text_parser.py`: `parse_text`, the first parser
  for `text/plain`.
- New `src/knowledge_service/normalization.py`: `normalize`,
  `NormalizedDocument`, `Quarantined`, and `QuarantineReason`.
- `tests/test_directory_source.py` (13 tests), new `tests/test_text_parser.py`
  (4), and new `tests/test_normalization.py` (25, counting parametrized cases).

How the module meets each requirement:

| Requirement | Decision |
|---|---|
| Selects a parser | `_PARSERS` maps the canonical media type to one of four parsers. The three text parsers are wrapped by `_decoded`, so every entry takes bytes. |
| Canonicalizes metadata | `_canonical_media_type` drops parameters such as `charset`, lowercases an ASCII value, and maps `application/xhtml+xml` and `text/x-markdown` to their standard names. `_canonical_title` removes control, format, and surrogate characters, applies NFC, collapses whitespace, cuts to 200 characters, and falls back to the external ID and then `Untitled`. |
| Records parser version | Each parser carries a version such as `markdown/1 markdown-it-py/4.2.0` or `pdf/1 pypdf/6.21.1`, read from the installed package when the module loads. |
| Detects empty content | Whitespace-only bytes are quarantined before parsing, and a parse that yields no blocks is quarantined after it. Both use `empty_content`. |
| Typed outcomes | `normalize` returns `NormalizedDocument` or `Quarantined`. Undecodable text and every `DocumentParseError` subclass map to a `QuarantineReason`. Only a deleted record raises, with `InvalidSource`. |
| Deterministic | Nothing depends on time or randomness, apart from the PDF parser's wall-clock budget, which only matters for hostile files. |

**Connector versus parser.** Decoding moved from `DirectorySource` into
normalization. Before, one file that was not UTF-8 made `fetch_page` raise, so
the whole page, and with it the Synchronization Run, failed. Now the connector
only lists files and reads bytes, and a bad file becomes one `Quarantined`
record while the rest are normalized. The end-to-end test reads a folder with
four good files and one that is not UTF-8 and gets four documents and one
quarantine.

**Why `NormalizedDocument` and not `Document`.** `documents.Document` needs a
`DocumentId`, fingerprints, and versions, which belong to M03-T08 and M03-T12.
`NormalizedDocument` holds everything a Document is built from: provenance,
title, media type, timestamps, size, access, parser version, and blocks.

**Adversarial review.** An independent review fuzzed the prototype with about
24,000 records (random bytes and hostile tokens across 15 media types,
including aliases, parameters, NUL, a lone surrogate, and a Kelvin-sign
look-alike) and 3,000 mutated PDFs. No exception escaped and no outcome was
nondeterministic. It found three defects, now fixed, each with a test that
failed against the first version:

- A title made only of zero-width or control characters, with an external ID
  of the same kind, cleaned to an empty string. The final fallback is now
  `Untitled`.
- Cleaning applied NFC before removing invisible characters, so `e`, a
  zero-width space, and an accent ended up as an uncomposed pair. NFC now runs
  after the removal.
- `str.lower()` is Unicode-aware, so `text/mar\u212adown` (Kelvin sign)
  lowercased into `text/markdown`. Only ASCII media types are lowercased now.

Title and detail cleaning, and `parse_text`, scale linearly; at about 1,000,000
characters each takes well under a second.

**Known limits, recorded here and not caused by this task:**

- **PDF opening phase (deferred by decision).** A PDF whose page dictionaries
  each sit in a padded object stream can still use hundreds of MB while it
  opens (the M03-T05 follow-up). `DirectorySource` reads operator-chosen local
  folders, so the hardening is deferred until PDFs can come from a remote or
  user-supplied Source, such as Confluence or uploads.
- **Markdown has no time budget.** Hostile Markdown near the 1,000,000-character
  limit, such as `![a](` repeated, takes 20 to 50 seconds inside
  `markdown-it-py`, and some inputs peak at 300 to 500 MB.
- **HTML tables without closing tags.** A 20 by 15 table that omits the
  optional `</td>` and `</tr>` passes the HTML parser's 256-element nesting
  limit and is quarantined as `unparsable`; only `<p>` and `<li>` are closed
  implicitly.
- **`charset` is ignored.** Text must be UTF-8; a Latin-1 file is quarantined as
  `undecodable_text`.
- **Text is decoded before its size is checked.** `DirectorySource` reads a
  whole file, and `_decoded` decodes all of it before the text parser's
  1,000,000-character limit applies. A 60 MB `.txt` file is correctly
  quarantined as `too_large`, but uses about 100 MB more while doing so, growing
  with the file. A cheap guard would refuse more than 4,000,000 bytes before
  decoding, since UTF-8 uses at most 4 bytes per character.

The fixtures hold only synthetic text. No credential or private content
entered any artifact, and the public types name no provider.

## Verification

Final run on 2026-10-19 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_normalization.py tests/test_text_parser.py tests/test_directory_source.py -q` | 42 passed |
| `make format-check lint typecheck` | 90 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 398 passed, 20 deselected |
| `make check` | 399 passed, 17 skipped, 2 deselected; total coverage 84%; `normalization.py` and `text_parser.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held. Along the way:

- The first `make format-check` failed on five files: missing blank lines
  between functions, a mis-indented signature in `_decoded`, and three missing
  final newlines. `make format` fixed them without changing behaviour.
- A typo in the NUL-character message (`characted`) and two docstrings in
  `directory_source.py` that still said "plain-text" were corrected.

## Reflection

1. **What happened to the other files when one was not UTF-8.** Before,
   `DirectorySource._read` raised `UndecodableSourceContent` for the bad file,
   which made the whole `fetch_page` call fail. None of the files on that page
   were returned, the checkpoint could not advance, and the Synchronization
   Run failed, and it would fail again on every retry until someone removed
   the file. Now the connector returns every file's bytes, and only the bad
   file comes back from `normalize` as `Quarantined` with
   `undecodable_text`; the other files are normalized as usual.
2. **What a caller would need if `normalize` raised.** A loop over 1,000
   records would need a `try` around every call that catches exactly the
   content errors, records the reason, and continues. Catch too little and one
   bad file stops the run; catch too much, such as a bare `except Exception`,
   and real bugs in our code are silently filed as bad content. Returning
   `Quarantined` makes the reason ordinary data, and the type checker reminds
   the caller to handle both outcomes.
3. **Why the parser version includes the library version.** The same bytes
   can produce different blocks when a library changes. If `markdown-it-py`
   changes how it splits a list next to a paragraph, a file normalized before
   the upgrade and its unchanged copy normalized after it have different
   blocks, so their chunks and citations differ too. Recording
   `markdown-it-py/4.2.0` on each Document lets a later task find the
   Documents built by an older parser and rebuild them, instead of guessing.
4. **A test whose removal would let a real bug through.**
   `test_every_file_in_a_folder_is_normalized_or_quarantined`. Without it, a
   change that made `DirectorySource` decode text again, or that made
   `normalize` raise on bad UTF-8, would still pass every unit test of the
   pieces separately, and in production one badly encoded file would stop a
   whole synchronization again.
5. **Which alternative would make the next change harder.** Decoding inside
   each parser, or an `if` in `normalize` that decides which types are text,
   would put the decoding rules in several places. Adding a format, or
   changing the rules, for example to honour `charset`, would then mean
   changing every copy; with `_decoded` it is one function and one table
   entry.
6. **What complexity `normalize` hides from its caller.** The caller hands
   over a Source Record and gets one of two values back. It never decodes
   bytes or deals with a byte-order mark, a NUL character, or `\r\n` line
   endings; never chooses a parser or spells a media type; never catches
   `DocumentTooLarge`, `EncryptedDocument`, `CorruptDocument`, `NoTextLayer`,
   or a decoding failure; and never cleans a title. A Synchronization Run only
   has to file each outcome.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
