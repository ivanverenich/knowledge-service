# M03-T08 — Content and authorization fingerprints evidence

Completed: 2026-10-20

## Invariant

The content fingerprint depends only on the parsed text and blocks, and the
authorization fingerprint depends only on the set of readers. Equal inputs
always give equal fingerprints, in any process and on any run. Grant order,
grant IDs, and grant times never change the authorization fingerprint. Any
change the chunker can see, in the text or in any block field, changes the
content fingerprint. The two kinds of fingerprint can never be equal to each
other, because each hash starts from its own label.

## Prediction

Written before the fingerprint module and its tests exist. This is what I
expect for each case.

- **The same grants in two orders.** The same authorization fingerprint: the
  subjects are sorted before hashing, so order is lost on purpose.
- **The same readers granted again with new grant IDs and times.** The same
  fingerprint: only each grant's subject is hashed, not its ID or time.
- **One extra reader.** A different fingerprint, because the set of subjects
  is different.
- **No readers at all versus a public grant.** Two different fingerprints. An
  empty list means nobody may read the document, which is the opposite of
  everyone.
- **A user and a group that share a UUID.** Different fingerprints: the
  subject's kind is hashed together with its ID.
- **The same reader granted twice.** `InvalidGrant`, because a subject may be
  granted at most once per document.
- **A grant for another document.** `InvalidGrant`, because every grant must
  belong to the same document.
- **The same Markdown parsed twice.** The same content fingerprint, because
  parsing is deterministic and the canonical form is fixed.
- **One changed word.** A different content fingerprint: the block text and
  the parsed text both change.
- **A heading changed from `#` to `##`.** A different fingerprint, even though
  the heading's words are the same, because the heading level is hashed.
- **An extra blank line.** A different fingerprint: the visible words are the
  same, but the next block's offsets move, and offsets are hashed because
  citations point into the text.
- **`Document.reconcile`.** For an unchanged update it returns the same
  Document object. A content-only update raises the content version to 2 and
  keeps the authorization version at 1. An access-only update does the
  opposite.

## Design

Changed files:

- `pyproject.toml` and `uv.lock`: `hypothesis` added to the `dev` group with
  `uv add --dev hypothesis` (6.170.5, MPL-2.0, never shipped with the service).
- New `src/knowledge_service/fingerprints.py`: `content_fingerprint` and
  `authorization_fingerprint`.
- `src/knowledge_service/access.py`: `Subject.__post_init__` rejects a kind
  that is not a `SubjectKind`.
- New `tests/test_fingerprints.py` (16 tests, counting parametrized cases),
  and one new test in `tests/test_access.py` (7 in total).

| Fingerprint | Hashes | Ignores |
|---|---|---|
| `content_fingerprint(parsed)` | `parsed.text` and every block's kind, text, offsets, heading level, list depth, code language, links, and page, in order | title, media type, language, parser version |
| `authorization_fingerprint(document_id=..., grants=...)` | the sorted set of subjects, as kind plus serialized ID | grant order, grant IDs, grant times |

Decisions:

- **Two fingerprints, not one.** `Document.reconcile` already raises the
  content version and the authorization version independently, each when its
  own fingerprint changes. Hashing them apart is what lets M03-T12 update
  Access Grants without re-chunking, and re-chunk without touching grants.
- **Canonical JSON, then SHA-256.** `json.dumps` with sorted keys, no optional
  spaces, and `ensure_ascii=False` writes each value one way only, and JSON
  escaping keeps separators inside strings unambiguous.
- **A versioned label inside each hash**
  (`knowledge-service/content/v1`, `knowledge-service/authorization/v1`). The
  two kinds of fingerprint can never collide, and a deliberate change of the
  canonical form is made visible by bumping `v1`.
- **Normalized grants, not `SourceAccess`.** The task asks for normalized
  Access Grants. `granted_subjects` is reused, so a reader granted twice or a
  grant for another document raises `InvalidGrant`, the module's typed
  failure. An empty grant list is valid and means nobody may read the
  document; it has its own fingerprint.
- **Subjects sorted by kind, then serialized ID.** A user and a group that
  share a UUID stay different readers. `None`, the public subject's ID, sorts
  as an empty string.
- **Numbers written with `int(...)`, grants copied to a tuple, and
  `"surrogatepass"` when encoding.** These close three cases found by review:
  blocks with `True`/`False` offsets equal blocks with `1`/`0` but hashed
  differently; a generator passed as `grants` would be used up by validation
  and silently hash as "nobody"; and a lone surrogate in parsed text would
  make plain UTF-8 encoding raise.
- **Everything the chunker can see is content, and nothing else.** The page
  number is included: a PDF paragraph that moves to another page with the
  same text must be re-chunked, or citations would name the old page. Title,
  media type, language, and parser version are left out, so a parser upgrade
  that produces identical blocks re-indexes nothing.
- **Property tests run deterministically.** `settings(derandomize=True,
  database=None, deadline=None)` makes Hypothesis generate the same examples
  on every run. Hypothesis still writes a small `.hypothesis/` cache, which
  carries its own `.gitignore`.

**Adversarial review.** An independent review checked collisions (user versus
group with one UUID, public versus nobody, `None` versus `""`, separators
inside link text, a surrogate pair versus the real character), ran the same
inputs under five `PYTHONHASHSEED` values in separate processes, and timed
large inputs. Nothing collided and every process gave the same fingerprints.
It found two minor breaks, both now fixed with tests that failed against the
first version:

- `StructuralBlock(..., start=False, end=True)` equals the block with `0` and
  `1`, but JSON wrote `false`/`true`, so equal documents hashed differently.
- `Subject("group", UserId.new())` passed validation, because a plain string
  never matches `is SubjectKind.GROUP`, and the fingerprint then raised
  `AttributeError` instead of `InvalidGrant`.

The property tests were also checked against deliberately broken versions: a
version that hashed grants in input order failed the ordering test, and a
version that hashed grant IDs failed the grant-ID test and the drift test.

**Recorded notes:**

- **HTML markup changes count as content changes.** For HTML, `parsed.text`
  is the raw page, and block offsets point into it. Changing
  `<p class="a">` to `class="b"` gives identical blocks but a new
  fingerprint, so the page is re-chunked. This keeps every citation offset
  valid, at the price of re-indexing on cosmetic edits.
- **Cost.** One million characters hash in about 0.01 seconds and 10,000
  grants in about 0.03 seconds. 100,000 blocks take about 1.3 seconds and 62
  MB, because the whole canonical form is built in memory before hashing.
- **If the title ever goes into chunk text or embeddings, it must join the
  content fingerprint.**

The fixtures hold only synthetic identifiers and text. No credential or
private content entered any artifact, and the public functions name no
provider.

## Verification

Final run on 2026-10-20 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_fingerprints.py tests/test_access.py -q` | 23 passed |
| `make format-check lint typecheck` | 94 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 441 passed, 20 deselected |
| `make check` | 442 passed, 17 skipped, 2 deselected; total coverage 85%; `fingerprints.py` fully covered |
| `make docs-check` | passed |
| `uv lock --check` | passed (64 packages resolved) |
| `git diff --check` | clean |

Each prediction held. Along the way:

- `test_the_canonical_form_does_not_drift` failed on the first run: the block
  canonical form had no `"page"` entry, so the content fingerprint was
  `7e0a8fbf…` instead of the pinned `9f9a307c…`. Adding
  `"page": _number(block.page)` restored the pinned value. The drift test
  caught a change no other test would have noticed.
- The first lint run failed on `fingerprints.py`: a signature line of 96
  characters and no final newline. `make format` fixed both.

## Reflection

1. **Why two fingerprints instead of one hash of everything.** With one hash,
   any change looks the same. If one user is removed from a large document's
   readers, a single hash changes and the service cannot tell that the text
   is untouched, so it would re-chunk and re-embed the whole document, which
   is slow and costs embedding calls, just to change who may read it. Worse,
   removing access is the urgent case, and it would wait behind that work.
   With two fingerprints, only the authorization version rises and only the
   grants are rewritten.
2. **Why order, IDs, and times must not count.** They are bookkeeping, not
   permission. Sources can return grants in any order, and building grants
   again gives them new IDs and times. If any of these counted, every
   Synchronization Run would see a new authorization fingerprint for every
   document, raise every authorization version, and rewrite every grant,
   while nobody's access had changed. Real access changes would also be lost
   in that noise.
3. **Why offsets and raw HTML text are included, and the cost.** Chunks and
   citations point into `parsed.text` by offset. If the offsets moved but the
   fingerprint stayed the same, the stored chunks would keep old offsets, and
   citations would highlight the wrong text. Including them keeps every
   offset valid. The cost is that a cosmetic HTML change, such as a CSS class,
   moves no visible word but still forces the page to be re-chunked.
4. **What the drift test protects.** It pins the exact fingerprint of two
   small fixtures. If the canonical form changes by accident, for example a
   renamed key, a missing field, or different JSON settings, every stored
   fingerprint stops matching, and the next run would treat every document as
   changed and re-index everything. Nothing would crash, so without this test
   the first sign would be a huge, unexplained re-index. It caught exactly
   that during this task, when the `page` entry was missing.
5. **What complexity the fingerprints hide from their caller.** The caller
   passes a parsed document or a list of grants and gets a string. It never
   decides which fields count, how to sort subjects, how to write JSON one
   way, how to keep the two kinds apart, or how to survive broken Unicode.
6. **Which alternative would make the next change harder.** Fingerprinting
   `SourceAccess` principal strings instead of normalized grants would tie
   the fingerprint to each provider's spelling of users and groups. When
   Confluence arrives, or when identity mapping changes how a principal is
   written, every authorization fingerprint would change without any real
   access change.
7. **What would escape without the negative tests.** Without
   `test_the_same_reader_granted_twice_is_refused` and
   `test_a_grant_for_another_document_is_refused`, a change that skipped
   `granted_subjects` would still pass every other test. Then a grant copied
   from another document would be fingerprinted and stored as if it belonged
   here, quietly giving its readers access to the wrong document, and a
   duplicated grant would make the same readers hash differently from a
   clean list, raising the authorization version for no reason.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
