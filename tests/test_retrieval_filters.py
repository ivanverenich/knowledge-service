"""The allowlist of metadata conditions, checked without a database."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import cast, override
from uuid import UUID

import pytest

from knowledge_service.identifiers import DocumentId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.filters import (
    MAX_FILTER_VALUES,
    FilterField,
    FilterOperator,
    FilterValue,
    MetadataCondition,
    UnsupportedFilter,
    checked_conditions,
)

SOURCE = SourceId(UUID(int=1))
DOCUMENT = DocumentId(UUID(int=2))
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

ANY_OF = FilterOperator.ANY_OF
ON_OR_AFTER = FilterOperator.ON_OR_AFTER


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        (FilterField.SOURCE, ANY_OF, frozenset({SOURCE})),
        (FilterField.DOCUMENT, ANY_OF, frozenset({DOCUMENT})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/markdown", "text/html"})),
        (FilterField.LANGUAGE, ANY_OF, frozenset({Language.UKRAINIAN})),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE),
    ],
)
def test_each_field_accepts_its_one_operator(
    field: FilterField, operator: FilterOperator, value: FilterValue
) -> None:
    condition = MetadataCondition(field, operator, value)

    assert condition.value == value


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        # An operator the field does not support, with a value the field
        # takes, so only the operator check can refuse it.
        (FilterField.SOURCE, ON_OR_AFTER, frozenset({SOURCE})),
        (FilterField.LANGUAGE, ON_OR_AFTER, frozenset({Language.ENGLISH})),
        (FilterField.MODIFIED_AT, ANY_OF, SINCE),
        # An operator the field does not support, with the wrong value too.
        (FilterField.LANGUAGE, ON_OR_AFTER, SINCE),
        (FilterField.SOURCE, ON_OR_AFTER, SINCE),
        (FilterField.MODIFIED_AT, ANY_OF, frozenset({SINCE})),
        # An operator or field that is not in the enums at all.
        (FilterField.MEDIA_TYPE, "like", frozenset({"text/%"})),
        (FilterField.LANGUAGE, "any_of", frozenset({Language.ENGLISH})),
        ("language", ANY_OF, frozenset({Language.ENGLISH})),
        ("title", ANY_OF, frozenset({"x"})),
        # A value of the wrong shape or type.
        (FilterField.SOURCE, ANY_OF, frozenset[SourceId]()),
        (FilterField.SOURCE, ANY_OF, [SOURCE]),
        (FilterField.SOURCE, ANY_OF, frozenset({SOURCE.value})),
        (FilterField.SOURCE, ANY_OF, frozenset({UserId(UUID(int=1))})),
        (FilterField.DOCUMENT, ON_OR_AFTER, frozenset({DOCUMENT})),
        (FilterField.DOCUMENT, ANY_OF, frozenset[DocumentId]()),
        (FilterField.DOCUMENT, ANY_OF, [DOCUMENT]),
        (FilterField.DOCUMENT, ANY_OF, frozenset({DOCUMENT.value})),
        (FilterField.DOCUMENT, ANY_OF, frozenset({str(DOCUMENT.value)})),
        # A Source ID is a UUID too, but names another kind of thing.
        (FilterField.DOCUMENT, ANY_OF, frozenset({SOURCE})),
        (
            FilterField.DOCUMENT,
            ANY_OF,
            frozenset(DocumentId(UUID(int=i)) for i in range(MAX_FILTER_VALUES + 1)),
        ),
        (FilterField.LANGUAGE, ANY_OF, frozenset({"uk"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({1})),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE.date()),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, "2026-10-01"),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, datetime(2026, 10, 1)),
        (
            FilterField.MODIFIED_AT,
            ON_OR_AFTER,
            datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=3))),
        ),
        # Media types are stored lowercase, without parameters.
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"Text/Markdown"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/plain; charset=utf-8"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/ plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/" + "x" * 300})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"téxt/plain"})),
        # Control characters: the NUL once reached PostgreSQL as a DataError.
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/\x00plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/\x01plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/plain\x7f"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/pl'ain"})),
        # Too many values for one condition.
        (
            FilterField.SOURCE,
            ANY_OF,
            frozenset(SourceId(UUID(int=i)) for i in range(MAX_FILTER_VALUES + 1)),
        ),
    ],
)
def test_anything_outside_the_allowlist_is_refused(
    field: object, operator: object, value: object
) -> None:
    with pytest.raises(UnsupportedFilter):
        MetadataCondition(
            cast(FilterField, field),
            cast(FilterOperator, operator),
            cast(FilterValue, value),
        )


def test_conditions_on_different_fields_are_kept_in_order() -> None:
    source = MetadataCondition(FilterField.SOURCE, ANY_OF, frozenset({SOURCE}))
    recent = MetadataCondition(FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE)

    assert checked_conditions([source, recent]) == (source, recent)
    assert checked_conditions(()) == ()


@pytest.mark.parametrize(
    "conditions",
    [
        # Two conditions on one field.
        (
            MetadataCondition(
                FilterField.LANGUAGE, ANY_OF, frozenset({Language.ENGLISH})
            ),
            MetadataCondition(
                FilterField.LANGUAGE, ANY_OF, frozenset({Language.UKRAINIAN})
            ),
        ),
        # Not conditions at all.
        ("language = 'uk'",),
        "language = 'uk'",
        None,
        iter(()),
    ],
)
def test_a_condition_list_must_hold_one_condition_per_field(
    conditions: object,
) -> None:
    with pytest.raises(UnsupportedFilter):
        checked_conditions(cast(tuple[MetadataCondition, ...], conditions))


def test_a_condition_altered_after_it_was_made_is_refused() -> None:
    condition = MetadataCondition(
        FilterField.LANGUAGE, ANY_OF, frozenset({Language.ENGLISH})
    )
    # Frozen dataclasses can still be changed this way.
    object.__setattr__(condition, "operator", "like")

    with pytest.raises(UnsupportedFilter):
        checked_conditions([condition])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (FilterField.SOURCE, frozenset({SOURCE})),
        (FilterField.DOCUMENT, frozenset({DOCUMENT})),
    ],
)
def test_an_id_altered_inside_a_condition_is_refused(
    field: FilterField, value: FilterValue
) -> None:
    condition = MetadataCondition(field, ANY_OF, value)
    (held,) = cast(frozenset[SourceId | DocumentId], condition.value)
    # Once reached PostgreSQL and failed there with a DataError.
    object.__setattr__(held, "value", "' OR 1=1 --")

    with pytest.raises(UnsupportedFilter):
        checked_conditions([condition])


def test_a_condition_keeps_its_own_copies_of_the_ids() -> None:
    document = DocumentId(UUID(int=2))
    condition = MetadataCondition(FilterField.DOCUMENT, ANY_OF, frozenset({document}))
    # The caller, or another task while the search waits, alters its own ID.
    object.__setattr__(document, "value", "' OR 1=1 --")

    (checked,) = checked_conditions([condition])

    assert checked.value == frozenset({DocumentId(UUID(int=2))})


@pytest.mark.parametrize("number", ["' OR true --", -1, 2**200, None])
def test_a_uuid_altered_inside_an_id_is_refused(number: object) -> None:
    uuid = UUID(int=2)
    object.__setattr__(uuid, "int", number)

    with pytest.raises(UnsupportedFilter):
        MetadataCondition(FilterField.DOCUMENT, ANY_OF, frozenset({DocumentId(uuid)}))


def test_an_id_without_its_value_is_refused() -> None:
    document = DocumentId(UUID(int=2))
    documents = frozenset({document})
    object.__delattr__(document, "value")

    with pytest.raises(UnsupportedFilter):
        MetadataCondition(FilterField.DOCUMENT, ANY_OF, documents)


class ShiftingZone(tzinfo):
    """Says UTC while it is checked, then moves five hours."""

    def __init__(self) -> None:
        self.calls = 0

    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        self.calls += 1
        return timedelta(0) if self.calls <= 2 else timedelta(hours=-5)

    @override
    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


class BrokenZone(tzinfo):
    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise RuntimeError("no offset")


class WrongTypeZone(tzinfo):
    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return cast(timedelta, 0)


def test_a_moment_keeps_no_caller_time_zone() -> None:
    shifting = datetime(2026, 10, 1, tzinfo=ShiftingZone())

    condition = MetadataCondition(FilterField.MODIFIED_AT, ON_OR_AFTER, shifting)

    # Once kept the zone, so the boundary moved after it was checked.
    assert condition.value == datetime(2026, 10, 1, tzinfo=UTC)
    assert cast(datetime, condition.value).tzinfo is UTC


@pytest.mark.parametrize("zone", [BrokenZone(), WrongTypeZone()])
def test_a_time_zone_that_cannot_answer_is_refused(zone: tzinfo) -> None:
    with pytest.raises(UnsupportedFilter):
        MetadataCondition(
            FilterField.MODIFIED_AT, ON_OR_AFTER, datetime(2026, 10, 1, tzinfo=zone)
        )
