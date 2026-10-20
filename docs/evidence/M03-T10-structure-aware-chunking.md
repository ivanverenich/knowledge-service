# M03-T10 — Structure-aware chunking evidence

Completed: 2026-10-20

## Invariant

The same document and budgets always give the same chunks, in order, with
ordinals from 0. Every chunk holds between 1 and `max_tokens` tokens, slices
back exactly from the reading text, and every token is in some chunk. Chunks
are cut only between blocks, except inside fixed windows for a block that
does not fit. A chunk's heading path is the path of the block it begins in,
and every title in it is at most 100 characters. `chunk_document` gives
exactly the same output as before this change.

## Prediction

Written before the structure-aware chunker and its tests exist. This is what I
expect for each case.

- **`# Guide`, `## Setup`, `### Tools`, `## Usage`, each with a short
  paragraph, and a minimum of 1.** Four chunks, one per section, each starting
  with its heading. Their paths are `Guide`, `Guide > Setup`,
  `Guide > Setup > Tools`, and `Guide > Usage`: when `## Usage` arrives,
  `Setup` and `Tools` are dropped from the path because `Usage` is at the same
  level as `Setup`.
- **Two tiny sections, `# A` and `# B`, with a minimum of 10.** One chunk
  holding both. When `# B` arrives, the open chunk is still below 10 tokens,
  so the heading does not start a new chunk. The chunk's path is `A`, because
  that is the block it begins in.
- **Three paragraphs of 3 tokens, a maximum of 6, and no headings.** Two
  chunks: the first two paragraphs together (6 tokens), then the third alone,
  because adding it would make 9. No chunk has a heading path.
- **A paragraph of 10 tokens with a maximum of 4 and an overlap of 1.** It
  cannot fit in one chunk, so it falls back to fixed windows: `w0` to `w3`,
  `w3` to `w6`, and `w6` to `w9`, each sharing one token with the previous.
- **A heading followed by a paragraph that fits alone but not together with
  the heading.** No chunk holds only the heading. The heading and the
  paragraph are cut into fixed windows together, so the first window starts
  with the heading and continues into the paragraph.
- **A heading of 1,000 words.** The heading itself is far over the maximum,
  so it is cut into several windows. Every chunk's heading path is the title
  cut to at most 100 characters, not the whole heading.
- **An empty document.** No chunks, rather than an error.
- **A minimum above the maximum.** `InvalidChunking`, because no chunk could
  ever satisfy both budgets.

## Design

Changed files:

- `src/knowledge_service/chunking.py`: new `chunk_by_structure`,
  `_heading_paths`, the budgets `MIN_TOKENS` (64) and `MAX_TOKENS` (256),
  `HEADING_SEPARATOR` (` > `), `MAX_TITLE_CHARACTERS` (100), and a new
  optional `DraftChunk.heading_path`. The fixed-window loop moved into the
  shared helpers `_windows` and `_draft`; `chunk_document` is now one call to
  `_windows` and gives the same output as before.
- New `tests/test_structured_chunking.py` (16 tests, counting parametrized
  cases), `tests/fixtures/chunking/handbook.md`, and its golden file
  `tests/fixtures/chunking/handbook.chunks.json`.

How `chunk_by_structure` decides, block by block:

| Situation | Result |
|---|---|
| The block holds no tokens (an empty code block) | Skipped. |
| The block alone holds more than `max_tokens`, or the open chunk is below `min_tokens` and the two together would pass `max_tokens` | Fixed windows of `max_tokens` with `overlap_tokens`. An open chunk below the minimum, such as a lone heading, becomes the start of the first window; otherwise it is finished first. |
| Adding the block would pass `max_tokens`, or the block is a heading and the open chunk holds at least `min_tokens` | The open chunk is finished, and the block starts a new one. |
| Otherwise | The block joins the open chunk. |

Decisions:

- **Cut only between blocks.** Paragraphs and list items stay whole; only a
  block that cannot fit is cut, and only then do chunks overlap. Structural
  cuts are natural boundaries, so overlapping them would only store and embed
  the same words twice.
- **A heading starts a new chunk once the current one holds `min_tokens`.**
  Without a minimum, every short section would become a tiny chunk with too
  little context to match a question.
- **One rule for heading paths: the path of the block a chunk begins in.**
  `_heading_paths` keeps a stack of open headings; each new heading pops
  every heading at its own level or deeper, so `## Usage` after
  `### Tools` gives `Guide > Usage`.
- **One module, shared helpers.** Both chunkers share the reading text, the
  tokens, and `_windows`, which windows any range of tokens while keeping
  offsets and block indexes relative to the whole document. That is what lets
  the fallback cut a single block.
- **Budgets are checked first.** `1 <= min_tokens <= max_tokens` and
  `0 <= overlap_tokens < max_tokens`, or `InvalidChunking`.

**Golden test.** The handbook fixture, with `min_tokens=8`, `max_tokens=24`,
and `overlap_tokens=4`, gives seven chunks: one per section, the tiny
`Security` section merged with its first subsection, and the long `Laptops`
paragraph cut into three overlapping windows that start with the `Laptops`
heading. The golden file was generated with `UPDATE_GOLDEN=1` and checked
against the expected table and file in the lesson guide; it matched exactly.

**Adversarial review.** An independent review, whose scripts are not part of
the repository, compared the refactored
`chunk_document` field by field with the version from HEAD on 12,000 random
documents and settings: identical. On random hand-built documents and random
Markdown, HTML, and text, every chunk held 1 to `max_tokens` tokens, ordinals
were contiguous, every chunk sliced back exactly, block indexes matched an
independent computation, and every token was covered. About 1.6 million
characters, 100,000 blocks, and 10,000 headings chunk in about 0.7 seconds.
It found one break and two design problems, all fixed with tests that failed
against the first version:

- **Heading paths grew with the square of a heading's length.** A heading
  larger than `max_tokens` was cut into windows that each carried the whole
  heading as its path: a 400 KB file gave 893 chunks and about 357 MB of
  paths. Titles are now cut to 100 characters, and the same input gives about
  17 KB of paths in total.
- **A lone heading before a block that fills a chunk stood alone.**
  `## Getting started` followed by a 256-token paragraph became a 2-token
  chunk. Now a block that cannot join a chunk below the minimum is windowed
  together with it.
- **Two path rules disagreed.** Merged sections took the first block's path,
  but a heading absorbed into a window took the oversized block's path. Now
  every chunk takes the path of the block it begins in.

The tests were also checked against deliberately broken versions: restoring
the lone heading failed the golden test, ignoring the minimum failed the
golden and minimum tests, and dropping the heading-level popping failed the
golden and heading-path tests.

**Recorded notes:**

- **A path can mislead when sections merge.** Tiny sections that join
  because they stay below the minimum share the first block's path, so a
  chunk covering sections `S0` to `S4` says only `S0`. In a sample handbook, a
  chunk mostly about passwords carried the path
  `Employee handbook > Time off > Sick leave`.
- **` > ` inside a heading is ambiguous.** A heading such as `a > b` cannot be
  told apart from two nested headings when reading the path back.
- **A heading at the very end stands alone.** A last section of only a heading
  becomes a small chunk; the minimum cannot be met when nothing follows.
- **The fallback can split a paragraph that would fit alone,** when a chunk
  below the minimum comes right before it. That is the price of keeping a
  heading with its text.

The fixtures hold only synthetic text. No credential or private content
entered any artifact, and the public types name no provider.

## Verification

Final run on 2026-10-20 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_structured_chunking.py tests/test_chunking.py -q` | 39 passed |
| `make format-check lint typecheck` | 97 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 480 passed, 20 deselected |
| `make check` | 481 passed, 17 skipped, 2 deselected; total coverage 86%; `chunking.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held, including the three overlapping windows `w0`–`w3`,
`w3`–`w6`, and `w6`–`w9`, and a 1,000-word heading whose chunks' paths are at
most 100 characters. Along the way:

- After the refactor, `tests/test_chunking.py` still gave 23 passed before any
  new test existed, confirming `chunk_document` was unchanged.
- The golden file generated with `UPDATE_GOLDEN=1` matched the lesson's
  expected file exactly.
- The first format check failed on one extra blank line before
  `token_spans` in `chunking.py`; `make format` fixed it.

## Reflection

1. **Why cutting at headings is usually better.** A question about vacation
   should retrieve the vacation rules. With fixed windows, the vacation rules
   can be split across two chunks, or share a chunk with the end of another
   section, so neither chunk matches the question strongly and the evidence
   shown to the model is half about something else. Structure-aware chunks
   hold one section, start with its heading, and carry the path
   `Team handbook > Vacation`, so the match is stronger and the citation is
   clearer.
2. **Why fixed windows are still needed.** A single paragraph can be longer
   than any chunk may be, because the embedding model has a limit. No block
   boundary exists inside it, so the only way to cut it is by token count,
   with an overlap so a sentence cut in two appears whole in one window.
3. **What the golden test protects.** It pins the whole output for a realistic
   document: every boundary, token count, block range, and heading path at
   once. The other tests each check one rule; the golden test notices any
   change at all, including an unintended one. The file must be reviewed
   after `UPDATE_GOLDEN=1` because the update simply writes whatever the code
   produces now: if the code is wrong, the golden file becomes wrong too, and
   the test would then protect the bug.
4. **Why the heading paths grew so fast.** A heading larger than the maximum
   was cut into windows, and every window carried the whole heading as its
   path. Doubling the heading doubled both the number of windows and the
   length of each path, so the total grew four times. Cutting each title to
   `MAX_TITLE_CHARACTERS` in `_heading_paths`
   (`" ".join(block.text.split())[:MAX_TITLE_CHARACTERS]`) made every path
   short, so the total now grows only with the number of chunks.
5. **What complexity `chunk_by_structure` hides from its caller.** The caller
   passes a parsed document and gets ordered chunks with paths. It never
   tracks heading levels, decides when a section is too small, finds a
   block's tokens, falls back to windows, or keeps a heading with its text.
6. **Which alternative would make the next change harder.** Writing a second,
   separate window loop for the fallback would leave two copies of the same
   subtle logic, such as how the next window starts and when to stop, which
   could drift apart. Sharing `_windows` keeps one tested loop for both
   chunkers.
7. **What would escape without the negative tests.** Without
   `test_impossible_budgets_are_refused`, a change that dropped the budget
   check would accept an overlap equal to the maximum, and the fallback loop
   would never move forward on an oversized block, hanging the worker instead
   of failing with a clear `InvalidChunking`.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
