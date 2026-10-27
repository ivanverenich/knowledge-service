# M04-T09 — Version index configurations

Source task: [M04-T09](../curriculum/milestones/M04-dense-retrieval/M04-T09-version-index-configurations.md)

## What you will do

A stored vector depends on more than the embedding model. The same Source Record goes through normalization (bytes to text and blocks), then the chunker (blocks to Chunks), then the model, then scaling to length 1, all for one distance measure. Change any step and the new vectors are no longer comparable with the old ones. You will write all of those settings down as one unchangeable value, an `IndexConfiguration`, give it a fingerprint, and store it so that the same settings always get the same identity and any change gets a new one. M04-T10 uses this to switch models safely.

| Requirement | How this lesson meets it |
|---|---|
| Fingerprint model, dimensions, chunker, normalization, and relevant parameters | `IndexConfiguration` holds the model identity, `ChunkerSettings` (version and token budgets), `NormalizationSettings` (version, every parser version, media type aliases, Unicode data version), the distance metric, and the vector scaling. `fingerprint` is a SHA-256 of one canonical form of them. |
| Immutable | Frozen dataclasses, checked on creation and checked again before storing; table rows are only inserted, never updated. |
| A changed setting creates a new configuration | `ensure_index_configuration` stores a new row for a new fingerprint; a test changes each setting in turn. |
| Identical settings reuse the existing identity | The same fingerprint returns the stored row's ID, also when settings are listed in another order or several callers store it at once. |
| Typed failure | `InvalidIndexConfiguration` for bad settings; `IndexConfigurationConflict` when a stored row's settings no longer match its fingerprint. |

This lesson includes coding: Steps 2–6 change code or docs, while Steps 1 and 7 do not.

Terms used in this lesson:

- **Index configuration:** every setting that decides what a stored vector looks like.
- **Fingerprint:** a SHA-256 hash of a value written in one fixed way, so equal values always give the same 64 hex characters and different values practically never do.
- **Canonical form:** that one fixed way to write the value: keys sorted, no spaces, the same JSON every time.
- **Idempotent:** doing it twice has the same effect as doing it once. Storing the same configuration twice gives one row.

## Before you begin

- **What already exists.** `fingerprints.py` has the canonical-form hash used for content and access fingerprints; it was private (`_hash`), and this lesson makes it public as `canonical_hash`, so all fingerprints are written the same way. `EmbeddingModelIdentity` (provider, model, dimensions) and the `embedding_spaces` table exist from M04-T01 and M04-T04.
- **What counts as a setting.** Only what changes the vectors. `reconcile_document` calls `chunk_by_structure` with its defaults (`MIN_TOKENS`, `MAX_TOKENS`, `OVERLAP_TOKENS`), and `embedding_publication.py` embeds only `chunk.text`. So the heading separator, title length, and `WINDOW_TOKENS` (used only by the baseline chunker) are left out. Code changes that have no number of their own are covered by version labels you raise by hand: the new `CHUNKER_VERSION` and `NORMALIZATION_VERSION`.
- **Why the Unicode version.** Tokens are cut with `str.isalnum` and `unicodedata.category`, and normalization drops characters by category. Both follow the Unicode data built into Python, so a Python upgrade can change chunk text with no code change. `unicodedata.unidata_version` records it.
- **`DistanceMetric` moves.** It moves from `vector_search.py` to `embeddings.py`. The new module needs it, and `persistence.py` imports the new module; `vector_search.py` imports `persistence.py`, so leaving it there would make an import cycle. `vector_search.py` imports it back, so `from knowledge_service.vector_search import DistanceMetric` keeps working.
- **No wiring yet.** Nothing stores a configuration during publication or search in this lesson. M04-T10 links configurations to Embedding Spaces when it switches models.
- **What the adversarial review found.** An independent review attacked the first version against the real database. It confirmed these breaks, all fixed below with regression tests that each fail when their fix is removed:
  - A NUL or a lone surrogate in any text setting passed the checks and failed later in PostgreSQL with a raw `DataError`. Text must now hold only visible characters.
  - Two spellings of one word (NFC and NFD), or a trailing zero-width space, looked equal but gave different fingerprints. Text must now be in NFC form, with no invisible characters.
  - Integers had no upper bound: `dimensions=10**5000` crashed the fingerprint with a bare `ValueError`, and `dimensions=10**30` was stored although pgvector allows at most 16000. Dimensions are now 1 to 16000, token budgets at most 100000.
  - `EmbeddingModelIdentity(123, "m", 8)` crashed with `AttributeError`. It now raises `InvalidEmbedding`.
  - `MAX_TOKEN_CHARACTERS` and `BLOCK_SEPARATOR` change chunk text but were covered only by remembering to raise `CHUNKER_VERSION`. A test now pins them to the version, so changing one fails until the version is raised too.
  - The Unicode data version was missing; it is now a setting.
  - A stored row edited to hold `8.0` instead of `8` was not noticed, because `8.0 == 8` in Python. The stored settings are now hashed again and compared with the fingerprint.
  - An alias could point at a media type with no parser. Aliases are now checked against the parsers.
  - A configuration altered with `object.__setattr__` was stored without being checked. `ensure_index_configuration` now rebuilds it first.

  It also confirmed what held: order independence, exact type checks (subclasses, `bool` as an int), the JSONB round trip for large numbers and Unicode, 30 concurrent callers over 3 configurations giving exactly 3 rows, every kind of tampered row raising the typed conflict, and the migration going up and down.
- **Known limit.** `text/html` is parsed by Python's own `html.parser`, which records only `"html/1"`, not the Python version. A Python patch release that changes `html.parser` would not change the fingerprint; the Unicode version covers most, not all, of what a Python upgrade can change. Record it as a risk.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

Create `docs/evidence/M04-T09-index-configurations.md` before editing any code. Start it with this text, then adjust the wording to your own:

```markdown
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

## Verification

## Reflection
```

Check: the file exists, and the predictions say what you expect before you run anything.

### Step 2 — Coding step: name the versions and expose the settings

**In plain words:** Before the settings can be written down, each one needs a name the new module can read. This step adds version labels to the chunker and to normalization, lets normalization list its parsers, moves the distance measure next to the other vector settings, makes the existing hash function public, and adds an ID type for the new rows. Nothing behaves differently afterwards.

| Change | File | What it does |
|---|---|---|
| `CHUNKER_VERSION` | `chunking.py` | `"structure/1"`; raise it whenever the cutting rules change. |
| `NORMALIZATION_VERSION`, `parser_versions()`, `media_type_aliases()` | `normalization.py` | A label for normalization code, and copies of the private parser and alias tables. |
| `DistanceMetric`, `VECTOR_SCALING` | `embeddings.py` | The metric moves here from `vector_search.py`; `"unit_length/1"` names the scaling from M04-T07. |
| Provider type check | `embeddings.py` | `EmbeddingModelIdentity(123, ...)` raises `InvalidEmbedding`, not `AttributeError`. |
| `canonical_hash` | `fingerprints.py` | `_hash` renamed and made public; its two callers updated. |
| `IndexConfigurationId` | `identifiers.py` | The identity of a stored configuration. |

In `src/knowledge_service/chunking.py`, add before `WINDOW_TOKENS = 256`:

```python
# Raise when the cutting rules change, so vectors made under the old rules
# belong to another index configuration.
CHUNKER_VERSION = "structure/1"
```

In `src/knowledge_service/normalization.py`, add after `MAX_DETAIL_CHARACTERS = 200`:

```python
# Raise when normalization changes what a Document's text or blocks become,
# so vectors made from the old text belong to another index configuration.
NORMALIZATION_VERSION = "normalization/1"
```

and add these two functions just before `def normalize(`:

```python
def parser_versions() -> dict[str, str]:
    """Return the parser version recorded for each supported media type."""
    return {media_type: parser.version for media_type, parser in _PARSERS.items()}


def media_type_aliases() -> dict[str, str]:
    """Return the media types read as another, such as XHTML as HTML."""
    return dict(_MEDIA_TYPE_ALIASES)
```

In `src/knowledge_service/embeddings.py`, add the type check at the start of `EmbeddingModelIdentity.__post_init__`:

```python
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidEmbedding("provider and model must be strings")
```

and add after `type Vector = tuple[float, ...]`:

```python
class DistanceMetric(StrEnum):
    """How `distance` is measured; lower is always nearer."""

    COSINE = "cosine"


# Names the scaling `unit_length` applies to every stored vector and query.
VECTOR_SCALING = "unit_length/1"
```

In `src/knowledge_service/vector_search.py`, delete the `DistanceMetric` class and the `from enum import StrEnum` line, and import the metric instead:

```python
from knowledge_service.embeddings import (
    DistanceMetric,
    EmbeddingModelIdentity,
    Vector,
    unit_length,
)
```

In `src/knowledge_service/fingerprints.py`, rename `def _hash(` to `def canonical_hash(` and change its two calls, `return _hash(_CONTENT_LABEL, ...)` and `return _hash(_AUTHORIZATION_LABEL, ...)`, to `canonical_hash(...)`.

In `src/knowledge_service/identifiers.py`, add before `class JobId`:

```python
class IndexConfigurationId(StableIdentifier):
    """Internal identity of an Index Configuration."""
```

Check: `uv run pyright` reports 0 errors, and `uv run pytest -m "not integration" -q` still passes.

### Step 3 — Coding step: the index configuration

**In plain words:** This file writes down every setting that decides what a stored vector looks like, as one value that cannot be changed, and turns it into a short fingerprint. Two configurations with the same settings, in any order, give the same fingerprint; changing any one setting gives a different one. It also builds the configuration the running code actually uses.

| Part | What it does |
|---|---|
| `ChunkerSettings` | `version`, `min_tokens`, `max_tokens`, `overlap_tokens`; exact `int`s with `1 <= min <= max <= 100000` and `0 <= overlap < max`. |
| `NormalizationSettings` | `version`, `parsers` and `aliases` as tuples of text pairs (kept sorted), and `unicode_version`. Every alias must point from an unparsed type to a parsed one. |
| `IndexConfiguration` | The model identity (dimensions 1 to 16000), the two settings objects, the `DistanceMetric`, and the vector scaling. |
| `settings()` / `fingerprint` | Plain JSON values in one fixed shape, and their `canonical_hash` under the label `knowledge-service/index-configuration/v1`. |
| `checked()` | Rebuilds the whole value, so every check runs again. |
| `settings_fingerprint` | Fingerprints settings read back from the database. |
| `current_index_configuration(model)` | The configuration this code runs with, from the constants and functions of Step 2. |
| `_check_text` | Text must be a trimmed, non-empty `str` of at most 200 characters, with no control, format, or surrogate characters, in Unicode NFC form. |

Why the sorting: `parser_versions()` returns a dict in insertion order; sorting the pairs means the order they are listed in can never change the fingerprint. Why the NFC check: `"café"` can be written with one character or two, which look the same but hash differently.

Create `src/knowledge_service/index_configuration.py`:

```python
"""Name every setting that shapes a stored vector, and fingerprint them.

Two vectors can be compared only if the same text went through the same
steps: the same normalization turned a Source Record into blocks, the same
chunker cut the blocks into Chunks, and the same model, at the same
dimensions, embedded them, scaled the same way, for the same distance. An
`IndexConfiguration` holds all of those settings as one immutable value.

Its `fingerprint` is a SHA-256 hash of one canonical form of the settings,
so equal settings always give the same fingerprint, whatever order they were
listed in, and changing any one of them gives a different one. Storing a
configuration (see `persistence.ensure_index_configuration`) reuses the row
of an equal configuration and creates a row for a changed one.
"""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from knowledge_service.chunking import (
    CHUNKER_VERSION,
    MAX_TOKENS,
    MIN_TOKENS,
    OVERLAP_TOKENS,
)
from knowledge_service.embeddings import (
    VECTOR_SCALING,
    DistanceMetric,
    EmbeddingModelIdentity,
)
from knowledge_service.fingerprints import canonical_hash
from knowledge_service.normalization import (
    NORMALIZATION_VERSION,
    media_type_aliases,
    parser_versions,
)

_LABEL = "knowledge-service/index-configuration/v1"
# Bounds the settings one configuration may list.
MAX_SETTING_ENTRIES = 100
MAX_SETTING_CHARACTERS = 200
# pgvector stores at most 16000 dimensions; the embedding spaces table agrees.
MAX_DIMENSIONS = 16_000
MAX_CHUNK_TOKENS = 100_000
# Control, format (zero-width, bidirectional), and surrogate characters.
_INVISIBLE = frozenset({"Cc", "Cf", "Cs"})


class InvalidIndexConfiguration(ValueError):
    """A setting is missing, of the wrong type, or out of range."""


class IndexConfigurationConflict(Exception):
    """A stored configuration has this fingerprint but other settings."""


@dataclass(frozen=True, slots=True)
class ChunkerSettings:
    """The rules and token budgets that cut a Document into Chunks."""

    version: str
    min_tokens: int
    max_tokens: int
    overlap_tokens: int

    def __post_init__(self) -> None:
        _check_text(self.version, "chunker version")
        for name in ("min_tokens", "max_tokens", "overlap_tokens"):
            # bool is an int, so `True` would otherwise count as 1.
            if type(getattr(self, name)) is not int:
                raise InvalidIndexConfiguration(f"{name} must be an int")
        if not 1 <= self.min_tokens <= self.max_tokens <= MAX_CHUNK_TOKENS:
            raise InvalidIndexConfiguration(
                f"token budgets need 1 <= min <= max <= {MAX_CHUNK_TOKENS}"
            )
        if not 0 <= self.overlap_tokens < self.max_tokens:
            raise InvalidIndexConfiguration("overlap needs 0 <= overlap < max")


@dataclass(frozen=True, slots=True)
class NormalizationSettings:
    """How Source Records become text: the code's version and every parser.

    `parsers` maps each media type to its parser version; `aliases` maps a
    media type to the one it is read as. Both are kept sorted, so the order
    they were given in never changes the fingerprint. `unicode_version` is
    the Unicode data Python classifies characters by: tokens and the
    characters normalization drops both depend on it.
    """

    version: str
    parsers: tuple[tuple[str, str], ...]
    aliases: tuple[tuple[str, str], ...]
    unicode_version: str

    def __post_init__(self) -> None:
        _check_text(self.version, "normalization version")
        _check_text(self.unicode_version, "unicode version")
        object.__setattr__(self, "parsers", _sorted_pairs(self.parsers, "parsers"))
        object.__setattr__(self, "aliases", _sorted_pairs(self.aliases, "aliases"))
        if not self.parsers:
            raise InvalidIndexConfiguration("at least one parser is needed")
        parsed = {media_type for media_type, _ in self.parsers}
        for alias, target in self.aliases:
            if alias in parsed or target not in parsed:
                raise InvalidIndexConfiguration(
                    "an alias must name another media type, read by a parser"
                )


@dataclass(frozen=True, slots=True)
class IndexConfiguration:
    """Every setting that shapes a stored vector, as one immutable value."""

    embedding: EmbeddingModelIdentity
    chunker: ChunkerSettings
    normalization: NormalizationSettings
    distance: DistanceMetric
    vector_scaling: str

    def __post_init__(self) -> None:
        if type(self.embedding) is not EmbeddingModelIdentity:
            raise InvalidIndexConfiguration("embedding must be a model identity")
        _check_text(self.embedding.provider, "provider")
        _check_text(self.embedding.model, "model")
        if not 1 <= self.embedding.dimensions <= MAX_DIMENSIONS:
            raise InvalidIndexConfiguration(
                f"dimensions must be between 1 and {MAX_DIMENSIONS}"
            )
        if type(self.chunker) is not ChunkerSettings:
            raise InvalidIndexConfiguration("chunker must be ChunkerSettings")
        if type(self.normalization) is not NormalizationSettings:
            raise InvalidIndexConfiguration(
                "normalization must be NormalizationSettings"
            )
        if type(self.distance) is not DistanceMetric:
            raise InvalidIndexConfiguration("distance must be a DistanceMetric")
        _check_text(self.vector_scaling, "vector scaling")

    def settings(self) -> dict[str, object]:
        """Return the settings as plain JSON values, in one fixed shape."""
        return {
            "embedding": {
                "provider": self.embedding.provider,
                "model": self.embedding.model,
                "dimensions": self.embedding.dimensions,
            },
            "chunker": {
                "version": self.chunker.version,
                "min_tokens": self.chunker.min_tokens,
                "max_tokens": self.chunker.max_tokens,
                "overlap_tokens": self.chunker.overlap_tokens,
            },
            "normalization": {
                "version": self.normalization.version,
                "parsers": dict(self.normalization.parsers),
                "aliases": dict(self.normalization.aliases),
                "unicode_version": self.normalization.unicode_version,
            },
            "distance": self.distance.value,
            "vector_scaling": self.vector_scaling,
        }

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the canonical settings, as 64 lowercase hex digits."""
        return settings_fingerprint(self.settings())

    def checked(self) -> IndexConfiguration:
        """Return the configuration rebuilt, so every check runs again.

        A frozen dataclass can still be changed with `object.__setattr__`;
        rebuilding catches a value changed after it was made.
        """
        return IndexConfiguration(
            embedding=EmbeddingModelIdentity(
                provider=self.embedding.provider,
                model=self.embedding.model,
                dimensions=self.embedding.dimensions,
            ),
            chunker=ChunkerSettings(
                version=self.chunker.version,
                min_tokens=self.chunker.min_tokens,
                max_tokens=self.chunker.max_tokens,
                overlap_tokens=self.chunker.overlap_tokens,
            ),
            normalization=NormalizationSettings(
                version=self.normalization.version,
                parsers=self.normalization.parsers,
                aliases=self.normalization.aliases,
                unicode_version=self.normalization.unicode_version,
            ),
            distance=self.distance,
            vector_scaling=self.vector_scaling,
        )


def settings_fingerprint(settings: object) -> str:
    """Fingerprint settings in their canonical form, such as stored JSON."""
    return canonical_hash(_LABEL, settings)


def current_index_configuration(
    embedding: EmbeddingModelIdentity,
) -> IndexConfiguration:
    """Return the configuration this code uses with the given model.

    The chunker and normalization settings come from the constants the code
    runs with, so changing one of them, or upgrading a parser library,
    changes the fingerprint.
    """
    return IndexConfiguration(
        embedding=embedding,
        chunker=ChunkerSettings(
            version=CHUNKER_VERSION,
            min_tokens=MIN_TOKENS,
            max_tokens=MAX_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
        ),
        normalization=NormalizationSettings(
            version=NORMALIZATION_VERSION,
            parsers=tuple(parser_versions().items()),
            aliases=tuple(media_type_aliases().items()),
            unicode_version=unicodedata.unidata_version,
        ),
        distance=DistanceMetric.COSINE,
        vector_scaling=VECTOR_SCALING,
    )


def _check_text(value: object, name: str) -> None:
    """Refuse text that could look equal to other text but hash differently."""
    if type(value) is not str:
        raise InvalidIndexConfiguration(f"{name} must be a string")
    if len(value) > MAX_SETTING_CHARACTERS:
        raise InvalidIndexConfiguration(
            f"{name} must be at most {MAX_SETTING_CHARACTERS} characters"
        )
    if not value.strip() or value != value.strip():
        raise InvalidIndexConfiguration(f"{name} must be trimmed and not empty")
    if any(unicodedata.category(character) in _INVISIBLE for character in value):
        raise InvalidIndexConfiguration(f"{name} must hold only visible characters")
    # NFC and NFD spellings of one word look the same but hash differently.
    if not unicodedata.is_normalized("NFC", value):
        raise InvalidIndexConfiguration(f"{name} must be in Unicode NFC form")


def _sorted_pairs(value: object, name: str) -> tuple[tuple[str, str], ...]:
    """Check a sequence of (key, value) text pairs and return it sorted."""
    if isinstance(value, Mapping) or type(value) is not tuple:
        raise InvalidIndexConfiguration(f"{name} must be a tuple of pairs")
    pairs = cast(tuple[object, ...], value)
    if len(pairs) > MAX_SETTING_ENTRIES:
        raise InvalidIndexConfiguration(
            f"{name} may list at most {MAX_SETTING_ENTRIES} entries"
        )
    checked: list[tuple[str, str]] = []
    for pair in pairs:
        if type(pair) is not tuple or len(cast(tuple[object, ...], pair)) != 2:
            raise InvalidIndexConfiguration(f"{name} must be a tuple of pairs")
        key, entry = cast(tuple[object, object], pair)
        _check_text(key, f"a key in {name}")
        _check_text(entry, f"a value in {name}")
        checked.append((cast(str, key), cast(str, entry)))
    keys = [key for key, _ in checked]
    if len(set(keys)) != len(keys):
        raise InvalidIndexConfiguration(f"{name} lists a key twice")
    return tuple(sorted(checked))
```

Check: `uv run pyright src/knowledge_service/index_configuration.py` reports 0 errors, and this prints a 64-character fingerprint:

```bash
uv run python -c "from knowledge_service.embeddings import EmbeddingModelIdentity as M; from knowledge_service.index_configuration import current_index_configuration as c; print(c(M('openai', 'text-embedding-3-small', 1536)).fingerprint)"
```

### Step 4 — Coding step: store configurations once

**In plain words:** This step saves configurations in the database so each one gets a lasting ID. Asking to save one that is already there just returns its ID, even when several callers ask at the same moment; a configuration with any setting changed gets a new row. If someone edited a stored row by hand, the code notices and refuses to use it.

| Part | What it does |
|---|---|
| `index_configurations` table | `configuration_id`, a unique `fingerprint` (checked to be 64 lowercase hex digits), the `settings` as JSONB, and `created_at`. No update function exists. |
| `ensure_index_configuration` | Re-checks the configuration, inserts it with `ON CONFLICT (fingerprint) DO NOTHING`, and returns the new ID; if a row already existed, reads it and returns its ID. |
| Conflict check | The stored settings must equal the given ones, and hashing the stored JSON must give the fingerprint. The second check catches `8.0` stored for `8`. |
| Migration | `migrations/versions/a5b6c7d8e9f0_index_configurations.py`, after `f4a5b6c7d8e9`. |

Why concurrent callers get one row: the second insert waits for the first transaction, then does nothing; its next statement sees the committed row. The unique fingerprint already serves as the index the lookup needs, so `tests/test_indexes.py` needs no change.

In `src/knowledge_service/persistence.py`, add `IndexConfigurationId,` to the `knowledge_service.identifiers` import, and add before the `knowledge_service.jobs` import:

```python
from knowledge_service.index_configuration import (
    IndexConfiguration,
    IndexConfigurationConflict,
    settings_fingerprint,
)
```

Add the table just before `access_grants = sa.Table(`:

```python
# One row per distinct set of settings that shapes stored vectors. Rows are
# only ever inserted: a changed setting is a new row, never an update.
index_configurations = sa.Table(
    "index_configurations",
    metadata,
    sa.Column("configuration_id", sa.Uuid(), primary_key=True),
    sa.Column("fingerprint", sa.Text(), nullable=False),
    sa.Column("settings", JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("fingerprint", name="uq_index_configurations_fingerprint"),
    sa.CheckConstraint(
        "fingerprint ~ '^[0-9a-f]{64}$'",
        name="ck_index_configurations_fingerprint",
    ),
)
```

Add the function just before `async def load_embedded_chunk_ids(`:

```python
async def ensure_index_configuration(
    connection: AsyncConnection,
    configuration: IndexConfiguration,
    *,
    created_at: datetime,
) -> IndexConfigurationId:
    """Return the identity of this configuration, storing it if it is new.

    Equal settings always return the identity stored first; any changed
    setting gives a new fingerprint and so a new row. Two callers storing the
    same new configuration at once get one row: the second insert does
    nothing and reads the first one's row. Raises
    `IndexConfigurationConflict` if a stored row has the fingerprint but not
    the settings, which would mean the row was edited. The configuration is
    checked again first, so one altered after it was made is refused.
    """
    configuration = configuration.checked()
    fingerprint = configuration.fingerprint
    settings = configuration.settings()
    inserted = await connection.scalar(
        insert(index_configurations)
        .values(
            configuration_id=IndexConfigurationId.new().value,
            fingerprint=fingerprint,
            settings=settings,
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[index_configurations.c.fingerprint])
        .returning(index_configurations.c.configuration_id)
    )
    if inserted is not None:
        return IndexConfigurationId(cast(UUID, inserted))
    stored = (
        await connection.execute(
            sa.select(
                index_configurations.c.configuration_id,
                index_configurations.c.settings,
            ).where(index_configurations.c.fingerprint == fingerprint)
        )
    ).one()
    # Hashing the stored JSON also catches 8.0 for 8 or true for 1, which
    # compare equal in Python but are other settings.
    if (
        stored.settings != settings
        or settings_fingerprint(stored.settings) != fingerprint
    ):
        raise IndexConfigurationConflict(
            "a stored configuration has this fingerprint but other settings"
        )
    return IndexConfigurationId(cast(UUID, stored.configuration_id))
```

Create the migration with the same ID as this guide, then replace the generated file's content:

```bash
uv run alembic revision -m "index configurations" --rev-id a5b6c7d8e9f0
```

```python
"""index configurations

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-27 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"


def upgrade() -> None:
    """Store each distinct set of settings that shapes stored vectors."""
    op.create_table(
        "index_configurations",
        sa.Column("configuration_id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_index_configurations_fingerprint"),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_index_configurations_fingerprint",
        ),
    )


def downgrade() -> None:
    """Drop the stored index configurations."""
    op.drop_table("index_configurations")
```

Check: `uv run alembic heads` shows `a5b6c7d8e9f0 (head)`, and `uv run pyright` reports 0 errors.

### Step 5 — Coding step: tests

**In plain words:** These tests prove the promise of the task: storing the same settings again gives the same ID, and changing any single setting gives a new one. Others check that the fingerprint of known settings never drifts, that bad or disguised settings are refused, and that the review's attacks stay fixed.

**`tests/test_embeddings.py`**: add `from typing import cast`, then add this case and its id to the table in `test_impossible_settings_are_refused`:

```python
        lambda: EmbeddingModelIdentity(
            provider=cast(str, 123), model="m", dimensions=4
        ),
```

with the id `"provider-not-text",` after `"no-dimensions",`.

**`tests/test_index_configuration.py`** (new, no database):

| Test | What it proves |
|---|---|
| `test_the_fingerprint_of_known_settings_never_changes` | A pinned fingerprint for fixed settings. If the canonical form drifts, every stored identity would change; this catches it. |
| `test_every_changed_setting_changes_the_fingerprint` | A table of 13 single changes, from provider to Unicode version. |
| `test_no_two_changes_share_a_fingerprint` | Those 13 changes give 13 different fingerprints. |
| `test_equal_settings_give_equal_fingerprints_in_any_order` | Reordered parsers: equal value and fingerprint. |
| `test_upgrading_a_parser_library_changes_the_current_fingerprint` | A new `pypdf` version changes the current configuration. |
| `test_the_chunker_rules_are_pinned_to_the_chunker_version` | Review regression: the text-shaping constants are pinned to `CHUNKER_VERSION`. |
| `test_the_chunker_settings_are_the_ones_reconciling_uses` | A spy on the chunker call inside `build_chunks` shows reconciling passes no budgets of its own, and the chunker's defaults equal the configuration's budgets. |
| Three invalid-input tables | `bool` and `float` counts, out-of-range budgets, huge numbers, NUL, surrogates, zero-width space, NFD text, bad pairs, bad aliases, wrong types. |
| `test_a_configuration_altered_after_it_was_made_is_refused_when_checked` | Review regression: `checked()` refuses a value changed with `object.__setattr__`. |

Two cases use `pytest.param(..., id=...)`: pytest builds a test ID from each value, and Python refuses to turn a 5000-digit integer into text, so those cases need a name of their own.

```python
"""Index configurations and their fingerprints, without a database."""

import dataclasses
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

import knowledge_service.chunking as chunking
import knowledge_service.index_configuration as index_configuration
import knowledge_service.reconciliation as reconciliation
from knowledge_service.chunking import DraftChunk
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.embeddings import DistanceMetric, EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.index_configuration import (
    MAX_CHUNK_TOKENS,
    MAX_DIMENSIONS,
    MAX_SETTING_ENTRIES,
    ChunkerSettings,
    IndexConfiguration,
    InvalidIndexConfiguration,
    NormalizationSettings,
    current_index_configuration,
)
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.sources import SourceAccess, SourceRecord
from knowledge_service.structure import ParsedDocument

OBSERVED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
MODEL = EmbeddingModelIdentity(provider="openai", model="embed-small", dimensions=8)
CHUNKER = ChunkerSettings(
    version="structure/1", min_tokens=64, max_tokens=256, overlap_tokens=32
)
NORMALIZATION = NormalizationSettings(
    version="normalization/1",
    parsers=(("text/plain", "text/1"), ("text/markdown", "markdown/1")),
    aliases=(("text/x-markdown", "text/markdown"),),
    unicode_version="16.0.0",
)
CONFIGURATION = IndexConfiguration(
    embedding=MODEL,
    chunker=CHUNKER,
    normalization=NORMALIZATION,
    distance=DistanceMetric.COSINE,
    vector_scaling="unit_length/1",
)
# The fingerprint of CONFIGURATION. If this changes, every stored
# configuration gets a new identity, so it must only change on purpose.
EXPECTED_FINGERPRINT = (
    "4ba7abebdc68a6fd149c666af0ea5a9092f7f9233ff4b48ce44b3bd473db3298"
)


def test_the_fingerprint_of_known_settings_never_changes() -> None:
    assert CONFIGURATION.fingerprint == EXPECTED_FINGERPRINT


def test_equal_settings_give_equal_fingerprints_in_any_order() -> None:
    reordered = dataclasses.replace(
        CONFIGURATION,
        normalization=NormalizationSettings(
            version="normalization/1",
            parsers=(("text/markdown", "markdown/1"), ("text/plain", "text/1")),
            aliases=(("text/x-markdown", "text/markdown"),),
            unicode_version="16.0.0",
        ),
    )

    assert reordered == CONFIGURATION
    assert reordered.fingerprint == CONFIGURATION.fingerprint


def _changed_parser(parsers: tuple[tuple[str, str], ...]) -> NormalizationSettings:
    return dataclasses.replace(NORMALIZATION, parsers=parsers)


CHANGES: dict[str, Callable[[], IndexConfiguration]] = {
    "provider": lambda: dataclasses.replace(
        CONFIGURATION, embedding=dataclasses.replace(MODEL, provider="other")
    ),
    "model": lambda: dataclasses.replace(
        CONFIGURATION, embedding=dataclasses.replace(MODEL, model="embed-large")
    ),
    "dimensions": lambda: dataclasses.replace(
        CONFIGURATION, embedding=dataclasses.replace(MODEL, dimensions=16)
    ),
    "chunker version": lambda: dataclasses.replace(
        CONFIGURATION, chunker=dataclasses.replace(CHUNKER, version="structure/2")
    ),
    "min tokens": lambda: dataclasses.replace(
        CONFIGURATION, chunker=dataclasses.replace(CHUNKER, min_tokens=65)
    ),
    "max tokens": lambda: dataclasses.replace(
        CONFIGURATION, chunker=dataclasses.replace(CHUNKER, max_tokens=257)
    ),
    "overlap tokens": lambda: dataclasses.replace(
        CONFIGURATION, chunker=dataclasses.replace(CHUNKER, overlap_tokens=0)
    ),
    "normalization version": lambda: dataclasses.replace(
        CONFIGURATION,
        normalization=dataclasses.replace(NORMALIZATION, version="normalization/2"),
    ),
    "a parser version": lambda: dataclasses.replace(
        CONFIGURATION,
        normalization=_changed_parser(
            (("text/plain", "text/2"), ("text/markdown", "markdown/1"))
        ),
    ),
    "a new parser": lambda: dataclasses.replace(
        CONFIGURATION,
        normalization=_changed_parser(
            (
                ("text/plain", "text/1"),
                ("text/markdown", "markdown/1"),
                ("text/html", "html/1"),
            )
        ),
    ),
    "an alias": lambda: dataclasses.replace(
        CONFIGURATION,
        normalization=dataclasses.replace(NORMALIZATION, aliases=()),
    ),
    "unicode version": lambda: dataclasses.replace(
        CONFIGURATION,
        normalization=dataclasses.replace(NORMALIZATION, unicode_version="17.0.0"),
    ),
    "vector scaling": lambda: dataclasses.replace(
        CONFIGURATION, vector_scaling="unit_length/2"
    ),
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_every_changed_setting_changes_the_fingerprint(change: str) -> None:
    changed = CHANGES[change]()

    assert changed.fingerprint != CONFIGURATION.fingerprint


def test_no_two_changes_share_a_fingerprint() -> None:
    fingerprints = {make().fingerprint for make in CHANGES.values()}

    assert len(fingerprints) == len(CHANGES)


def test_a_configuration_cannot_be_changed_in_place() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONFIGURATION.vector_scaling = "other"  # pyright: ignore[reportAttributeAccessIssue]


def test_the_current_configuration_follows_the_code() -> None:
    first = current_index_configuration(MODEL)
    again = current_index_configuration(MODEL)

    assert first == again
    assert first.fingerprint == again.fingerprint
    assert first.embedding == MODEL
    assert first.distance is DistanceMetric.COSINE
    assert dict(first.normalization.parsers).keys() >= {"text/plain", "text/markdown"}


def test_upgrading_a_parser_library_changes_the_current_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = current_index_configuration(MODEL)
    upgraded = dict(before.normalization.parsers)
    upgraded["application/pdf"] = "pdf/1 pypdf/99.0.0"
    monkeypatch.setattr(index_configuration, "parser_versions", lambda: upgraded)

    after = current_index_configuration(MODEL)

    assert after.fingerprint != before.fingerprint


@pytest.mark.parametrize(
    ("version", "min_tokens", "max_tokens", "overlap_tokens"),
    [
        ("structure/1", True, 256, 32),
        ("structure/1", 64.0, 256, 32),
        ("structure/1", 0, 256, 32),
        ("structure/1", 300, 256, 32),
        ("structure/1", 64, 256, 256),
        ("structure/1", 64, 256, -1),
        ("", 64, 256, 32),
        (" structure/1", 64, 256, 32),
        (1, 64, 256, 32),
        ("x" * 201, 64, 256, 32),
        # Once crashed the fingerprint: JSON cannot write such an int.
        pytest.param("structure/1", 1, 10**5000, 0, id="huge-max-tokens"),
        ("structure/1", 64, MAX_CHUNK_TOKENS + 1, 32),
        # Once reached PostgreSQL and failed there, or looked equal but
        # hashed differently.
        ("structure\x00/1", 64, 256, 32),
        ("structure\ud800/1", 64, 256, 32),
        ("structure/1\u200b", 64, 256, 32),
        ("structure\n/1", 64, 256, 32),
        ("caf\u0065\u0301", 64, 256, 32),
    ],
)
def test_invalid_chunker_settings_are_refused(
    version: object, min_tokens: object, max_tokens: object, overlap_tokens: object
) -> None:
    with pytest.raises(InvalidIndexConfiguration):
        ChunkerSettings(
            version=cast(str, version),
            min_tokens=cast(int, min_tokens),
            max_tokens=cast(int, max_tokens),
            overlap_tokens=cast(int, overlap_tokens),
        )


@pytest.mark.parametrize(
    ("parsers", "aliases"),
    [
        ((), ()),
        ({"text/plain": "text/1"}, ()),
        ([("text/plain", "text/1")], ()),
        ((("text/plain", "text/1"), ("text/plain", "text/2")), ()),
        ((("text/plain",),), ()),
        ((("text/plain", "text/1", "extra"),), ()),
        ((("text/plain", 1),), ()),
        ((("text/plain", ""),), ()),
        ((("text/plain", "text/1"),), (("a", "b"), ("a", "c"))),
        # An alias must point at a parsed media type, from one that is not.
        ((("text/plain", "text/1"),), (("x/y", "nope/none"),)),
        ((("text/plain", "text/1"),), (("text/plain", "text/plain"),)),
        (
            tuple((f"type/{i}", "v/1") for i in range(MAX_SETTING_ENTRIES + 1)),
            (),
        ),
    ],
)
def test_invalid_normalization_settings_are_refused(
    parsers: object, aliases: object
) -> None:
    with pytest.raises(InvalidIndexConfiguration):
        NormalizationSettings(
            version="normalization/1",
            parsers=cast(tuple[tuple[str, str], ...], parsers),
            aliases=cast(tuple[tuple[str, str], ...], aliases),
            unicode_version="16.0.0",
        )


@pytest.mark.parametrize(
    "change",
    [
        {"distance": "cosine"},
        {"vector_scaling": ""},
        {"chunker": dataclasses.asdict(CHUNKER)},
        {"normalization": None},
        {"embedding": dataclasses.asdict(MODEL)},
        {
            "embedding": EmbeddingModelIdentity(
                provider=" openai", model="m", dimensions=8
            )
        },
        {"embedding": EmbeddingModelIdentity("openai", "m", MAX_DIMENSIONS + 1)},
        pytest.param(
            {"embedding": EmbeddingModelIdentity("openai", "m", 10**5000)},
            id="huge-dimensions",
        ),
        {"vector_scaling": "unit_length/1\u200b"},
    ],
)
def test_an_invalid_configuration_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(InvalidIndexConfiguration):
        dataclasses.replace(CONFIGURATION, **change)  # pyright: ignore[reportArgumentType]


def test_a_configuration_altered_after_it_was_made_is_refused_when_checked() -> None:
    altered = dataclasses.replace(CONFIGURATION)
    object.__setattr__(altered, "vector_scaling", "unit\x00length")

    with pytest.raises(InvalidIndexConfiguration):
        altered.checked()


def test_the_chunker_rules_are_pinned_to_the_chunker_version() -> None:
    # These constants change chunk text, so vectors, without being settings
    # of their own. Changing one must come with a new CHUNKER_VERSION; then
    # update this test.
    assert (
        chunking.CHUNKER_VERSION,
        chunking.MAX_TOKEN_CHARACTERS,
        chunking.BLOCK_SEPARATOR,
    ) == ("structure/1", 32, "\n\n")


def test_the_chunker_settings_are_the_ones_reconciling_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reconciling cuts Chunks through build_chunks. If it passed its own
    # budgets, or the chunker's defaults moved away from the constants, the
    # fingerprint would describe other Chunks than the ones stored.
    budgets: list[dict[str, int]] = []
    chunk_by_structure = reconciliation.chunk_by_structure

    def spy(parsed: ParsedDocument, **given: int) -> tuple[DraftChunk, ...]:
        budgets.append(given)
        return chunk_by_structure(parsed, **given)

    monkeypatch.setattr(reconciliation, "chunk_by_structure", spy)
    source_id = SourceId.new()
    record = normalize(
        SourceRecord.present(
            external_id="policy.txt",
            version="v1",
            media_type="text/plain",
            content=b"The leave policy.",
            access=SourceAccess(public=True),
        ),
        source_id=source_id,
    )
    assert isinstance(record, NormalizedDocument)
    document = Document.register(
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(source_id=source_id, external_id="policy.txt"),
        title="Policy",
        content_fingerprint="content",
        authorization_fingerprint="access",
        observed_at=OBSERVED_AT,
    )

    reconciliation.build_chunks(record, document)

    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(chunk_by_structure).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    chunker = current_index_configuration(MODEL).chunker
    assert budgets == [{}]
    assert defaults == {
        "min_tokens": chunker.min_tokens,
        "max_tokens": chunker.max_tokens,
        "overlap_tokens": chunker.overlap_tokens,
    }
```

**`tests/test_index_configuration_integration.py`** (new, real database):

| Test | What it proves |
|---|---|
| `test_identical_settings_reuse_the_stored_identity` | Same settings, one row, same ID. |
| `test_a_changed_setting_creates_a_new_identity` | Five changes, each a second row with its own ID, reused when stored again. |
| `test_settings_listed_in_another_order_reuse_the_identity` | Order does not matter. |
| `test_two_callers_storing_one_new_configuration_get_one_row` | Four concurrent callers, one row. |
| `test_a_stored_row_edited_after_the_fact_is_refused` | Typed failure: `IndexConfigurationConflict`. |
| `test_a_stored_number_written_as_a_float_is_refused` | Review regression: `8.0` for `8` is caught. |
| `test_a_configuration_altered_after_it_was_made_is_never_stored` | Review regression: nothing is stored. |
| `test_the_database_refuses_a_malformed_fingerprint` | The check constraint. |

```python
"""Real PostgreSQL tests: equal settings reuse an identity, changed ones do not."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings import EmbeddingModelIdentity
from knowledge_service.identifiers import IndexConfigurationId
from knowledge_service.index_configuration import (
    IndexConfiguration,
    IndexConfigurationConflict,
    InvalidIndexConfiguration,
    NormalizationSettings,
    current_index_configuration,
)
from knowledge_service.persistence import (
    ensure_index_configuration,
    index_configurations,
)
from knowledge_service.settings import Settings

CREATED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
MODEL = EmbeddingModelIdentity(provider="openai", model="embed-small", dimensions=8)
CURRENT = current_index_configuration(MODEL)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def ensure(
    runtime: DatabaseRuntime, configuration: IndexConfiguration
) -> IndexConfigurationId:
    async with runtime.transaction() as connection:
        return await ensure_index_configuration(
            connection, configuration, created_at=CREATED_AT
        )


async def stored_rows(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        count = await connection.scalar(
            sa.select(sa.func.count()).select_from(index_configurations)
        )
    return int(count or 0)


@pytest.mark.integration
async def test_identical_settings_reuse_the_stored_identity(
    runtime: DatabaseRuntime,
) -> None:
    first = await ensure(runtime, CURRENT)
    again = await ensure(runtime, current_index_configuration(MODEL))

    assert again == first
    assert await stored_rows(runtime) == 1


@pytest.mark.integration
async def test_the_stored_row_holds_the_fingerprint_and_settings(
    runtime: DatabaseRuntime,
) -> None:
    configuration_id = await ensure(runtime, CURRENT)

    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    index_configurations.c.fingerprint,
                    index_configurations.c.settings,
                ).where(
                    index_configurations.c.configuration_id == configuration_id.value
                )
            )
        ).one()

    assert row.fingerprint == CURRENT.fingerprint
    assert row.settings == CURRENT.settings()


@pytest.mark.integration
@pytest.mark.parametrize(
    "changed",
    [
        dataclasses.replace(
            CURRENT, embedding=EmbeddingModelIdentity("openai", "embed-large", 8)
        ),
        dataclasses.replace(
            CURRENT, embedding=EmbeddingModelIdentity("openai", "embed-small", 16)
        ),
        dataclasses.replace(
            CURRENT,
            chunker=dataclasses.replace(
                CURRENT.chunker, max_tokens=CURRENT.chunker.max_tokens + 1
            ),
        ),
        dataclasses.replace(
            CURRENT,
            normalization=dataclasses.replace(
                CURRENT.normalization, version="normalization/999"
            ),
        ),
        dataclasses.replace(CURRENT, vector_scaling="unit_length/999"),
    ],
    ids=["model", "dimensions", "chunker", "normalization", "scaling"],
)
async def test_a_changed_setting_creates_a_new_identity(
    runtime: DatabaseRuntime, changed: IndexConfiguration
) -> None:
    original = await ensure(runtime, CURRENT)

    new = await ensure(runtime, changed)
    again = await ensure(runtime, changed)

    assert new != original
    assert again == new
    assert await stored_rows(runtime) == 2


@pytest.mark.integration
async def test_settings_listed_in_another_order_reuse_the_identity(
    runtime: DatabaseRuntime,
) -> None:
    original = await ensure(runtime, CURRENT)
    reordered = dataclasses.replace(
        CURRENT,
        normalization=NormalizationSettings(
            version=CURRENT.normalization.version,
            parsers=tuple(reversed(CURRENT.normalization.parsers)),
            aliases=tuple(reversed(CURRENT.normalization.aliases)),
            unicode_version=CURRENT.normalization.unicode_version,
        ),
    )

    assert await ensure(runtime, reordered) == original


@pytest.mark.integration
async def test_two_callers_storing_one_new_configuration_get_one_row(
    runtime: DatabaseRuntime,
) -> None:
    identities = await asyncio.gather(*(ensure(runtime, CURRENT) for _ in range(4)))

    assert len(set(identities)) == 1
    assert await stored_rows(runtime) == 1


@pytest.mark.integration
async def test_a_stored_row_edited_after_the_fact_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    await ensure(runtime, CURRENT)
    # Rows are never updated by the application; simulate a manual edit.
    edited = CURRENT.settings() | {"vector_scaling": "edited"}
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(index_configurations).values(settings=edited)
        )

    with pytest.raises(IndexConfigurationConflict):
        await ensure(runtime, CURRENT)


@pytest.mark.integration
async def test_the_database_refuses_a_malformed_fingerprint(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(IntegrityError):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.insert(index_configurations).values(
                    configuration_id=IndexConfigurationId.new().value,
                    fingerprint="not-a-sha256",
                    settings={},
                    created_at=CREATED_AT,
                )
            )


@pytest.mark.integration
async def test_a_stored_number_written_as_a_float_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    await ensure(runtime, CURRENT)
    # 8.0 equals 8 in Python, so only the fingerprint of the row tells them
    # apart; this edit once went unnoticed.
    settings = CURRENT.settings()
    embedding = cast(dict[str, object], settings["embedding"])
    edited = settings | {"embedding": embedding | {"dimensions": 8.0}}
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(index_configurations).values(settings=edited)
        )

    with pytest.raises(IndexConfigurationConflict):
        await ensure(runtime, CURRENT)


@pytest.mark.integration
async def test_a_configuration_altered_after_it_was_made_is_never_stored(
    runtime: DatabaseRuntime,
) -> None:
    altered = dataclasses.replace(CURRENT)
    # Once stored, then failed in PostgreSQL with a raw DataError.
    object.__setattr__(altered, "vector_scaling", "unit\x00length")

    with pytest.raises(InvalidIndexConfiguration):
        await ensure(runtime, altered)
    assert await stored_rows(runtime) == 0
```

Check: `uv run pytest tests/test_index_configuration.py tests/test_embeddings.py -q` passes, then:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py knowledge-service-postgres)" uv run pytest tests/test_index_configuration_integration.py tests/test_migrations_integration.py -q
```

### Step 6 — Coding step: update the architecture doc

**In plain words:** The architecture page lists the rule each table enforces. This step adds the new table's rule: one row per fingerprint, never updated.

In `docs/architecture.md`, in the table of rules, add this row before the `embedding_spaces` row:

```markdown
| `index_configurations` | One row per settings `fingerprint` (64 lowercase hex digits); rows are only inserted, so a changed setting is a new row. |
```

Check: `make docs-check` passes.

### Step 7 — No coding in this step: run the gates, then record evidence

Run these in order and keep the outputs:

```bash
uv run pytest tests/test_index_configuration.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: everything passes. On the reference run, `make check` reported 980 passed and 3 deselected, with `index_configuration.py` fully covered.

Then complete the evidence file from Step 1:

- **Design:** the changed files and a table of the settings in a configuration, with why each one shapes vectors.
- **Verification:** the commands and their pass counts. Optionally, break one thing at a time (drop `unicode_version` from `settings()`; remove `ON CONFLICT DO NOTHING`; remove the `settings_fingerprint` comparison) and record which test fails.
- **Risks:** the `html.parser` limit above; version labels must be raised by hand when code changes without a number to show it; nothing uses configurations yet (M04-T10).
- **Reflection:** your answers to the questions below.

Finally, add the M04-T09 row to `docs/curriculum/PROGRESS.md`, set the next task to M04-T10, and commit with `m04-t09: version index configurations`.

## Reflection questions

1. Why is changing the chunker's `max_tokens` from 256 to 257 a reason to treat all stored vectors as belonging to a different configuration, even though the embedding model did not change?
2. The fingerprint is computed from the settings, and the database also stores the settings themselves. Why keep both? What would we lose if we stored only the fingerprint?
3. A teammate fixes a bug in how the chunker joins blocks and forgets to raise `CHUNKER_VERSION`. Which test would notice, which would not, and what would happen to search quality if nobody noticed?
4. Four requests try to save the same new configuration at the same moment. Walk through what the database does with the second request, and why it ends up with the first request's ID instead of an error.
5. We record the Unicode data version but not the full Python version. What is the trade-off between recording too little (stale vectors) and recording too much (re-embedding everything after every small upgrade)?

## Completion checklist

- [ ] The evidence file has the invariant and predictions, written before the code.
- [ ] `CHUNKER_VERSION`, `NORMALIZATION_VERSION`, `parser_versions`, `media_type_aliases`, `DistanceMetric` in `embeddings.py`, `canonical_hash`, and `IndexConfigurationId` exist.
- [ ] `IndexConfiguration` covers model, dimensions, chunker, normalization, Unicode version, distance, and scaling, and refuses bad settings with `InvalidIndexConfiguration`.
- [ ] `ensure_index_configuration` reuses the identity for equal settings and creates a new one for any change; the migration drift test passes.
- [ ] Every single-setting change gives a new fingerprint; a pinned fingerprint guards the canonical form.
- [ ] A tampered stored row raises `IndexConfigurationConflict`.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] `docs/architecture.md` lists the new table's rule.
- [ ] The evidence file has the design, verification, risks and your reflection.
- [ ] The PROGRESS row is added and the commit is made.

When you have written the code, share it or any error output, and I will review it against this checklist.
