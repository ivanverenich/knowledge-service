"""Queue due retries and due synchronizations, then publish: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come, and what queues each enabled Source's next
synchronization. It exits 0 when every Job it found was published or
skipped, and 1 when the queue refused a message or scheduling failed; the
rest wait for the next pass. A failed scheduling step does not stop the
publishing step.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelayPass:
    """What one relay pass did, in the order it did it."""

    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
    published: RelayReport


async def relay_once(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
    now: datetime,
    schedule: SynchronizationSchedule,
) -> RelayPass:
    """Queue due retries, queue due synchronizations, then publish.

    A failure while scheduling is logged by type and reported; publishing
    still runs, so other Jobs are not held up by it.
    """
    requeued = await requeue_due_retries(runtime, now=now)
    try:
        scheduled = await schedule_due_synchronizations(
            runtime, now=now, schedule=schedule
        )
        schedule_failed = False
    except Exception as error:
        logger.warning("scheduling synchronizations failed: %s", type(error).__name__)
        scheduled = ScheduleReport(queued=(), already_open=())
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
        published=published,
    )


async def _relay(settings: Settings, publisher: JobPublisher) -> RelayPass:
    runtime = create_database_runtime(settings)
    schedule = SynchronizationSchedule(
        interval=timedelta(minutes=settings.synchronization_interval_minutes)
    )
    try:
        return await relay_once(
            runtime, publisher=publisher, now=datetime.now(UTC), schedule=schedule
        )
    finally:
        await runtime.dispose()


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        done = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    report = done.published
    sys.stdout.write(
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed or done.schedule_failed else 0


if __name__ == "__main__":
    sys.exit(main())
