"""Index configurations and their fingerprints, without a database."""

import dataclasses
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

import knowledge_service.embeddings.index_configuration as index_configuration
import knowledge_service.ingestion.chunking as chunking
import knowledge_service.ingestion.reconciliation as reconciliation
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.embeddings.index_configuration import (
    MAX_CHUNK_TOKENS,
    MAX_DIMENSIONS,
    MAX_SETTING_ENTRIES,
    ChunkerSettings,
    IndexConfiguration,
    InvalidIndexConfiguration,
    NormalizationSettings,
    current_index_configuration,
)
from knowledge_service.embeddings.port import DistanceMetric, EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.ingestion.chunking import DraftChunk
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.parsers.structure import ParsedDocument
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord

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
