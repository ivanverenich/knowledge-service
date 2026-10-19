"""A Source adapter that reads document files from one local directory.

The adapter delivers each file's bytes unchanged, with a media type chosen by
its extension. Decoding and parsing belong to normalization, so a file that
cannot be read as text is quarantined there instead of failing the whole read.

A file's identity is its path relative to the root, written with forward
slashes. Records come back sorted by that path, and the checkpoint is the last
path delivered. Symbolic links are never followed, because a link inside the
root can point anywhere on the machine.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)

_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".pdf": "application/pdf",
}


class DirectorySource:
    """SourcePort over the document files under one local directory."""

    def __init__(self, root: Path, *, access: SourceAccess) -> None:
        self._root = root
        self._access = access

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if page_size < 1:
            raise InvalidSource("page size must be at least 1")
        return await asyncio.to_thread(self._read_page, checkpoint, page_size)

    def _read_page(self, checkpoint: str | None, page_size: int) -> SourcePage:
        root = self._resolved_root()
        try:
            listed = _list_files(root)
        except OSError as error:
            raise SourceUnavailable("source directory could not be listed") from error
        remaining = [
            external_id
            for external_id in listed
            if checkpoint is None or external_id > checkpoint
        ]
        selected = remaining[:page_size]
        records = tuple(
            record
            for external_id in selected
            if (record := self._read(root, external_id)) is not None
        )
        next_checkpoint = selected[-1] if len(remaining) > page_size else None
        return SourcePage(records=records, next_checkpoint=next_checkpoint)

    def _resolved_root(self) -> Path:
        if not self._root.is_dir():
            raise SourceUnavailable("source directory is not available")
        return self._root.resolve()

    def _read(self, root: Path, external_id: str) -> SourceRecord | None:
        path = root / external_id
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                return None
            raw = path.read_bytes()
            modified = path.stat().st_mtime
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SourceUnavailable("source file could not be read") from error
        return SourceRecord.present(
            external_id=external_id,
            version=hashlib.sha256(raw).hexdigest(),
            media_type=_MEDIA_TYPES[path.suffix.lower()],
            content=raw,
            access=self._access,
            metadata=SourceMetadata(
                title=path.stem.strip() or external_id,
                modified_at=datetime.fromtimestamp(modified, tz=UTC),
                size_bytes=len(raw),
            ),
        )


def _list_files(root: Path) -> list[str]:
    """Return every supported file under `root` as a sorted relative path.

    A folder that cannot be read raises instead of being skipped: a missing
    file would otherwise look deleted.
    """
    external_ids: list[str] = []
    for directory, _, filenames in root.walk(on_error=_raise):
        for filename in filenames:
            path = directory / filename
            if path.is_symlink() or path.suffix.lower() not in _MEDIA_TYPES:
                continue
            external_ids.append(path.relative_to(root).as_posix())
    return sorted(external_ids)


def _raise(error: OSError) -> None:
    raise error
