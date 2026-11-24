"""Build every Job handler the worker runs, from settings.

The purge handler needs only the database. The synchronization handler also
needs a way to open each stored Source, a lease store, and the principal
directory, which come from settings here, once, when the worker starts. A
missing Redis URL or an unreadable directory file stops the worker at start,
not at its first Job.
"""

import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from knowledge_service.ingestion.grant_normalization import (
    InvalidPrincipalDirectory,
    PrincipalDirectory,
)
from knowledge_service.ingestion.synchronization_jobs import (
    LeaseStoreFactory,
    SynchronizationJobs,
    open_source,
)
from knowledge_service.jobs import JobKind
from knowledge_service.leases import LeaseStore, RedisLeaseStore, lease_client
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import WorkerConfigurationError
from knowledge_service.worker.jobs import HANDLERS, RUNNABLE_KINDS, JobHandler


def load_principal_directory(path: Path) -> PrincipalDirectory:
    """Read a directory file: `{"issuer": ..., "users": {...}, "groups": {...}}`."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WorkerConfigurationError(
            "the principal directory file cannot be read as JSON"
        ) from error
    if not isinstance(data, dict):
        raise WorkerConfigurationError("the principal directory must be an object")
    fields = cast(dict[str, Any], data)
    issuer = fields.get("issuer")
    if not isinstance(issuer, str):
        raise WorkerConfigurationError("the principal directory needs an issuer")
    try:
        return PrincipalDirectory(
            issuer=issuer,
            users=fields.get("users", {}),
            groups=fields.get("groups", {}),
        )
    except InvalidPrincipalDirectory as error:
        raise WorkerConfigurationError(str(error)) from error


def redis_leases(redis_url: str) -> LeaseStoreFactory:
    """Lease stores on `redis_url`, one client per run, closed after it."""

    @asynccontextmanager
    async def leases() -> AsyncGenerator[LeaseStore]:
        client = lease_client(redis_url)
        try:
            yield RedisLeaseStore(client)
        finally:
            await client.aclose()

    return leases


def job_handlers(settings: Settings) -> Mapping[JobKind, JobHandler]:
    """A handler for every kind in `RUNNABLE_KINDS`."""
    if settings.redis_url is None:
        raise WorkerConfigurationError(
            "KNOWLEDGE_SERVICE_REDIS_URL is required to synchronize sources"
        )
    directory_file = settings.principal_directory_file
    handlers: dict[JobKind, JobHandler] = {
        **HANDLERS,
        JobKind.SYNCHRONIZE_SOURCE: SynchronizationJobs(
            open_source=open_source,
            leases=redis_leases(settings.redis_url.get_secret_value()),
            principal_directory=(
                None
                if directory_file is None
                else load_principal_directory(directory_file)
            ),
            full_pass_interval=timedelta(
                hours=settings.full_synchronization_interval_hours
            ),
        ),
    }
    if frozenset(handlers) != RUNNABLE_KINDS:
        raise WorkerConfigurationError("a runnable job kind has no handler")
    return handlers
