# M03-T11 — Chunk provenance evidence

Completed: 2026-10-21

## Invariant

For every chunk, `chunk_sources` returns an excerpt, a location, and an
anchor, and the recorded source offsets slice the original text of the blocks
the chunk came from. Everything shown is display-safe: no control or
bidirectional characters, no user name, password, query, or fragment in any
address, and no link that leads to another host or runs code. A chunk that
does not belong to the document is refused with `InvalidChunkSource`.

## Prediction

Written before the provenance module and its tests exist. This is what I
expect for each case.

- **The Vacation chunk of the handbook fixture.** The excerpt is the chunk's
  text on one line: `Vacation Request vacation at least two weeks ahead. Your
  team lead approves it in the HR system.` The location is the external ID,
  `guides/handbook.md`, and the anchor is that location plus
  `#offset=START-END`. Slicing the original Markdown with those offsets gives
  text that starts with `## Vacation`, because the offsets cover the whole
  heading and paragraph blocks in the source, including the `##`.
- **A two-page PDF.** Each chunk records its first and last page. A chunk that
  runs from page 1 into page 2 has the anchor `…#page=1`, and a chunk on page
  2 only has `…#page=2`: the anchor names the first page.
- **A source URL with a user name, a password, and a query.** The location is
  the URL with only its scheme, host, and path; the user name, password, and
  query are gone. The anchor starts with that clean URL.
- **The link `https://me:secret@docs.example.com/guide?token=t#top`.** Its
  target becomes `https://docs.example.com/guide`: credentials, query, and
  fragment are all dropped.
- **The relative link `../policy.md`.** Kept as it is: it is a plain path, so
  it can only lead to a file on the same site.
- **An HTML link to `\\evil.com`.** Dropped. Browsers read backslashes as
  slashes, so it would lead to another host, and a backslash is not allowed
  in a kept relative path.
- **Text containing `//admin:hunter2@db.internal`.** In the excerpt it becomes
  `//db.internal`: the `admin:hunter2@` part is removed even without a scheme
  in front.
- **A window that starts in the middle of a table.** Its `table_header` is
  the table's first row, such as `Tool | Version`, so its rows can still be
  read. The window that starts at the table's first row has no header, since
  the header is already in its text.
- **An external ID of `javascript:alert(1)`.** The location is
  `unknown source`: it looks like an address, but not a safe web address.
- **A chunk from another document.** `InvalidChunkSource`, because its offsets
  do not slice this document's reading text into the chunk's text.

## Design

Changed files:

- `src/knowledge_service/html_parser.py`: `_safe_target` drops a link whose
  target `urlsplit` cannot parse, instead of raising `ValueError`.
- New `src/knowledge_service/provenance.py`: `ChunkSource`, `chunk_sources`,
  `display_url`, `InvalidChunkSource`, `EXCERPT_CHARACTERS` (200), and
  `UNKNOWN_LOCATION`.
- `tests/test_html_parser.py` (one new test, 28 in total) and new
  `tests/test_provenance.py` (45 tests, counting parametrized cases).

What a `ChunkSource` carries for each chunk:

| Field | Meaning |
|---|---|
| `excerpt` | The chunk's first words, display-safe, at most 200 characters, cut between words or whole characters. |
| `location` | A safe source URL, or the external ID shown safely, or `unknown source`. |
| `anchor` | `location#page=N` for a PDF, otherwise `location#offset=START-END`; the value M03-T12 will store as `Chunk.source_anchor`. |
| `heading_path` | Copied from the chunk. |
| `first_page`, `last_page` | The PDF pages of the first and last block, or None. |
| `source_start`, `source_end` | Offsets into the original parsed text, from the first block's start to the last block's end. |
| `links` | Links of the covered blocks whose text appears in the chunk, with safe targets, without repeats. |
| `table_header` | The table's first row when the chunk starts after it, so its rows can still be read. |

Decisions:

- **A separate module; the chunkers are unchanged.** `chunk_sources` reads
  the chunkers' output once per document.
- **A strict address policy.** `display_url` keeps only an `http` or `https`
  scheme, the host, the port, and the path. The user name, password, whole
  query, path parameters (`;jsessionid=...`), and fragment are dropped, because
  any of them can carry a secret and no list of secret names is complete.
  The host is converted with IDNA to ASCII and must then be letters, digits,
  dots, and hyphens, or a bracketed IPv6 address; that shows the look-alike
  `\u0430pple.com` as `xn--pple-43d.com` and refuses a percent-encoded host.
  Addresses with whitespace, a backslash, a quote, `<`, `>`, or similar
  characters are refused.
- **Link targets.** A web link goes through `display_url`; a `mailto:` link
  keeps only a plain address (no `%`, no query); a relative link survives only
  as a plain path of letters, digits, and `. _ ~ % / + -` that does not start
  with `//`. Anything else is dropped.
- **Shown text.** Excerpts, link texts, table headers, and locations drop
  control, format, bidirectional, and surrogate characters, except the
  zero-width non-joiner and joiner that Persian, Indic scripts, and emoji
  need. Anything shaped like `name:password@` is removed, with or without a
  scheme, except a `mailto:` address, and each web address is shown through
  `display_url` after its query and fragment are cut off.
- **Ownership is checked.** A chunk's offsets must slice the reading text to
  its text, and its start and end must fall inside the blocks it names; a
  blank external ID is refused too. Both raise `InvalidChunkSource`.
- **The HTML parser crash.** `parse_html('<a href="http://[broken">')` raised
  a raw `ValueError` from `urlsplit`, so one such link made `normalize` raise
  and could stop a Synchronization Run. The link is now dropped.

**Adversarial review.** Two review rounds attacked earlier versions of this
module and found ten breaks, all fixed with tests that failed against the
version they broke (12 failures against the first version, 9 against the
second):

- Round one: relative links such as `\\evil.com\x`, `/\evil.com`, and
  `\/evil.com`, which browsers resolve to another host; secrets kept in
  relative-link queries; secrets after `;` in a query or path; credentials in
  an autolink's visible text; an unsafe external ID shown as the location;
  block indexes that did not match the chunk; excerpts cutting an accent or
  half a flag. It also showed that a list of secret parameter names was both
  too broad (dropping `keyword` and `author`) and too narrow (keeping `pw`
  and `jwt`), which is why the whole query is now dropped.
- Round two: credentials without a scheme (`//admin:hunter2@db.internal`), a
  quote ending a matched address early so part of a password stayed visible,
  and an external ID such as `notes http://u:p@h.com/?t=S` shown unredacted.

The final version held a 200,000-input fuzz of `display_url` (always
idempotent, never a user name, password, query, fragment, or `;`) and a
100,000-input fuzz of HTML link targets (never an exception other than
`DocumentParseError`).

**Known limits:**

- **Queries are always dropped,** so a link such as `?id=123` loses its query.
- **Source offsets are block-level.** Several windows of one long paragraph
  or table share the same anchor.
- **A PDF anchor names only the first page,** even when `last_page` is later.
- **Decomposed Hangul and Indic conjuncts can still be cut** in an excerpt of
  text without spaces; splitting them correctly needs Unicode's full text
  segmentation rules, which the standard library does not provide.
- **Only what is shown is redacted.** The chunk text itself is document
  content and is stored and embedded unchanged.
- **A caller must still escape text for HTML** when it renders a citation;
  display-safe is not the same as HTML-escaped.
- **A Windows-style external ID** such as `C:\Users\a.md` is shown as
  `unknown source`.
- **Ukrainian domains are shown in punycode.** `https://приклад.укр/документ`
  becomes `https://xn--80aikifvh.xn--j1amh/документ`: safe against
  look-alikes, but hard to read on a bilingual platform. Showing the readable
  form when a domain uses a single alphabet, as browsers do, is a possible
  later improvement.

**Follow-ups found by the closing review (not yet fixed):**

- **A raw `@` inside a password is only partly removed from shown text.**
  `//admin:pa@ss@db.internal` is shown as `//ss@db.internal`, because the
  password part of `_CREDENTIALS` stops at the first `@`. Such userinfo is
  malformed (an `@` in a password must be percent-encoded), and the URL form
  is safe because `urlsplit` drops the whole userinfo, but it is still a
  partial leak. Fix: match the password up to the last `@` in the word, and add
  a test case.
- **A non-web external ID with `://`, such as `s3://bucket/key?sig=1`, becomes
  the location `[link]`** instead of `unknown source`. Fix: check for a scheme
  before redacting in `_location`.
- **A chunk of only combining marks gets an excerpt of only the ellipsis.**
  Harmless, but uninformative.

The fixtures hold only synthetic text and `example.com` addresses. No
credential or private content entered any artifact, and the public types name
no provider.

## Verification

Final run on 2026-10-21 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_provenance.py tests/test_html_parser.py -q` | 73 passed |
| `make format-check lint typecheck` | 99 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 526 passed, 20 deselected |
| `make check` | 527 passed, 17 skipped, 2 deselected; total coverage 87%; `provenance.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held. Along the way:

- The first format check failed on three files: `chunk_sources` and
  `_describe` had parameters indented 8 spaces, two comments had one space
  before `#`, and `tests/test_html_parser.py` had no final newline.
  `make format` fixed them without changing behaviour.
- The docs link checker reads Markdown links even inside code blocks, so the
  test strings for `../policy.md` and `ftp://` links are written as
  `"[policy]" "(../policy.md)"`; Python joins them into one string.

## Reflection

1. **Why every query parameter is dropped.** Secrets can hide under any name.
   The review showed that a list of names cannot win: it dropped harmless
   parameters such as `keyword` and `author`, because they contain `key` and
   `auth`, and it kept real secrets named `pw` and `jwt`. Secrets also hid
   after `;`, which the query parser no longer splits on. Dropping the whole
   query is simple to explain and impossible to slip past; the cost is that a
   link such as `?id=123` loses its query.
2. **How `\\evil.com\x` leads elsewhere.** Browsers treat a backslash in a
   web address like a forward slash, so `\\evil.com\x` becomes
   `//evil.com/x`, a scheme-relative address on another host. It passed the
   old check, which only looked for a leading `//`. Now a relative target must
   be a plain path of letters, digits, and `. _ ~ % / + -`, so a backslash is
   refused.
3. **Using the source offsets.** `parsed.text[source_start:source_end]` is the
   original text of the blocks the chunk came from: the Markdown section with
   its `##`, the raw HTML, or the PDF text. A citation can show or highlight
   that span. Windows cut from one long paragraph share the range because
   only whole blocks know their place in the source; the windows' own offsets
   index the reading text, not the original.
4. **A test whose removal would let a leak through.**
   `test_credentials_and_queries_never_show_in_text`. Without it, a change
   back to matching only `scheme://` addresses would pass the other tests,
   and a document mentioning `//admin:hunter2@db.internal` would show that
   password in every citation excerpt.
5. **What complexity the module hides from its caller.** The caller passes
   chunks and an external ID and gets ready-to-show citations. It never parses
   URLs, decides which characters are safe, cuts excerpts, finds pages or
   source offsets, or checks that a chunk belongs to the document.
6. **Which alternative would make the next change harder.** Building
   citations inside the chunkers would mix two jobs: every new chunking
   strategy would have to repeat the display-safety rules, and a fix to those
   rules would have to be made in several places. Keeping them in one module
   that reads any chunker's output means one place to fix.
7. **What would escape without the negative tests.** Without
   `test_a_chunk_from_another_document_is_refused`, a caller that mixed up two
   documents' chunks would get citations pointing at the wrong paragraph,
   with confident offsets, instead of an `InvalidChunkSource`.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
