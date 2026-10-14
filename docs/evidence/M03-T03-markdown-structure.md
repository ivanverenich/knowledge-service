# M03-T03 — Markdown structure evidence

Completed: 2026-10-14

## Invariant

The same Markdown text always produces the same structural blocks, in source
order. For every block, `parsed.text[block.start:block.end]` is exactly the
Markdown that produced it, where `parsed.text` is the input with line endings
normalized to `\n`. Parsing never fails because the Markdown is malformed:
CommonMark gives every input a meaning, so bad syntax becomes literal text
rather than an error. The only typed failure is input too large to parse
safely, which raises `DocumentTooLarge`.

The blocks are format-neutral. HTML (M03-T04) and PDF (M03-T05) parsers will
return the same `StructuralBlock` type, so chunking and citations never need to
know which format a Document came from.

## Prediction

Written before the parser and its tests exist. This is what I expect for each
malformed case in `tests/fixtures/markdown/malformed.md`.

- **`#No space means this is not a heading`.** CommonMark requires a space
  after `#`, so this becomes a paragraph whose text keeps the `#`.
- **An unclosed link, `[unclosed link](https://example.com ...`.** Without a
  closing parenthesis it is not a link. The brackets and the URL stay in the
  paragraph's text literally, and the block has no links. The unclosed `*`
  also stays as a literal character.
- **A raw `<script>` block.** It is dropped entirely: no block is produced and
  the script text appears nowhere in the output.
- **A table row with too few or too many cells.** The table keeps the header's
  column count. A short row is padded with an empty cell, and an extra cell
  beyond the header's columns is dropped.
- **A `javascript:` link.** The parser refuses unsafe link targets, so it never
  becomes a `Link`. The text stays as a plain paragraph, and the block's links
  are empty.
- **A code fence that is never closed.** The code block runs to the end of the
  file instead of failing, and keeps its `python` language.

## Design

Changed files:

- `pyproject.toml`, `uv.lock`: added `markdown-it-py>=4,<5`, locked at 4.2.0
  with its dependency `mdurl` 0.1.2.
- `src/knowledge_service/structure.py`: the format-neutral block model.
- `src/knowledge_service/markdown_parser.py`: `parse_markdown`.
- `tests/test_markdown_parser.py` (11 tests), plus `representative.md`, `malformed.md`,
  and their reviewed `*.blocks.json` golden files in `tests/fixtures/markdown/`.

The contract:

| Name | Role |
|---|---|
| `BlockKind` | `heading`, `paragraph`, `list_item`, `code`, `table`. |
| `StructuralBlock` | `kind`, `text`, `start`, `end`, plus `heading_level` (headings only, 1–6), `list_depth` (list items only, at least 1), `language` (code only), and `links`. Only a code block may have empty text. |
| `Link` | Visible text and a non-empty target. |
| `ParsedDocument` | The normalized text the offsets index, and the blocks in source order. No block may end past the text. |
| `InvalidBlock` | A block that breaks those rules: a programming error. |
| `DocumentParseError` → `DocumentTooLarge` | Typed failures for content a parser refuses, for M03-T06 to turn into a quarantine result. |

How `parse_markdown` works and why:

- **CommonMark plus pipe tables.** `MarkdownIt("commonmark").enable("table")`
  decides what is a heading, a list, or a link by the specification, not by our
  own rules, so malformed input has one defined meaning instead of an error.
- **Offsets.** Line endings are normalized to `\n` first. The parser's
  `[first_line, last_line)` map is turned into character offsets through a
  table of line starts, with trailing newlines trimmed, so
  `text[start:end]` is the block's exact source.
- **Lists.** Each list item is its own block with its nesting depth; the list
  itself is not a block. Chunking can then never split one item.
- **Tables.** One line per row, cells joined with ` | `. The header fixes the
  column count: a short row is padded with an empty cell, and an extra cell is
  dropped. A row whose cells are all empty is dropped, and a table with no text
  left produces no block.
- **Links.** Link text is gathered in its own list while the link is open, so
  parsing time grows linearly with the number of links.
- **Safety.** Raw HTML blocks are dropped, so a `<script>` block never reaches
  the index. Inline HTML tags are dropped too, but text between them stays as
  plain text: `Hi <script>alert(1)</script> there` becomes `Hi alert(1)
  there`. It cannot execute, but sanitizing it properly is M03-T04's job. Unsafe link targets such as `javascript:` are refused by
  `markdown-it-py` and stay plain text. Input over 1,000,000 characters raises
  `DocumentTooLarge`.
- **Format-neutral.** The model lives in `structure.py`, so the HTML and PDF
  parsers can return the same blocks. The parser is not wired into
  `DirectorySource`; choosing a parser per media type is M03-T06's job.

The fixtures contain only synthetic text and `example.com` or `python.org`
links. No credential or private content entered any artifact.

## Verification

Final run on 2026-10-14, after the review fixes, with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_markdown_parser.py -q` | 11 passed |
| `make format-check lint typecheck` | 81 files already formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 322 passed, 20 deselected |
| `make check` | 323 passed, 17 skipped, 2 deselected; `markdown_parser.py` 94% covered, `structure.py` 75% |
| `make docs-check` | exit 0 |
| `git diff --check` | silent |
| `pre-commit run` on the changed files | all hooks passed |
| `uv lock --check` | lockfile consistent with `pyproject.toml` |

The golden files were generated once with `UPDATE_GOLDEN=1`, reviewed against
the expected-block table in the lesson guide, and then pass without it:

- **`representative`:** 11 blocks. That is 3 headings at levels 1–3, the
  third in Ukrainian; a
  paragraph whose text has no Markdown syntax and one link; list items at depth
  1 and 2; `bash` code; a table with its link; and a Ukrainian paragraph.
- **`malformed`:** 6 blocks, matching every prediction above. The
  `[unsafe]: javascript:…` definition line also became a plain paragraph,
  because the parser refuses it as a link definition. No `<script>` text
  appears anywhere.

The focused tests prove that offsets slice back to the exact source, that
`\r\n` input gives identical blocks, that nested list items record depth 1 and
2, that oversized input raises `DocumentTooLarge`, and that a heading without a
level is rejected.

The review found two defects, and each was fixed with a regression test:

- **An all-empty table crashed the parser.** `| |` followed by `|-|` raised
  `InvalidBlock`, an untyped crash on malformed input. The table branch now
  skips a table with no text, and empty rows are dropped.
  `test_a_table_with_only_empty_cells_produces_no_block` covers three such
  inputs.
- **Link handling was quadratic.** Each link rebuilt all the text collected so
  far, so 20,000 links took 3.6 seconds and input near the size limit could take
  tens of seconds. 40,000 links now parse in about 0.9 seconds, and
  `test_many_links_parse_in_linear_time` fails if they take more than 5.

Against the unfixed parser, all four new test cases failed, and the 7 original
tests still passed. The golden files did not change.

Not exercised by tests:

- the two `DocumentParseError` guards, for a block token with no source
  position and a table that is never closed; `markdown-it-py` never produces
  either
- hard line breaks
- an empty heading being skipped
- most `StructuralBlock` and `ParsedDocument` rule violations besides the
  missing heading level
- content nested deeper than `markdown-it-py`'s limit of 20 levels, which is
  dropped silently
- a link with an empty target, which keeps its text but produces no `Link`

## Reflection

1. **What does a later task not need to know about Markdown?** It does not need
   to know which syntax makes a heading or a list, because blocks arrive with a
   kind and a level. It does not need to strip link, emphasis, or code markup
   from text, because `text` is already plain and links are separate. It does
   not need to care about Windows line endings or nested list indentation,
   because offsets and depth are already resolved.
2. **What could a regular-expression parser get wrong?** A simple pattern
   such as `^#+\s*(.*)` would turn `#No space means this is not a heading` into
   a heading, because it allows zero spaces. That would give M03-T10's chunker
   a false section boundary. A link pattern could also accept
   `javascript:alert(1)` as a link target and pass it on to M03-T11's citation
   links.
3. **What breaks if the offset test is deleted and `\r\n` normalization is
   removed?** I checked this by parsing without the normalization.
   `markdown-it-py` still counts `\r\n` as one line break, and the block text is
   unchanged. However, every source slice ends with a stray `\r`: the heading's
   slice is `'# Title\r'`. The offsets also differ from the same file saved
   with `\n`, so the two parses are no longer equal. M03-T11 would show
   excerpts with stray carriage returns, and the same document would get
   different offsets depending on which editor last saved it. Without the
   offset and line-ending tests, nothing would catch this.
