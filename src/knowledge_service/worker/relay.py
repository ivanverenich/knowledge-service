"""Queue due retries, then publish Jobs no message names yet: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come. It exits 0 when every Job it found was
published or skipped, and 1 when the queue refused a message; the rest wait
for the next pass.
"""

import asyncio
import sys
from datetime import UTC, datetime

from knowledge_service.database import create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import (
    JobPublisher,
    RelayReport,
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobRunner,
    declare_job_actors,
)


async def _relay(
    settings: Settings, publisher: JobPublisher
) -> tuple[tuple[JobId, ...], RelayReport]:
    runtime = create_database_runtime(settings)
    now = datetime.now(UTC)
    try:
        requeued = await requeue_due_retries(runtime, now=now)
        report = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    finally:
        await runtime.dispose()
    return requeued, report


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        requeued, report = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    sys.stdout.write(
        f"requeued {len(requeued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
