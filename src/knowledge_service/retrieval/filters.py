"""Typed conditions that narrow retrieval to some Documents.

A caller never writes SQL. It names a field and an operator from a fixed
list, with a value of the type that field takes, and the persistence layer
turns each pair into a fixed SQL condition with bound parameters:

| Field | Operator | Value |
|---|---|---|
| `SOURCE` | `ANY_OF` | a non-empty frozenset of `SourceId` |
| `DOCUMENT` | `ANY_OF` | a non-empty frozenset of `DocumentId` |
| `MEDIA_TYPE` | `ANY_OF` | a non-empty frozenset of media types (`text/markdown`) |
| `LANGUAGE` | `ANY_OF` | a non-empty frozenset of `Language` |
| `MODIFIED_AT` | `ON_OR_AFTER` | a UTC `datetime` |

Every other pair, value type, or a second condition on the same field raises
`UnsupportedFilter`. Conditions only narrow: they are added to the
eligibility check, never in place of it, so a filter cannot show a Document
the reader may not see.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID

from knowledge_service.documents import MAX_MEDIA_TYPE_CHARACTERS
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.language import Language

# Bounds the values one condition sends to the database.
MAX_FILTER_VALUES = 100

# A canonical media type: lowercase type/subtype from the characters RFC 6838
# allows in a name, so no space, quote, control character, or parameter.
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")


class UnsupportedFilter(ValueError):
    """A condition is not one of the allowed field, operator, and value types."""


class FilterField(StrEnum):
    """What a condition looks at."""

    SOURCE = "source"
    DOCUMENT = "document"
    MEDIA_TYPE = "media_type"
    LANGUAGE = "language"
    MODIFIED_AT = "modified_at"


class FilterOperator(StrEnum):
    """How a condition compares."""

    ANY_OF = "any_of"
    ON_OR_AFTER = "on_or_after"


_ALLOWED_OPERATOR = {
    FilterField.SOURCE: FilterOperator.ANY_OF,
    FilterField.DOCUMENT: FilterOperator.ANY_OF,
    FilterField.MEDIA_TYPE: FilterOperator.ANY_OF,
    FilterField.LANGUAGE: FilterOperator.ANY_OF,
    FilterField.MODIFIED_AT: FilterOperator.ON_OR_AFTER,
}

_ID_FIELDS = frozenset({FilterField.SOURCE, FilterField.DOCUMENT})

_ELEMENT_TYPE: dict[FilterField, type] = {
    FilterField.SOURCE: SourceId,
    FilterField.DOCUMENT: DocumentId,
    FilterField.MEDIA_TYPE: str,
    FilterField.LANGUAGE: Language,
}

type FilterValue = (
    frozenset[SourceId]
    | frozenset[DocumentId]
    | frozenset[str]
    | frozenset[Language]
    | datetime
)


@dataclass(frozen=True, slots=True)
class MetadataCondition:
    """One allowed condition, such as "language is any of uk, en"."""

    field: FilterField
    operator: FilterOperator
    value: FilterValue

    def __post_init__(self) -> None:
        # A plain string would compare equal to the enum, so exact types.
        if type(self.field) is not FilterField:
            raise UnsupportedFilter("the field is not a FilterField")
        if type(self.operator) is not FilterOperator:
            raise UnsupportedFilter("the operator is not a FilterOperator")
        if self.operator is not _ALLOWED_OPERATOR[self.field]:
            raise UnsupportedFilter(
                f"{self.field.value} does not support {self.operator.value}"
            )
        if self.field is FilterField.MODIFIED_AT:
            # Keep a plain UTC copy, never the caller's tzinfo object, which
            # could answer differently once it has been checked.
            object.__setattr__(self, "value", _utc_moment(self.value))
        elif self.field in _ID_FIELDS:
            object.__setattr__(self, "value", _copied_ids(self.field, self.value))
        else:
            _check_values(self.field, self.value)


def checked_conditions(
    conditions: Sequence[MetadataCondition],
) -> tuple[MetadataCondition, ...]:
    """Return the conditions checked again, refusing anything but one per field.

    Each condition is rebuilt, so one changed after it was made (a frozen
    dataclass can still be altered through `object.__setattr__`) is checked
    again. Two conditions on one field would have to be combined somehow;
    refusing them keeps the meaning obvious: every condition must hold.
    """
    if type(conditions) is not tuple and type(conditions) is not list:
        raise UnsupportedFilter("conditions must be a tuple or a list")
    given = tuple(cast(Sequence[object], conditions))
    if any(type(condition) is not MetadataCondition for condition in given):
        raise UnsupportedFilter("every condition must be a MetadataCondition")
    checked = tuple(
        MetadataCondition(c.field, c.operator, c.value)
        for c in cast(tuple[MetadataCondition, ...], given)
    )
    fields = [condition.field for condition in checked]
    if len(set(fields)) != len(fields):
        raise UnsupportedFilter("at most one condition per field")
    return checked


def _utc_moment(value: object) -> datetime:
    if type(value) is not datetime:
        raise UnsupportedFilter("modified_at takes a datetime")
    try:
        offset = value.utcoffset()
    except Exception:  # a tzinfo is caller code and may raise anything
        raise UnsupportedFilter("modified_at has an unusable time zone") from None
    if offset != timedelta(0):
        raise UnsupportedFilter("modified_at must be a UTC-aware datetime")
    return value.replace(tzinfo=UTC)


def _check_values(field: FilterField, value: object) -> None:
    if type(value) is not frozenset:
        raise UnsupportedFilter(f"{field.value} takes a frozenset of values")
    values = cast(frozenset[object], value)
    if not values:
        raise UnsupportedFilter(f"{field.value} needs at least one value")
    if len(values) > MAX_FILTER_VALUES:
        raise UnsupportedFilter(
            f"{field.value} takes at most {MAX_FILTER_VALUES} values"
        )
    expected = _ELEMENT_TYPE[field]
    if any(type(element) is not expected for element in values):
        raise UnsupportedFilter(
            f"every {field.value} value must be a {expected.__name__}"
        )
    if field is FilterField.MEDIA_TYPE and not all(
        _is_media_type(cast(str, element)) for element in values
    ):
        raise UnsupportedFilter("a media type is lowercase type/subtype")


def _copied_ids(
    field: FilterField, value: object
) -> frozenset[SourceId] | frozenset[DocumentId]:
    """Return new IDs rebuilt from the checked numbers of the given ones.

    An ID, and even the UUID inside it, can be altered after it was made,
    also while a search waits on the database. The condition keeps these
    copies, which no caller holds, so what was checked is what is sent.
    """
    _check_values(field, value)
    kind = cast(type[SourceId] | type[DocumentId], _ELEMENT_TYPE[field])
    copies: set[SourceId | DocumentId] = set()
    for element in cast(frozenset[object], value):
        uuid = getattr(element, "value", None)
        number = getattr(uuid, "int", None) if type(uuid) is UUID else None
        if type(number) is not int or not 0 <= number < 2**128:
            raise UnsupportedFilter(f"every {field.value} value must hold a UUID")
        copies.add(kind(UUID(int=number)))
    return cast(frozenset[SourceId] | frozenset[DocumentId], frozenset(copies))


def _is_media_type(value: str) -> bool:
    return (
        len(value) <= MAX_MEDIA_TYPE_CHARACTERS
        and _MEDIA_TYPE.fullmatch(value) is not None
    )
