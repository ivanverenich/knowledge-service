# M03-T04 — HTML parsing evidence

Completed: 2026-10-14

## Invariant

The same HTML always produces the same structural blocks, in source order. For
every block, `parsed.text[block.start:block.end]` is HTML from the source that
contains the block's text, where `parsed.text` is the input with line endings
normalized to `\n`. No block text and no link target can execute: script and
style content, event-handler attributes, and `javascript:`, `vbscript:`, or
`data:` targets never reach the output. Malformed HTML never crashes the
parser. The only typed failures are input that is too large
(`DocumentTooLarge`) and elements nested deeper than the limit
(`DocumentParseError`).

The blocks are the same format-neutral `StructuralBlock`s that the Markdown
parser returns, so chunking and citations do not need to know a Document was
HTML.

## Prediction

Written before the parser and its tests exist. This is what I expect for each
part of `tests/fixtures/html/hostile.html`.

- **`onclick` attribute.** The output is plain text, so attributes never appear
  in it. The `<div>`'s text survives as a paragraph, and the handler is gone.
- **Inline `<script>` inside a paragraph.** The script and its content are
  dropped, but the paragraph is not split: "Inline script is removed." stays
  one block.
- **`javascript:` link.** The target is refused, so the block has no `Link`.
  The link's visible text stays in the paragraph.
- **`java&#x09;script:` link.** The entity is decoded to a tab, and the tab is
  ignored when the scheme is read, so this is `javascript:` too. It is refused
  in the same way, and its text stays.
- **`data:` link.** Refused like `javascript:`. Its text stays.
- **`#top` link.** A link within the same page is not a useful target, so it
  becomes no `Link`, but "Back to top" stays as text.
- **`<iframe>`.** Dropped with everything inside it, including its fallback
  paragraph.
- **`<svg>` with a script.** Dropped with all its content: neither the script
  nor the SVG text appears.
- **`hidden` and `aria-hidden="true"` elements.** Dropped with their text,
  because a reader never sees them.
- **`<noscript>`.** Dropped. "Enable JavaScript" is page noise, not content.
- **Comment.** Ignored, including the `<p>markup</p>` written inside it.
- **Unclosed `<p>` and `<li>`.** HTML's implied end tags apply: a new `<p>`
  ends the previous paragraph, and a new `<li>` ends the previous item. They
  come out as two paragraphs and two list items, and the mis-nested
  `<b><i></b></i>` does not break the text.
- **Empty table.** A table whose cells are all empty produces no block.

## Design

Changed files:

- `src/knowledge_service/html_parser.py`: `parse_html`, `MAX_CHARACTERS`, and
  `MAX_DEPTH`.
- `tests/test_html_parser.py` (27 tests), plus `representative.html`,
  `hostile.html`, and their reviewed `*.blocks.json` golden files in
  `tests/fixtures/html/`.

No new dependency: Python's built-in `html.parser.HTMLParser` reports the line
and column of every tag and text event, which is what makes source offsets
possible. It also decodes entities, treats `<script>` and `<style>` content as
raw text, and never raises on broken HTML. The output reuses the
`StructuralBlock`, `ParsedDocument`, and error types from `structure.py`.

How `parse_html` meets each requirement:

| Requirement | Decision |
|---|---|
| Headings, text, links, tables | Events are collected into a buffer and emitted as a block at each block boundary. The innermost open element decides the kind: `h1`–`h6` give a heading with its level, `<pre>` gives code with the language from a `language-…` class on `<pre>` or a `<code>` inside it, an open `ul`/`ol` gives a list item with its depth, and anything else gives a paragraph. Tables become one line per row with cells joined by ` | `. Empty rows and empty tables are dropped. |
| Remove scripts and styles | `script`, `style`, `noscript`, `template`, `iframe`, `object`, `embed`, `svg`, `math`, and `canvas` are dropped with their whole subtree. An inline script is skipped without splitting its paragraph. `<script/>` is treated as a start tag, as browsers do, so the self-closing form cannot expose its content. While skipping, an end tag for an element opened outside the dropped one is ignored, so it cannot end the skip early. |
| Remove navigation noise | `head`, `nav`, `aside`, `footer`, `form`, `button`, `select`, elements with `hidden` or `aria-hidden="true"`, and the `navigation`, `banner`, `contentinfo`, `complementary`, and `search` roles are dropped the same way. Attribute values are compared case-insensitively. |
| Remove unsafe markup | Only text is emitted, so attributes such as `onclick` never appear. A link becomes a `Link` only for `http`, `https`, `mailto`, or relative targets. The target is trimmed, `urlsplit` ignores tabs and newlines, and the scheme is lowercased, so `java&#x09;script:` and `JaVaScRiPt:` are refused. Same-page `#fragment` links are refused too. |
| Preserved provenance | The open-element stack records each start tag's offset. A block usually runs from its element's start tag to the end of its closing tag. If HTML ends it implicitly, it runs to where the next element starts. When a nested block splits an element, as the nested `<ul>` does after "Install the tools:", the first part starts at its first text instead. Trailing whitespace is trimmed. Each slice is HTML that contains the block's text. |
| Malformed HTML | HTML's implied end tags are applied: a new block ends an open `<p>`, and a new `<li>` ends the previous item of the same list. An end tag closes everything down to its matching element, which handles `<b><i></b></i>`. Anything still open at the end of the document is closed there. |
| Bounded cost | Input over 1,000,000 characters raises `DocumentTooLarge`. Nesting deeper than 256 elements raises `DocumentParseError`. |

**Why there is a nesting limit.** Every tag consults the open-element stack,
so cost grows with depth. While prototyping, 40,000 nested `<div>`s took 13
seconds, and that time grew with the square of the depth. With the limit, the
cost per tag is bounded, and 200,000 nested `<div>`s fail in 0.03 seconds. Real
pages nest a few dozen levels, so the typed failure only stops pathological
input, and M03-T06 can quarantine it.

Bounded is not the same as fast. Each block still rescans a stack up to 256
deep. These inputs of about 1,000,000 characters were measured after the
review fixes:

| Input | Time |
|---|---|
| 380 copies of 255 nested `<div>`s, each closed | 0.96 s |
| 240,000 stray `</x>` tags at depth 250 | 1.98 s |
| 142,000 `<b></b>` pairs at depth 254 | 2.42 s |
| 120,000 `<p>` paragraphs at depth 255 | 5.56 s |

Keeping running counts of open tags would remove the rescans, at the cost of a
more complex parser. That is left for when real pages show a need.

The fixtures contain only synthetic text and `example.com` or `python.org`
links. No credential or private content entered any artifact. The parser is
not wired into `DirectorySource` yet; that is M03-T06's job.

## Verification

Final run on 2026-10-14, after the review fixes, with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_html_parser.py -q` | 27 passed |
| `make format-check lint typecheck` | 83 files already formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 349 passed, 20 deselected |
| `make check` | 350 passed, 17 skipped, 2 deselected; `html_parser.py` 94% covered |
| `make docs-check` | exit 0 |
| `git diff --check` | silent for the working tree |
| `pre-commit run` on the changed files | all hooks passed |

The golden files were generated with `UPDATE_GOLDEN=1`, which now ends in
`25 passed, 2 skipped` so an update run can never pass. They were reviewed
against the lesson's table and pass without the variable:

- **`representative`:** 11 blocks. These are h1, h2, and the Ukrainian h3; a
  paragraph with the decoded `Tom & Jerry` and one safe link; list items
  "Clone the repository." and "Install the tools:" at depth 1, and "uv" and
  "PostgreSQL" at depth 2; `bash` code over two lines; a table with its link;
  and the Ukrainian paragraph from a bare `<div>`. A scan of all block text
  found none of "Home", "About", "Privacy", "©", "analytics", or CSS.
- **`hostile`:** 8 blocks, matching every prediction above. A scan found none
  of the iframe, SVG, hidden, decorative, menu, noscript, or comment text, and
  no `alert`.

While prototyping, the parser was also checked in three other ways:

- **Fuzzing:** 3,000 random tag-soup documents produced no crash, and every
  offset stayed inside the text.
- **Fake regressions:** four safety rules were broken on purpose: allowing every
  link scheme, keeping `<script>`, ignoring `hidden`, and skipping implicit `<p>`
  closes. Each one made at least one test fail.
- **Bugs found and fixed before the lesson:**
  - list depth was off by one
  - an inline script split its paragraph
  - implied end tags were missing
  - a heading interrupted by a nested block could crash with `InvalidBlock`

That fuzzing only generated plain start and end tags, so it missed the defects
the review then found. Each was fixed with a regression test:

- **Self-closing tags leaked dropped content.** `HTMLParser` reports
  `<script/>` as a self-closing tag, which the parser ignored, so
  `<script/>alert(1)</script>` produced the paragraph `alert(1)`. The same
  happened with `<style/>` and `<div hidden/>`. Browsers ignore the `/` on
  non-void elements, and now the parser does too.
  `test_hidden_content_stays_hidden` covers these cases.
- **An outer end tag ended the skip early.** In
  `<p><a href=…>one<nav>x</a>two</nav>three</p>`, the `</a>` closed the `<nav>`
  on its way to the `<a>`, so the navigation text "two" leaked.
  `test_an_outer_end_tag_does_not_end_a_dropped_element` covers this.
- **Upper-case `aria-hidden="TRUE"` was not hidden.** This was found by the
  adversarial fuzzing below; the value is now lowercased before it is
  compared.
- **A code language leaked from inline `<code>`.** A `language-py` class
  outside `<pre>` carried over to the next code block. The class now applies
  only inside `<pre>`.
- **Void elements had no tests.** `<br>`, `<img alt>`, and `<hr>` are now
  covered.

Against the code as first written, the six new regression cases failed and
the other 20 tests passed. The golden files did not change.

After the fixes, an adversarial fuzz targeted the safety guarantee itself. It
generated 100,000 documents across five random seeds. Every dropped element
held a `LEAK` marker, and the documents mixed self-closing, upper-case,
stray, and interleaved end tags. No marker reached any block text or link
text, nothing raised an untyped error, and every offset stayed inside the
text.

Block text is plain text. Escaped markup such as `&lt;script&gt;` and an
image's `alt` text are kept as literal characters, so anything that renders
block text as HTML must escape it.

Not exercised by tests, from the coverage report:

- a dropped element left open at the end of the document
- an `<a>` without `href`, and a link closed with no text collected
- table elements other than cells, rows, and links, such as `<tbody>`
- an `<li>` outside any list
- an `<img>` without `alt`, `<hr>` inside a table, and a void element inside a
  dropped element
- a `<code>` class list that also contains non-`language-` names
- the defensive early return in `_end_row`

## Reflection

1. **What does indexing code no longer have to worry about?** It never has to
   handle executable content: scripts, styles, and embedded frames are dropped
   whole by `_is_noise` and `_skip_from`, and `onclick`-style attributes never
   leave the parser because only text is emitted. It never sees page chrome:
   `<nav>` menus, elements marked `role="navigation"`, footers, and forms are
   dropped by the same check. It never sees dangerous links, because
   `_safe_target` turns them into plain text. It also never deals with
   entities or with HTML's implied end tags, which `HTMLParser` and
   `_close_implied` resolve.
2. **What if only the `<script>` and `<style>` tags were removed but their text
   kept?** The hostile fixture would put `alert("inline")` and `alert("svg")`
   into the index, and the representative page would add its CSS rule and
   `window.analytics = true;`, all as if they were document text. Retrieval could then match a user's question against code, and an
   answer could quote `alert(...)` back to a user. Losing a few words of real
   text costs some recall. Indexing script text pollutes every search and puts
   attacker-controlled content into answers.
3. **What if `_safe_target` only checked `href.startswith("javascript:")`?**
   The fixture's `java&#x09;script:alert(2)` link decodes to a target with a
   tab in it, so `startswith("javascript:")` is false and the check passes. So
   would ` JaVaScRiPt:` with a leading space and uppercase letters, and the
   `data:text/html,…` link. Each would become a real `Link`. When M03-T11 shows
   citations, a user clicking that link could run the attacker's script in
   their browser. Without the nine-case test, the hostile golden file and the
   target check in `test_no_executable_content_survives` would still catch the
   encoded link, but nothing would test the upper-case, `vbscript:`, or
   `#fragment` cases.
