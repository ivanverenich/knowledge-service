"""Name every setting that shapes a stored vector, and fingerprint them.

Two vectors can be compared only if the same text went through the same
steps: the same normalization turned a Source Record into blocks, the same
chunker cut the blocks into Chunks, and the same model, at the same
dimensions, embedded them, scaled the same way, for the same distance. An
`IndexConfiguration` holds all of those settings as one immutable value.

Its `fingerprint` is a SHA-256 hash of one canonical form of the settings,
so equal settings always give the same fingerprint, whatever order they were
listed in, and changing any one of them gives a different one. Storing a
configuration (see `persistence.embeddings.ensure_index_configuration`)
reuses the row of an equal configuration and creates a row for a changed
one.
"""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from knowledge_service.embeddings.port import (
    VECTOR_SCALING,
    DistanceMetric,
    EmbeddingModelIdentity,
)
from knowledge_service.ingestion.chunking import (
    CHUNKER_VERSION,
    MAX_TOKENS,
    MIN_TOKENS,
    OVERLAP_TOKENS,
)
from knowledge_service.ingestion.fingerprints import canonical_hash
from knowledge_service.ingestion.normalization import (
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
