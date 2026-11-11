"""Evidence: opaque IDs, copied provenance, a safe excerpt, nothing leaked."""

from collections.abc import Iterator
from dataclasses import replace
from uuid import UUID

import pytest

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.evidence import (
    MAX_EVIDENCE,
    Evidence,
    EvidenceId,
    EvidenceSet,
    InvalidEvidence,
    build_evidence,
)
from knowledge_service.identifiers import ChunkId, DocumentId
from knowledge_service.ingestion.provenance import EXCERPT_CHARACTERS
from knowledge_service.retrieval.vector_search import VectorCandidate


def test_each_candidate_becomes_evidence_with_its_provenance_copied() -> None:
    candidates = [candidate(1), candidate(2), candidate(3)]

    evidence = build_evidence(candidates)

    assert len(evidence.items) == 3
    for item, source in zip(evidence.items, candidates, strict=True):
        assert (item.chunk_id, item.document_id, item.source_id) == (
            source.chunk_id,
            source.document_id,
            source.source_id,
        )
        assert (item.content_version, item.ordinal) == (
            source.content_version,
            source.ordinal,
        )
        assert (item.title, item.heading_path, item.source_link) == (
            source.title,
            source.heading_path,
            source.source_anchor,
        )
        assert item.text == source.text
        assert item.excerpt == source.text
        assert evidence.get(item.evidence_id) is item


def test_evidence_ids_are_opaque_distinct_and_new_for_every_set() -> None:
    candidates = [candidate(number) for number in range(1, MAX_EVIDENCE + 1)]

    first = build_evidence(candidates)
    second = build_evidence(candidates)

    ids = [item.evidence_id.value for item in first.items]
    assert len(set(ids)) == MAX_EVIDENCE
    assert all(len(value) == 9 and value.startswith("E") for value in ids)
    # Nothing in an ID comes from the Chunk, and a new set gets new IDs.
    assert not set(ids) & {item.evidence_id.value for item in second.items}
    assert all(str(c.chunk_id.value)[:8] not in "".join(ids) for c in candidates)


def test_no_candidates_give_an_empty_set() -> None:
    evidence = build_evidence([])

    assert evidence.items == ()
    assert evidence.get(EvidenceId.new()) is None


def test_more_candidates_than_the_limit_are_refused() -> None:
    with pytest.raises(InvalidEvidence, match=f"at most {MAX_EVIDENCE}"):
        build_evidence([candidate(number) for number in range(MAX_EVIDENCE + 1)])


def test_the_same_chunk_twice_is_refused() -> None:
    with pytest.raises(InvalidEvidence, match="a Chunk appears twice"):
        build_evidence([candidate(1), candidate(1, rank=2)])


@pytest.mark.parametrize(
    "candidates",
    [
        MARKER,
        b"bytes",
        {MARKER: 1},
        [object()],
        [candidate(1, chunk_id=str(UUID(int=1)))],
        [candidate(1, text="   ")],
        [candidate(1, text=None)],
        [candidate(1, ordinal=-1)],
        [candidate(1, ordinal=True)],
        [candidate(1, title=None)],
        [candidate(1, source_anchor=5)],
        [candidate(1, content_version=3)],
    ],
    ids=[
        "a-string",
        "bytes",
        "a-dict",
        "not-a-candidate",
        "chunk-id-as-text",
        "blank-text",
        "no-text",
        "negative-ordinal",
        "bool-ordinal",
        "no-title",
        "link-not-text",
        "version-not-a-version",
    ],
)
def test_candidates_that_are_not_what_retrieval_returns_are_refused(
    candidates: object,
) -> None:
    with pytest.raises(InvalidEvidence) as caught:
        build_evidence(candidates)  # pyright: ignore[reportArgumentType]

    assert MARKER not in str(caught.value)


class Hostile:
    """A Candidate whose text property raises with Document text in it."""

    def __getattr__(self, name: str) -> object:
        return getattr(candidate(1), name)

    @property
    def text(self) -> str:
        raise ValueError(MARKER)


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate(1, chunk_id=DocumentId(UUID(int=1)))],
        [candidate(1, chunk_id=ChunkId(str(UUID(int=1))))],  # pyright: ignore[reportArgumentType]
        [candidate(1, document_id=DocumentId(None))],  # pyright: ignore[reportArgumentType]
        [Hostile()],
    ],
    ids=["chunk-id-of-a-document", "id-holding-text", "id-holding-none", "raising"],
)
def test_an_identifier_of_the_wrong_kind_or_a_raising_field_is_refused(
    candidates: list[object],
) -> None:
    with pytest.raises(InvalidEvidence) as caught:
        build_evidence(candidates)  # pyright: ignore[reportArgumentType]

    assert MARKER not in str(caught.value)
    assert caught.value.__cause__ is None
    assert MARKER not in repr(caught.value.__context__)


def test_an_endless_stream_of_candidates_is_cut_at_the_limit() -> None:
    def endless() -> Iterator[VectorCandidate]:
        number = 0
        while True:
            number += 1
            yield candidate(number)

    with pytest.raises(InvalidEvidence, match=f"at most {MAX_EVIDENCE}"):
        build_evidence(endless())


def test_title_heading_and_link_are_cleaned_for_display() -> None:
    (item,) = build_evidence(
        [
            candidate(
                1,
                title="Leave\u202e policy\u0007",
                heading_path="Leave > \u200bDays",
                source_anchor="https://admin:secret@hr.example.com/leave",
            )
        ]
    ).items

    assert item.title == "Leave policy"
    assert item.heading_path == "Leave > Days"
    assert item.source_link is not None
    assert "secret" not in item.source_link


def test_evidence_built_by_hand_is_checked_too() -> None:
    (item,) = build_evidence([candidate(1)]).items

    with pytest.raises(InvalidEvidence, match="wrong type"):
        replace(item, ordinal=-5)
    with pytest.raises(InvalidEvidence, match="wrong type"):
        replace(item, text="   ")
    with pytest.raises(InvalidEvidence, match="wrong type"):
        replace(item, document_id=DocumentId("javascript:x"))  # pyright: ignore[reportArgumentType]


def test_the_excerpt_is_short_and_safe_to_show() -> None:
    text = (
        "Open https://admin:secret@hr.example.com/leave?token=abc and read"
        " \u202ethe rules\u0000. " + "More words follow here. " * 30
    )

    (item,) = build_evidence([candidate(1, text=text)]).items

    assert len(item.excerpt) <= EXCERPT_CHARACTERS
    assert item.excerpt.endswith("…")
    assert "secret" not in item.excerpt
    assert "token" not in item.excerpt
    assert "\u202e" not in item.excerpt
    assert "\u0000" not in item.excerpt
    # The model still reads the whole text, as stored.
    assert item.text == text


def test_text_and_provenance_are_kept_out_of_every_repr() -> None:
    evidence = build_evidence(
        [
            candidate(
                1,
                title=MARKER,
                heading_path=MARKER,
                source_anchor=MARKER,
                text=f"{MARKER} leave",
            )
        ]
    )

    assert MARKER not in repr(evidence)
    assert MARKER not in repr(evidence.items[0])


@pytest.mark.parametrize(
    "value",
    ["E1", "e3f9a1c0b", "E3F9A1C0B", "E3f9a1c0b ", "E3f9a1c0b\n", "", 5, None],
)
def test_an_evidence_id_is_e_and_eight_lowercase_hex_digits(value: object) -> None:
    with pytest.raises(InvalidEvidence, match="E and 8 lowercase hex digits"):
        EvidenceId(value)  # pyright: ignore[reportArgumentType]


def test_a_str_subclass_is_not_an_evidence_id() -> None:
    class Sneaky(str):
        __slots__ = ()

    with pytest.raises(InvalidEvidence):
        EvidenceId(Sneaky("E3f9a1c0b"))


def test_a_set_built_by_hand_keeps_the_same_rules() -> None:
    (item,) = build_evidence([candidate(1)]).items
    other = replace(item, evidence_id=EvidenceId.new())

    with pytest.raises(InvalidEvidence, match="a Chunk appears twice"):
        EvidenceSet((item, other))
    with pytest.raises(InvalidEvidence, match="an Evidence ID appears twice"):
        EvidenceSet((item, replace(item, chunk_id=ChunkId(UUID(int=9)))))
    with pytest.raises(InvalidEvidence, match="a tuple of Evidence"):
        EvidenceSet([item])  # pyright: ignore[reportArgumentType]
    with pytest.raises(InvalidEvidence, match=f"at most {MAX_EVIDENCE}"):
        EvidenceSet(
            tuple(
                replace(
                    item,
                    evidence_id=EvidenceId.new(),
                    chunk_id=ChunkId(UUID(int=number)),
                )
                for number in range(MAX_EVIDENCE + 1)
            )
        )
    assert type(item) is Evidence
