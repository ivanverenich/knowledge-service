"""Tests for the local directory Source adapter."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecord,
    SourceUnavailable,
)

PUBLIC = SourceAccess(public=True)


def write(root: Path, relative: str, content: bytes) -> Path:
    """Create one file under `root`, making parent directories as needed."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def read_all(source: SourcePort, *, page_size: int = 100) -> list[SourceRecord]:
    """Follow checkpoints until the Source reports it is exhausted."""
    records: list[SourceRecord] = []
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        records.extend(page.records)
        if page.next_checkpoint is None:
            return records
        checkpoint = page.next_checkpoint


def ids(records: list[SourceRecord]) -> list[str]:
    return [record.external_id for record in records]


async def test_nested_files_come_back_sorted_by_relative_path(tmp_path: Path) -> None:
    write(tmp_path, "b.txt", b"second")
    write(tmp_path, "a/z.txt", b"nested")
    write(tmp_path, "a.txt", b"first")
    write(tmp_path, "notes.rtf", b"{\\rtf1 unsupported}")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert ids(records) == ["a.txt", "a/z.txt", "b.txt"]
    assert records[1].content == b"nested"
    assert records[1].media_type == "text/plain"
    assert records[1].access == PUBLIC


async def test_a_record_carries_file_metadata(tmp_path: Path) -> None:
    path = write(tmp_path, "guide.txt", b"hello")
    os.utime(path, (1_700_000_000, 1_700_000_000))

    [record] = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert record.metadata is not None
    assert record.metadata.title == "guide"
    assert record.metadata.size_bytes == 5
    assert record.metadata.modified_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


async def test_pages_resume_after_the_last_delivered_path(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        write(tmp_path, name, name.encode())
    source = DirectorySource(tmp_path, access=PUBLIC)

    first = await source.fetch_page(checkpoint=None, page_size=2)
    rest = await source.fetch_page(checkpoint=first.next_checkpoint, page_size=2)

    assert ids(list(first.records)) == ["a.txt", "b.txt"]
    assert ids(list(rest.records)) == ["c.txt"]
    assert rest.next_checkpoint is None


async def test_a_modified_file_keeps_its_identity_and_changes_version(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "doc.txt", b"before")
    source = DirectorySource(tmp_path, access=PUBLIC)
    [before] = await read_all(source)

    path.write_bytes(b"after")
    [after] = await read_all(source)

    assert after.external_id == before.external_id
    assert after.version != before.version
    assert after.content == b"after"


async def test_a_deleted_file_is_absent_from_the_next_read(tmp_path: Path) -> None:
    write(tmp_path, "keep.txt", b"keep")
    gone = write(tmp_path, "gone.txt", b"gone")
    source = DirectorySource(tmp_path, access=PUBLIC)
    assert ids(await read_all(source)) == ["gone.txt", "keep.txt"]

    gone.unlink()

    assert ids(await read_all(source)) == ["keep.txt"]


async def test_a_blank_file_name_falls_back_to_the_path_as_title(
    tmp_path: Path,
) -> None:
    write(tmp_path, " .txt", b"untitled")

    [record] = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert record.metadata is not None
    assert record.metadata.title == " .txt"


async def test_each_supported_extension_gets_its_media_type(tmp_path: Path) -> None:
    for name in ("a.txt", "b.md", "c.html", "d.pdf", "e.MD"):
        write(tmp_path, name, b"content")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert [(record.external_id, record.media_type) for record in records] == [
        ("a.txt", "text/plain"),
        ("b.md", "text/markdown"),
        ("c.html", "text/html"),
        ("d.pdf", "application/pdf"),
        ("e.MD", "text/markdown"),
    ]


async def test_file_bytes_are_delivered_unchanged(tmp_path: Path) -> None:
    # Decoding belongs to normalization, so a byte-order mark and bytes that
    # are not UTF-8 both reach the record as they are on disk.
    write(tmp_path, "bom.txt", b"\xef\xbb\xbf" + "привіт".encode())
    write(tmp_path, "bad.txt", b"\xff\xfe broken")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert [record.content for record in records] == [
        b"\xff\xfe broken",
        b"\xef\xbb\xbf" + "привіт".encode(),
    ]


async def test_symbolic_links_are_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    write(outside, "secret.txt", b"secret")
    write(root, "inside.txt", b"inside")
    (root / "link.txt").symlink_to(outside / "secret.txt")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    records = await read_all(DirectorySource(root, access=PUBLIC))

    assert ids(records) == ["inside.txt"]


async def test_a_checkpoint_is_never_opened_as_a_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write(tmp_path, "secret.txt", b"secret")
    write(root, "inside.txt", b"inside")
    source = DirectorySource(root, access=PUBLIC)

    page = await source.fetch_page(checkpoint="../secret.txt", page_size=10)

    assert ids(list(page.records)) == ["inside.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
async def test_an_unreadable_folder_is_unavailable_not_deleted(
    tmp_path: Path,
) -> None:
    write(tmp_path, "b.txt", b"visible")
    locked = write(tmp_path, "sub/a.txt", b"hidden").parent
    locked.chmod(0)
    try:
        with pytest.raises(SourceUnavailable):
            await read_all(DirectorySource(tmp_path, access=PUBLIC))
    finally:
        locked.chmod(0o755)


async def test_a_missing_directory_is_unavailable(tmp_path: Path) -> None:
    source = DirectorySource(tmp_path / "missing", access=PUBLIC)

    with pytest.raises(SourceUnavailable):
        await source.fetch_page(checkpoint=None, page_size=10)


async def test_page_size_must_be_positive(tmp_path: Path) -> None:
    source = DirectorySource(tmp_path, access=PUBLIC)

    with pytest.raises(InvalidSource, match="at least 1"):
        await source.fetch_page(checkpoint=None, page_size=0)
