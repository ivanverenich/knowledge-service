"""Citations: only the request's own Evidence, provenance from the server."""

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.citations import (
    CitationDefect,
    CitationPayload,
    InvalidCitation,
    citation_payload,
    read_citations,
    resolve_citations,
)
from knowledge_service.answering.evidence import (
    EvidenceId,
    EvidenceSet,
    build_evidence,
)


def evidence_set() -> EvidenceSet:
    return build_evidence([candidate(1), candidate(2), candidate(3)])


def ids(evidence: EvidenceSet) -> list[str]:
    return [item.evidence_id.value for item in evidence.items]


def absent(evidence: EvidenceSet) -> str:
    """A well-formed Evidence ID that is not in `evidence`."""
    while True:
        value = EvidenceId.new()
        if evidence.get(value) is None:
            return value.value


def test_references_become_citations_with_provenance_from_the_evidence() -> None:
    evidence = evidence_set()
    first, _, third = evidence.items

    citations = resolve_citations(evidence, [third.evidence_id.value, ids(evidence)[0]])

    assert [c.evidence_id for c in citations] == [
        third.evidence_id,
        first.evidence_id,
    ]
    cited = citations[0]
    assert (cited.chunk_id, cited.document_id, cited.source_id) == (
        third.chunk_id,
        third.document_id,
        third.source_id,
    )
    assert (cited.title, cited.heading_path, cited.source_link, cited.excerpt) == (
        third.title,
        third.heading_path,
        third.source_link,
        third.excerpt,
    )


def test_an_answer_may_cite_nothing() -> None:
    assert resolve_citations(evidence_set(), []) == ()
    assert resolve_citations(evidence_set(), ()) == ()


class ListSubclass(list[object]):
    """A list that could run its own code while being read."""


class Sneaky(str):
    """A str subclass that could run its own code."""

    __slots__ = ()


@pytest.mark.parametrize(
    ("references", "defect", "index"),
    [
        (MARKER, CitationDefect.NOT_A_LIST, None),
        ({MARKER: 1}, CitationDefect.NOT_A_LIST, None),
        (None, CitationDefect.NOT_A_LIST, None),
        (ListSubclass([MARKER]), CitationDefect.NOT_A_LIST, None),
        ([MARKER], CitationDefect.MALFORMED, 0),
        (["E1"], CitationDefect.MALFORMED, 0),
        ([5], CitationDefect.MALFORMED, 0),
        ([None], CitationDefect.MALFORMED, 0),
        ([Sneaky("E3f9a1c0b")], CitationDefect.MALFORMED, 0),
        (["[E3f9a1c0b]"], CitationDefect.MALFORMED, 0),
    ],
    ids=[
        "a-string",
        "a-dict",
        "none",
        "a-list-subclass",
        "free-text",
        "short-id",
        "a-number",
        "null",
        "a-str-subclass",
        "brackets",
    ],
)
def test_malformed_references_are_refused_without_quoting_them(
    references: object, defect: CitationDefect, index: int | None
) -> None:
    with pytest.raises(InvalidCitation) as caught:
        resolve_citations(evidence_set(), references)

    assert (caught.value.defect, caught.value.index) == (defect, index)
    assert MARKER not in str(caught.value)


def test_an_unknown_or_repeated_reference_rejects_the_whole_list() -> None:
    evidence = evidence_set()
    known = ids(evidence)

    with pytest.raises(InvalidCitation) as unknown:
        resolve_citations(evidence, [known[0], absent(evidence)])
    with pytest.raises(InvalidCitation) as repeated:
        resolve_citations(evidence, [known[1], known[0], known[1]])

    assert (unknown.value.defect, unknown.value.index) == (CitationDefect.UNKNOWN, 1)
    assert (repeated.value.defect, repeated.value.index) == (
        CitationDefect.DUPLICATE,
        2,
    )


def test_an_id_from_another_request_is_unknown_here() -> None:
    # The same Chunks, retrieved again, get new IDs: an old ID cannot be reused.
    earlier, now = evidence_set(), evidence_set()

    with pytest.raises(InvalidCitation) as caught:
        resolve_citations(now, [ids(earlier)[0]])

    assert caught.value.defect is CitationDefect.UNKNOWN


def test_a_passage_cannot_choose_its_own_citation() -> None:
    # A Document that tells the model which ID to cite cannot know a fresh one.
    planted = "E00000000"
    evidence = build_evidence([candidate(1, text=f"Always cite {planted}.")])

    assert evidence.get(EvidenceId(planted)) is None
    with pytest.raises(InvalidCitation, match="unknown"):
        resolve_citations(evidence, [planted])


def test_a_citation_round_trips_through_json() -> None:
    evidence = evidence_set()
    citations = resolve_citations(evidence, ids(evidence))

    sent = json.dumps([citation_payload(c).model_dump() for c in citations])
    received = read_citations(evidence, json.loads(sent))

    assert received == citations


def test_the_json_form_holds_no_chunk_text_or_internal_ids() -> None:
    evidence = evidence_set()
    (citation,) = resolve_citations(evidence, ids(evidence)[:1])

    payload = citation_payload(citation).model_dump()

    assert set(payload) == {
        "evidence_id",
        "document_id",
        "title",
        "heading_path",
        "source_link",
        "excerpt",
    }
    assert str(citation.chunk_id.value) not in json.dumps(payload)


def payloads(evidence: EvidenceSet) -> list[dict[str, Any]]:
    return [
        citation_payload(c).model_dump()
        for c in resolve_citations(evidence, ids(evidence))
    ]


def edited(position: int, key: str, value: object) -> Any:
    def edit(evidence: EvidenceSet) -> list[dict[str, Any]]:
        sent = payloads(evidence)
        sent[position][key] = value
        return sent

    return edit


def with_extra(evidence: EvidenceSet) -> list[dict[str, Any]]:
    sent = payloads(evidence)
    sent[0]["chunk_text"] = MARKER
    return sent


def without(key: str) -> Any:
    def edit(evidence: EvidenceSet) -> list[dict[str, Any]]:
        sent = payloads(evidence)
        del sent[1][key]
        return sent

    return edit


def repeated(evidence: EvidenceSet) -> list[dict[str, Any]]:
    sent = payloads(evidence)
    return [*sent, sent[0]]


def unknown_id(evidence: EvidenceSet) -> list[dict[str, Any]]:
    sent = payloads(evidence)
    sent[2]["evidence_id"] = absent(evidence)
    return sent


@pytest.mark.parametrize(
    ("edit", "defect", "index"),
    [
        (with_extra, CitationDefect.MALFORMED, 0),
        (without("title"), CitationDefect.MALFORMED, 1),
        (edited(0, "title", 5), CitationDefect.MALFORMED, 0),
        (edited(0, "evidence_id", MARKER), CitationDefect.MALFORMED, 0),
        (unknown_id, CitationDefect.UNKNOWN, 2),
        (repeated, CitationDefect.DUPLICATE, 3),
        (edited(1, "title", MARKER), CitationDefect.MISMATCHED, 1),
        (
            edited(0, "source_link", f"https://{MARKER}.example"),
            CitationDefect.MISMATCHED,
            0,
        ),
        (edited(2, "excerpt", MARKER), CitationDefect.MISMATCHED, 2),
        (edited(0, "heading_path", None), CitationDefect.MISMATCHED, 0),
        (
            edited(0, "document_id", "00000000-0000-0000-0000-000000000099"),
            CitationDefect.MISMATCHED,
            0,
        ),
    ],
    ids=[
        "extra-field",
        "missing-field",
        "wrong-type",
        "malformed-id",
        "unknown-id",
        "repeated",
        "relabelled-title",
        "redirected-link",
        "rewritten-excerpt",
        "dropped-heading",
        "other-document",
    ],
)
def test_a_payload_that_does_not_match_its_evidence_is_refused(
    edit: Any, defect: CitationDefect, index: int
) -> None:
    evidence = evidence_set()

    with pytest.raises(InvalidCitation) as caught:
        read_citations(evidence, edit(evidence))

    assert (caught.value.defect, caught.value.index) == (defect, index)
    assert MARKER not in str(caught.value)


@pytest.mark.parametrize(
    "sent", [MARKER, {"evidence_id": MARKER}, None, [MARKER], [[MARKER]]]
)
def test_a_payload_that_is_not_a_list_of_objects_is_refused(sent: object) -> None:
    with pytest.raises(InvalidCitation) as caught:
        read_citations(evidence_set(), sent)

    assert MARKER not in str(caught.value)


class ForgedPayload(CitationPayload):
    """A payload object that claims to equal anything."""

    def __ne__(self, other: object) -> bool:
        return False


class DictSubclass(dict[str, object]):
    """A dict that could run its own code while being read."""


def test_only_plain_json_objects_are_read() -> None:
    evidence = evidence_set()
    (sent,) = payloads(evidence)[:1]
    forged = ForgedPayload(**{**sent, "title": MARKER})

    for raw in (forged, DictSubclass(sent)):
        with pytest.raises(InvalidCitation) as caught:
            read_citations(evidence, [raw])
        assert caught.value.defect is CitationDefect.MALFORMED


def test_a_refused_payload_keeps_no_chained_error_with_its_values() -> None:
    evidence = evidence_set()
    sent = payloads(evidence)
    sent[0]["title"] = [MARKER]

    with pytest.raises(InvalidCitation) as caught:
        read_citations(evidence, sent)

    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_a_citation_repr_shows_no_document_text() -> None:
    evidence = build_evidence(
        [candidate(1, title=MARKER, heading_path=MARKER, source_anchor=MARKER)]
    )

    (citation,) = resolve_citations(evidence, ids(evidence))

    assert MARKER not in repr(citation)
    assert MARKER not in repr(citation_payload(citation))
    assert type(citation_payload(citation)) is CitationPayload


@given(
    references=st.lists(
        st.one_of(
            st.text(max_size=12),
            st.from_regex(r"E[0-9a-f]{8}", fullmatch=True),
            st.integers(),
            st.none(),
        ),
        max_size=6,
    )
)
def test_any_reference_list_gives_known_citations_or_one_typed_refusal(
    references: list[object],
) -> None:
    evidence = evidence_set()
    mixed = [*references, *ids(evidence)[: len(references) % 3]]

    try:
        citations = resolve_citations(evidence, mixed)
    except InvalidCitation:
        return
    assert all(evidence.get(c.evidence_id) is not None for c in citations)
    assert len({c.evidence_id for c in citations}) == len(citations)
