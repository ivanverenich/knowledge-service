# M03-T09 — Fixed-window chunking evidence

Completed: 2026-10-20

## Invariant

The same parsed document and settings always give the same chunks, in order,
with ordinals from 0. Every chunk holds between 1 and `window_tokens` tokens,
neighbouring chunks share exactly `overlap_tokens` tokens, and every token is
in at least one chunk. `reading_text(parsed)[chunk.start:chunk.end]` is
exactly the chunk's text, and `first_block` and `last_block` are the blocks
holding its first and last character. No token is longer than 32 characters,
and no token ever contains whitespace.

## Prediction

Written before the chunker and its tests exist. This is what I expect for
each case.

- **A 6-token sentence with a window of 8.** One chunk holding all 6 tokens:
  the text is shorter than one window.
- **Exactly 8 tokens with a window of 8.** One chunk of 8 tokens. The window
  reaches the last token, so no second chunk is started.
- **9 tokens with a window of 8 and an overlap of 2.** Two chunks: `w0` to
  `w7`, then `w6`, `w7`, `w8`. The second starts 2 tokens back so the two
  share exactly 2 tokens, and it is shorter than a full window.
- **1,000 tokens with a window of 64 and an overlap of 8.** Each new chunk
  starts 56 tokens after the previous one, so there are 18 chunks. The first
  17 hold 64 tokens each, and the last holds the remaining 48, ending with
  `w999`.
- **A single 1,000-letter word.** It is split into tokens of at most 32
  characters: 31 tokens of 32 letters and one of 8, so 32 tokens. With the
  default window of 256, that is one chunk.
- **Ukrainian text with an accented letter and an emoji.** Each Ukrainian word
  is one token, punctuation marks are separate tokens, the accent stays in the
  same token as its letter, and an emoji joined with a zero-width joiner, such
  as a woman at a laptop, stays one token.
- **An empty document.** No chunks at all, rather than an error.
- **A two-page PDF.** Each chunk names its first and last block, and those
  blocks carry page numbers. With a window of 5 and an overlap of 1, the first
  chunk runs from page 1 into page 2, and the second lies on page 2 only.
- **A window of 8 with an overlap of 8.** `InvalidChunking`: an overlap as
  large as the window would never move forward.

## Design

Changed files:

- New `src/knowledge_service/chunking.py`: `reading_text`, `token_spans`,
  `chunk_document`, `DraftChunk`, and `InvalidChunking`.
- New `tests/test_chunking.py` (23 tests, counting parametrized cases).

| Piece | Decision |
|---|---|
| What is chunked | The reading text: the blocks' texts joined by `"\n\n"`. For HTML, `parsed.text` is the raw page, so chunking it would put markup into chunks. |
| Token | A run of letters, digits, and underscores, or one other visible character. Combining marks, zero-width joiners, variation selectors, emoji skin tones, and flag tags stay with the character before them. No token is longer than 32 characters. |
| Window | At most `window_tokens` tokens (256 by default); each new window starts `overlap_tokens` (32) before the previous one ended. The loop stops as soon as a window reaches the last token, so no tail chunk only repeats the previous one. |
| Provenance | `start` and `end` come from the first and last token, so `reading_text(parsed)[start:end]` is exactly the chunk's text. `first_block` and `last_block` are found with `bisect_right` over the blocks' starting offsets, and lead to `block.start`, `block.end`, and `block.page`. |
| Typed failure | `InvalidChunking` for impossible settings (a window below 1, a negative overlap, or an overlap at least as large as the window, which would never move forward) and for an inconsistent `DraftChunk`. |

Decisions:

- **`DraftChunk`, not `chunks.Chunk`.** A stored Chunk needs a `ChunkId`, a
  `DocumentId`, a content version, and the content fingerprint, which exist
  only once M03-T12 reconciles a Document. `DraftChunk` already guarantees
  what `Chunk` checks (non-blank text, at least one token), so M03-T12 only
  adds identity.
- **A deterministic word tokenizer, not a model's.** A model tokenizer needs
  a large dependency, model files, or a download, and the embedding model is
  not chosen yet. Word tokens are offline, explainable, and stable.
- **The window loop.** For 9 tokens, a window of 8, and an overlap of 2, the
  chunks are `w0`–`w7` and then `w6`–`w8`: the next first token is
  `last + 1 - overlap`.

**Adversarial review.** An independent review checked every claim on 18
hand-picked inputs at five window and overlap settings and on 3,000 random
multi-block documents: reversibility, block indexes against a brute-force
search, no whitespace inside tokens, no overlapping tokens, every visible
character covered, exact overlap, and the chunk count
`ceil((n - overlap) / (window - overlap))`. All passed. Timing stayed linear:
about 0.3 seconds for 1,000,000 characters and 0.13 seconds with a window of
10,000. It found one break, now fixed with tests that failed against the
first version:

- A visible character after a zero-width joiner always joined the current
  token before the length cap was checked, and stacked accents had no cap at
  all. `"!\u200d" * 500_000` became one token, so a single one-token chunk
  held 1,000,000 characters. The length check now comes first, so every
  token, and every chunk of 256 tokens, stays bounded: the same input now
  gives chunks of at most 8,192 characters.

Two of its observations were also fixed: emoji skin-tone modifiers and the tag
characters in flag sequences now stay with their emoji. The tests were also
checked against deliberately broken versions: dropping the overlap failed
three tests, and computing `last_block` from the wrong offset failed the PDF
page test.

**Recorded notes:**

- **Word tokens are not model tokens.** A model usually counts more,
  especially for Ukrainian, so the window must leave room under the embedding
  model's limit once it is chosen.
- **Chunk size in characters is bounded by tokens, not by whitespace.** A
  chunk has at most `window_tokens` tokens of at most 32 characters, but long
  runs of spaces between tokens are kept, because a chunk must slice back
  exactly.
- **Some emoji still count as several tokens.** A flag is two
  regional-indicator characters and becomes two tokens, so a window edge
  could cut it in two.
- **Code blocks are chunked like prose.** Structure-aware chunking
  (M03-T10) can treat them differently.
- **Offsets index the reading text, not `parsed.text`.** M03-T12 must build a
  Chunk's `source_anchor` from the blocks, not from the chunk offsets.
- **A large overlap multiplies memory.** Each chunk copies its text, so an
  overlap close to the window size stores the text almost `window` times.

The fixtures hold only synthetic text. No credential or private content
entered any artifact, and the public types name no provider.

## Verification

Final run on 2026-10-20 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_chunking.py -q` | 23 passed |
| `make format-check lint typecheck` | 96 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 464 passed, 20 deselected |
| `make check` | 465 passed, 17 skipped, 2 deselected; total coverage 85%; `chunking.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held, including 18 chunks for 1,000 tokens with a window of
64 and an overlap of 8, the last holding 48 tokens, and 32 tokens for a single
1,000-letter word. Along the way:

- The first format check failed on `chunking.py`: `chunk_document`'s
  parameters were indented 8 spaces instead of 4, and the file had no final
  newline. `make format` fixed both without changing behaviour.
- Two typos in the `InvalidChunking` docstring (`Chucking`, `frafted`) were
  corrected.

## Reflection

1. **Why neighbouring chunks overlap.** A window cuts wherever it reaches its
   token limit, often in the middle of a sentence. With an overlap of 0, a
   sentence cut in two exists only as two halves in two chunks, and a question
   that matches the whole sentence may match neither half well enough to be
   retrieved. With an overlap, the end of one chunk is repeated at the start
   of the next, so a short sentence near a cut appears whole in at least one
   chunk.
2. **Why the chunker reads the blocks.** For HTML, `parsed.text` is the raw
   page with tags and attributes, which would be embedded and shown as
   evidence. The blocks hold the clean text. A chunk still leads back to the
   original: its `start` and `end` slice the reading text exactly, and its
   `first_block` and `last_block` lead to `block.start`, `block.end`, and, for
   a PDF, `block.page` in the source.
3. **What the million-character token would have done.** A chunk is limited
   by its token count, so a single huge "token" made a 1-token chunk of a
   million characters. An embedding model would truncate it, so most of the
   text would never be searchable, or reject it, failing the document's
   indexing. Moving the length check, `index - start >= MAX_TOKEN_CHARACTERS`,
   before the check for attached characters in `_continues` fixed it.
4. **A test whose removal would let a real bug through.**
   `test_every_chunking_is_reversible_bounded_and_complete`. It checks every
   invariant on random text, window, and overlap. Without it, an off-by-one in
   the window loop, such as dropping or repeating one token at a cut, could
   pass the hand-picked tests and silently lose text from the index or break
   citation offsets.
5. **What complexity the chunker hides from its caller.** The caller passes a
   parsed document and gets ordered drafts. It never decides what a token is,
   keeps accents and emoji whole, computes overlaps, avoids a redundant tail
   chunk, or maps character offsets back to blocks and pages.
6. **Which alternative would make the next change harder.** Chunking
   `parsed.text` directly would mix markup into chunks and give offsets that
   only work for some formats. Structure-aware chunking (M03-T10) cuts at
   block boundaries and uses fixed windows as its fallback for oversized
   sections, so it needs the chunker to work on blocks and report block
   ranges, which this design already does.
7. **What would escape without the negative tests.** Without
   `test_impossible_settings_are_refused`, a change that dropped the settings
   check would let an overlap equal to the window through: the loop would
   start every new window where the last one started, never reach the end, and
   hang the synchronization worker on the first document instead of failing
   with a clear `InvalidChunking`.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
