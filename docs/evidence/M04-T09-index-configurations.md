# M04-T09 — Index configuration evidence

## Invariant

Every setting that shapes a stored vector (the embedding model and its
dimensions, the chunker version and token budgets, the normalization
version, every parser version, the media type aliases, the Unicode data
version, the distance metric, and the vector scaling) is part of one
immutable index configuration. Its fingerprint is a SHA-256 of one canonical
form: equal settings, in any order, always give the same fingerprint and the
same stored identity; changing any one setting gives a new fingerprint and a
new identity. A stored row is never updated.

## Expected failing check

Before the change, the new test files fail to import
`knowledge_service.index_configuration`, and the migration drift test would
fail once `index_configurations` is declared without its migration.

Predictions once it exists:

- Storing the current configuration twice: one row, the same identity.
- Raising `max_tokens` by one, changing the model, the dimensions, the
  normalization version, or the vector scaling: a second row and a new
  identity.
- The same parsers listed in reverse order: the same identity.
- Four callers storing one new configuration at once: one row.
- A stored row edited by hand: `IndexConfigurationConflict`.
- `min_tokens=True`, `dimensions=16001`, or a NUL in a version:
  `InvalidIndexConfiguration`.

## Design

Completed: 2026-10-27

Changed files:

- `src/knowledge_service/chunking.py`: `CHUNKER_VERSION`.
- `src/knowledge_service/normalization.py`: `NORMALIZATION_VERSION`,
  `parser_versions()`, `media_type_aliases()`.
- `src/knowledge_service/embeddings.py`: `DistanceMetric` (moved from
  `vector_search.py` to avoid an import cycle), `VECTOR_SCALING`, and a type
  check for the provider and model of `EmbeddingModelIdentity`.
- `src/knowledge_service/fingerprints.py`: `_hash` is now the public
  `canonical_hash`.
- `src/knowledge_service/identifiers.py`: `IndexConfigurationId`.
- New `src/knowledge_service/index_configuration.py`: `ChunkerSettings`,
  `NormalizationSettings`, `IndexConfiguration`, `settings_fingerprint`,
  `current_index_configuration`, `InvalidIndexConfiguration`, and
  `IndexConfigurationConflict`.
- `src/knowledge_service/persistence.py`: the `index_configurations` table
  and `ensure_index_configuration`.
- New migration `a5b6c7d8e9f0_index_configurations.py`.
- `tests/test_embeddings.py`, new `tests/test_index_configuration.py`
  (60 tests) and `tests/test_index_configuration_integration.py` (13 tests),
  counting parametrized cases.
- `docs/architecture.md`: the table's rule.

What a configuration holds, and why each part shapes a stored vector:

| Setting | Why it matters |
|---|---|
| Provider, model, dimensions | Another model or length gives incomparable vectors. |
| Chunker version, `min_tokens`, `max_tokens`, `overlap_tokens` | They decide where Chunks are cut, so which text is embedded. `reconcile_document` uses the chunker's defaults, and a test spies on that call. |
| Normalization version, parser versions, media type aliases | They decide the text and blocks a Source Record becomes. A library upgrade, such as `pypdf`, changes the parser version. |
| Unicode data version | Tokens and dropped characters follow Python's Unicode data. |
| Distance metric, vector scaling | How stored vectors are scaled and compared. |

`fingerprint` is `canonical_hash` of the settings under the label
`knowledge-service/index-configuration/v1`. `ensure_index_configuration`
re-checks the configuration, inserts it with `ON CONFLICT (fingerprint) DO
NOTHING`, and otherwise returns the stored row's ID after checking that the
stored settings equal the given ones and hash to the fingerprint. Rows are
never updated.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_index_configuration.py -q` | 60 passed |
| `uv run pytest tests/test_index_configuration_integration.py tests/test_migrations_integration.py -q` | 17 passed |
| `uv run pytest -m "not integration and not live" -q` | 817 passed, 166 deselected |
| `make test-integration` (same `pytest -m "integration and not live"`) | 163 passed, 820 deselected |
| `make format-check lint typecheck` | 135 files formatted; all checks passed; 0 errors |
| `make check` | 980 passed, 3 deselected; coverage 94%, `index_configuration.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

Deliberate breaks, each undone afterwards:

| Break | Failed |
|---|---|
| `unicode_version` left out of `settings()` | `test_the_fingerprint_of_known_settings_never_changes`, the `unicode version` case of `test_every_changed_setting_changes_the_fingerprint` |
| No `ON CONFLICT DO NOTHING` | `test_identical_settings_reuse_the_stored_identity` and every case of `test_a_changed_setting_creates_a_new_identity` |
| Reconciling calls the chunker with `max_tokens=512` | `test_the_chunker_settings_are_the_ones_reconciling_uses` |
| The chunker's `max_tokens` default changed to 300 | `test_the_chunker_settings_are_the_ones_reconciling_uses` |

Every prediction above held, as each case has its own test.

Adversarial review: an independent review attacked the first version on
the real database. Its breaks, each fixed with a regression test that fails
when the fix is removed:

- NUL or lone surrogates in text settings reached PostgreSQL as a raw
  `DataError`; NFC and NFD spellings, or a trailing zero-width space, looked
  equal but hashed differently. Text must now be visible and in NFC form.
- Unbounded integers: `10**5000` crashed the fingerprint; `dimensions=10**30`
  was stored. Dimensions are now 1–16000 and token budgets at most 100000.
- `EmbeddingModelIdentity(123, ...)` crashed with `AttributeError`.
- `MAX_TOKEN_CHARACTERS` and `BLOCK_SEPARATOR` were not covered; a test now
  pins them to `CHUNKER_VERSION`. The Unicode data version was missing.
- A stored `8.0` for `8` went unnoticed; the stored settings are now hashed
  again. Aliases could point at unparsed types. A configuration altered with
  `object.__setattr__` was stored unchecked; it is now rebuilt first.

The review confirmed that order independence, exact type checks, the JSONB
round trip, 30 concurrent callers over 3 configurations (3 rows), typed
conflicts for tampered rows, and the migration all held.

Finish review: the Spec review found nothing blocking. The Standards review
found one blocking issue: `test_the_chunker_settings_match_what_reconciling_uses`
compared the configuration with the constants it was built from, so it
could not fail. Claude, who had written it, replaced it with
`test_the_chunker_settings_are_the_ones_reconciling_uses`, which spies on
the chunker call in `build_chunks` and reads the chunker's defaults; both
drifts above now fail it.

Telemetry: nothing new is logged, and a configuration holds only settings,
never Document text.

Risks and notes:

- `CHUNKER_VERSION` and `NORMALIZATION_VERSION` must be raised by hand when
  logic changes without a number to show it, such as `token_spans` or the
  HTML and text parsers. Only two chunker constants are pinned by a test.
- `text/html` uses Python's own `html.parser`; only the Unicode data
  version is recorded, not the Python version.
- Nothing stores or uses a configuration yet; M04-T10 links them to
  Embedding Spaces.
- Non-blocking suggestions not applied: add "Index Configuration" to
  `CONTEXT.md` (fits M04-T10); share `MAX_DIMENSIONS` with the
  `embedding_spaces` check; comment `MAX_CHUNK_TOKENS`; drop the dead
  `Mapping` branch in `_sorted_pairs` and the checks `EmbeddingModelIdentity`
  already makes; say in the pin test why `HEADING_SEPARATOR` is left out
  (heading paths are not embedded).

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **Why 257 tokens is a new configuration.** The chunker would cut the
   text in other places, so the Chunks, and the texts sent to the model,
   would differ. Vectors of the old Chunks would then be searched alongside
   or instead of vectors of Chunks that no longer exist.
2. **Why store the settings too.** The fingerprint only says whether two
   configurations are equal. The stored settings say what they are, so a
   person or M04-T10 can see what changed, and the stored JSON can be hashed
   again to prove it was not edited.
3. **A forgotten `CHUNKER_VERSION` bump.** The pin test notices only if
   `MAX_TOKEN_CHARACTERS` or `BLOCK_SEPARATOR` changed; a change inside
   `chunk_by_structure` passes every test. Old and new Chunks would then
   share one configuration, and search would mix vectors of differently cut
   text without anyone knowing to re-embed.
4. **Four callers at once.** The first insert takes the unique fingerprint.
   The others wait for its transaction, then do nothing because the row
   exists; their next statement reads the committed row and returns its ID.
5. **Too little versus too much.** Recording too little leaves stale vectors
   that quietly lower search quality. Recording too much, such as every
   Python patch version, would re-embed everything after each upgrade, which
   costs money and time for no change in the vectors.
