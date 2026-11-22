"""The worker process: `dramatiq knowledge_service.worker.main`.

Importing this module builds the broker from settings, makes it Dramatiq's
global broker, and declares every actor on it. Dramatiq then reads the
declared queues.
"""

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.actors import declare_actors, log_ping
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import HANDLERS, JobRunner, declare_job_actors

settings = Settings()
broker = broker_from_settings(settings)
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
job_actors = declare_job_actors(
    broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
)
