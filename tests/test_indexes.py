"""Tests for the indexes the named access patterns read through."""

import pytest
import sqlalchemy as sa

from knowledge_service.persistence import metadata

ACCESS_PATTERN_INDEXES = {
    "ix_access_grants_subject_document",
    "ix_audit_events_target_occurred_at",
    "ix_conversations_retention_deadline",
    "ix_synchronization_runs_source_started_at",
    "uq_synchronization_runs_running_source",
}


class ForeignKeyNotIndexed(AssertionError):
    """A foreign key does not start any index, so a parent delete scans the child."""


def leading_index_columns(table: sa.Table) -> set[str]:
    """Return the first column of every index that serves the table."""
    columns = {index.columns[0].name for index in table.indexes}
    for constraint in table.constraints:
        if isinstance(constraint, (sa.PrimaryKeyConstraint, sa.UniqueConstraint)):
            first = list(constraint.columns)
            if first:
                columns.add(first[0].name)
    return columns


def assert_every_foreign_key_leads_an_index(table: sa.Table) -> None:
    """Raise when a foreign key has no index to serve it."""
    leading = leading_index_columns(table)
    for column in table.columns:
        if column.foreign_keys and column.name not in leading:
            raise ForeignKeyNotIndexed(f"{table.name}.{column.name}")


def test_only_the_named_access_patterns_have_an_index() -> None:
    declared = {
        index.name for table in metadata.sorted_tables for index in table.indexes
    }

    assert declared == ACCESS_PATTERN_INDEXES


def test_every_foreign_key_leads_an_index() -> None:
    for table in metadata.sorted_tables:
        assert_every_foreign_key_leads_an_index(table)


def test_the_retention_index_covers_only_live_conversations() -> None:
    conversations = metadata.tables["conversations"]
    index = next(
        index
        for index in conversations.indexes
        if index.name == "ix_conversations_retention_deadline"
    )

    assert [column.name for column in index.columns] == ["retention_deadline"]
    assert str(index.dialect_options["postgresql"]["where"]) == "deleted_at is null"


def test_a_foreign_key_with_no_index_is_reported() -> None:
    unindexed = sa.Table(
        "probe_child",
        sa.MetaData(),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("probe_parent.parent_id")),
    )

    with pytest.raises(ForeignKeyNotIndexed, match=r"probe_child\.parent_id"):
        assert_every_foreign_key_leads_an_index(unindexed)
