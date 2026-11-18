"""The eligibility predicate takes only a sound Authorization Context.

Every search builds its permission check from the reader. A subclass could
answer with other Groups than the ones it was checked with, a look-alike was
never checked at all, and a context changed after it was made skipped its
checks, so all three are refused before any SQL is built.
"""

from typing import Any, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from knowledge_service.access import AuthorizationContext, InvalidAuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks

ALICE = UserId(UUID(int=0xA1))
ENGINEERING = GroupId(UUID(int=0xE1))
EVERY_GROUP = frozenset(GroupId(UUID(int=n)) for n in range(1, 4))


def wider_than_checked() -> Any:
    """A subclass that stores the Group it is checked with, then reads others."""
    stored: Any = AuthorizationContext.__dict__["group_ids"]

    def read(self: object) -> frozenset[GroupId]:
        return EVERY_GROUP

    def write(self: object, value: frozenset[GroupId]) -> None:
        stored.__set__(self, value)

    wider = type(
        "Wider",
        (AuthorizationContext,),
        {"__slots__": (), "group_ids": property(read, write)},
    )
    return wider(user_id=ALICE, group_ids=frozenset({ENGINEERING}))


def changed_after_checking(field: str, value: object) -> AuthorizationContext:
    """A real context whose field was replaced, skipping its own checks."""
    context = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))
    object.__setattr__(context, field, value)
    return context


class LookAlike:
    user_id = ALICE
    group_ids = EVERY_GROUP


@pytest.mark.parametrize(
    "reader",
    [
        wider_than_checked(),
        LookAlike(),
        None,
        changed_after_checking("group_ids", set(EVERY_GROUP)),
        changed_after_checking("group_ids", frozenset({ALICE})),
        changed_after_checking("user_id", ENGINEERING),
    ],
    ids=[
        "subclass",
        "look-alike",
        "none",
        "groups-set",
        "user-as-group",
        "group-as-user",
    ],
)
def test_anything_but_an_authorization_context_is_refused(reader: Any) -> None:
    with pytest.raises(InvalidAuthorizationContext):
        eligible_chunks_predicate(cast(AuthorizationContext, reader))


def test_the_reader_reaches_the_sql_only_as_bound_parameters() -> None:
    reader = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))

    compiled = eligible_chunks_predicate(reader).compile(dialect=postgresql.dialect())

    assert str(ALICE.value) not in str(compiled)
    assert ALICE.value in compiled.params.values()
    assert [ENGINEERING.value] in compiled.params.values()


def test_the_predicate_ties_each_chunk_to_its_own_document() -> None:
    # A statement that reads chunks but forgets to join documents must not
    # pair every Chunk with every Document the reader may see.
    reader = AuthorizationContext(user_id=ALICE)
    statement = sa.select(chunks.c.chunk_id).where(eligible_chunks_predicate(reader))

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "documents.document_id = chunks.document_id" in sql
