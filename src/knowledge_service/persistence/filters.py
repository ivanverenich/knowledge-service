"""Turn checked retrieval filters into fixed SQL conditions."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, assert_never, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.language import Language
from knowledge_service.persistence.schema import document_metadata, documents
from knowledge_service.retrieval.filters import FilterField, MetadataCondition


def metadata_predicate(
    conditions: Sequence[MetadataCondition],
) -> sa.ColumnElement[bool]:
    """The condition a Chunk's Document must meet for every given filter.

    Each allowed field maps to one fixed SQL condition here, and every value
    is a bound parameter. With no conditions it is simply true. The
    statement using it must join `chunks` to `documents`. A Document with no
    metadata row matches no media type, language, or modified-since
    condition; a Source or Document condition still matches it.
    """
    return sa.and_(sa.true(), *(_condition_clause(c) for c in conditions))


def _condition_clause(condition: MetadataCondition) -> sa.ColumnElement[bool]:
    match condition.field:
        case FilterField.SOURCE:
            source_ids = cast(frozenset[SourceId], condition.value)
            return documents.c.source_id == _any_of(
                "source_ids", sorted(s.value for s in source_ids), sa.Uuid()
            )
        case FilterField.DOCUMENT:
            document_ids = cast(frozenset[DocumentId], condition.value)
            return documents.c.document_id == _any_of(
                "document_ids", sorted(d.value for d in document_ids), sa.Uuid()
            )
        case FilterField.MEDIA_TYPE:
            media_types = cast(frozenset[str], condition.value)
            return _described(
                document_metadata.c.media_type
                == _any_of("media_types", sorted(media_types), sa.Text())
            )
        case FilterField.LANGUAGE:
            languages = cast(frozenset[Language], condition.value)
            return _described(
                document_metadata.c.language
                == _any_of(
                    "languages", sorted(lang.value for lang in languages), sa.Text()
                )
            )
        case FilterField.MODIFIED_AT:
            since = cast(datetime, condition.value)
            # NULL >= since is not true, so an unknown time never matches.
            return _described(document_metadata.c.modified_at >= since)
        case _:
            assert_never(condition.field)


def _any_of(
    name: str, values: Sequence[object], item_type: sa.types.TypeEngine[Any]
) -> sa.ColumnElement[Any]:
    # One array parameter, however many values; unique keeps names apart.
    return sa.any_(
        sa.bindparam(name, list(values), type_=ARRAY(item_type), unique=True)
    )


def _described(clause: sa.ColumnElement[bool]) -> sa.ColumnElement[bool]:
    return sa.exists().where(
        document_metadata.c.document_id == documents.c.document_id, clause
    )
