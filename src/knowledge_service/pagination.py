"""Pages of a list: how many items, and where the next page starts.

Every paginated route takes `limit` and `cursor` and answers
`{"items": [...], "next_cursor": ...}`. A cursor names the last item of the
page before, so the next page starts after it even when items are added in
between. Clients must treat it as opaque. It is not signed: it only says
where to start in a list its caller may read whole anyway, and the server
checks every field before using it.
"""

import base64
import json
import re
from collections.abc import Mapping
from typing import cast

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_CHARACTERS = 512
_CURSOR_VERSION = 1
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS = re.compile(r"[1-9][0-9]{0,2}")


class InvalidPage(ValueError):
    """A page size or cursor the API does not accept."""


def page_size(raw: str | None) -> int:
    """`limit` as a number of items: absent means the default."""
    if raw is None:
        return DEFAULT_PAGE_SIZE
    if not _DIGITS.fullmatch(raw) or not 1 <= int(raw) <= MAX_PAGE_SIZE:
        raise InvalidPage(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}")
    return int(raw)


def encode_cursor(listing: str, position: Mapping[str, str]) -> str:
    """The cursor that starts the next page of `listing` after `position`."""
    document = json.dumps(
        [_CURSOR_VERSION, listing, dict(position)],
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(document.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, listing: str, keys: frozenset[str]) -> dict[str, str]:
    """The position a cursor of `listing` names, with exactly `keys`, all text.

    Raises `InvalidPage` for anything `encode_cursor` would not have written
    for this list: a cursor too long, not base64url, not JSON, of another
    version or list, with other fields, or another spelling of the same ones.
    """
    if len(cursor) > MAX_CURSOR_CHARACTERS or not _BASE64URL.fullmatch(cursor):
        raise InvalidPage("the cursor is not one this API gave")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        document: object = json.loads(raw.decode())
    # Bad base64, bad UTF-8, and bad JSON are all a ValueError.
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    position = _position(document, listing, keys)
    # One spelling only: key order, spaces, or spare base64 bits differ.
    if position is None or encode_cursor(listing, position) != cursor:
        raise InvalidPage("the cursor is not one this API gave")
    return position


def _position(
    document: object, listing: str, keys: frozenset[str]
) -> dict[str, str] | None:
    """`[1, listing, {key: text, ...}]` with exactly `keys`, else None."""
    if type(document) is not list:
        return None
    parts = cast(list[object], document)
    # `type(...) is int`, because JSON `true` would equal 1.
    if len(parts) != 3 or type(parts[0]) is not int or parts[0] != _CURSOR_VERSION:
        return None
    if parts[1] != listing or type(parts[2]) is not dict:
        return None
    fields = cast(dict[object, object], parts[2])
    position = {
        key: value
        for key, value in fields.items()
        if type(key) is str and type(value) is str
    }
    if len(position) != len(fields) or frozenset(position) != keys:
        return None
    return position
