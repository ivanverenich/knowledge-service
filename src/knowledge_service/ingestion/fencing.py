"""A check that runs first inside each write of a synchronization pass.

A guard is awaited at the start of a write's own transaction. It may take
locks and read the database, and raising from it rolls the write back, so
nothing the pass writes can land after the guard says it should not.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

type WriteGuard = Callable[[AsyncConnection], Awaitable[None]]
