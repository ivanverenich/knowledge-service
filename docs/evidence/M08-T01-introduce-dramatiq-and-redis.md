# M08-T01 — Introduce Dramatiq and Redis evidence

## Invariant

A worker runs a message only through an actor declared on a broker built
by `build_broker`: JSON messages, the `knowledge-service.v1` namespace,
named queues, the actor's own time limit, and no Dramatiq retries, whatever
options the message carries. The worker rechecks every argument it reads
from Redis. Readiness is true only while Redis answers PING within one
deadline, and every failure reads as "not ready", never as a crash.
PostgreSQL, not Redis, stays the record of every Job.

## Prerequisites read

M07-T16 evidence (the previous completed task), ADR 0011 (Redis for
ephemeral coordination only), ADR 0005 (at-least-once synchronization),
`src/knowledge_service/jobs.py` and `persistence/jobs.py` (the stored Job),
and the Dramatiq 2.2.1 source for `RedisBroker`, `TimeLimit`, and `Retries`.

## Owners

- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Timeouts: the actor's time limit; Redis calls make one attempt each.
- Authorization: none yet; `ping` reads and changes no data.

## Expected failing check

Before the change, `uv run pytest tests/test_worker.py -q` fails with
`ERROR: file or directory not found: tests/test_worker.py`.

## Predictions

Not recorded before the change: the learner went straight to the
implementation, so no prediction can be compared with its result. The
observed answers are in Verification: `not ready: the Redis broker did not
answer`; one run, then the dead-letter queue; every queued message is lost;
5 tests fail.

- What `make worker-ready` prints when the Redis container is stopped: not recorded
- How many times a ping that always fails runs before it reaches the dead-letter queue: not recorded
- What happens to queued messages when you run `make redis-down`: not recorded
- How many tests in `tests/test_worker.py` fail if `ActorOwnsOptions()` is removed from `queue_middleware`: not recorded

## Design

- **Package.** `src/knowledge_service/worker/` is the queue transport
  adapter. Dramatiq is imported only here; domain and application modules
  stay free of it, as `docs/architecture.md` requires.
- **Broker** (`broker.py`). `build_broker` sets the JSON encoder, builds a
  `RedisBroker` under the `knowledge-service.v1` namespace with a client
  that has a 2 s connect timeout, a 5 s socket timeout, and one attempt per
  call, and declares the `maintenance` and `synchronization` queues.
  `queue_middleware` is the whole chain: `ActorOwnsOptions`, `AgeLimit`
  (one day), `TimeLimit` (60 s default), `ShutdownNotifications`, and
  `Retries(max_retries=0)`. `ActorOwnsOptions` deletes every option in
  `broker.actor_options` from an arriving message, so only its actor's
  limits apply. `broker_from_settings` raises `WorkerConfigurationError`
  without `KNOWLEDGE_SERVICE_REDIS_URL`.
- **Actor** (`actors.py`). `ping` runs on `maintenance` with a 5 s limit
  and no retries. `check_ping_token` accepts only a `str` of 1 to 64 ASCII
  letters, digits, `_`, or `-`, on send and again on receipt, and raises
  `InvalidPing` otherwise. `declare_actors` takes the broker and a recorder,
  so tests use the same actor on a `StubBroker`.
- **Entry point** (`main.py`). The `dramatiq` command imports it; it makes
  the settings' broker global and declares the actors.
- **Readiness** (`health.py`). `broker_ready` sends `PING` in a daemon
  thread and waits at most the timeout; any exception is "not ready".
  `python -m knowledge_service.worker.health` exits 0 (ready), 1 (no
  answer), or 2 (settings missing or invalid), and never prints the URL.
- **Settings.** `redis_url: SecretStr | None` and
  `redis_health_timeout_seconds` (default 2.0, from 0.05 to 30).
- **Local infrastructure.** `deploy/redis/compose.yaml` pins
  `redis:8.2.3-alpine` by digest on `127.0.0.1:6380`, with `noeviction`,
  no RDB or AOF persistence, and a `redis-cli ping` health check. Make
  targets: `redis-up`, `redis-down`, `worker` (runs `.venv/bin/dramatiq`
  after `uv sync`), `worker-ready`, `worker-ping`; `test-integration` passes
  `KNOWLEDGE_SERVICE_TEST_REDIS_URL` (database 15).
- **Replay.** Delivery is at least once. A duplicate `ping` only logs its
  token twice, so it is harmless; a real job must be idempotent before it
  uses this queue (M08-T02 and M08-T03).
- **Changed files.** `pyproject.toml`, `uv.lock`,
  `src/knowledge_service/settings.py`, `src/knowledge_service/worker/`
  (`__init__`, `broker`, `actors`, `health`, `main`), `tests/test_worker.py`,
  `tests/test_worker_integration.py`, `deploy/redis/compose.yaml`,
  `Makefile`, `docs/architecture.md`, `docs/threat-model.md`, the lesson
  guides, and this file.

## Verification

### Manual run (Step 9), 2026-11-21

A real worker (`make worker`, one process, two threads) consumed a ping
sent through the local Redis:

```text
$ make worker-ping
sent ping 6824b11f1f5b445d828d0566a99176a0

[2026-11-21 11:21:30,219] [dramatiq.WorkerProcess(0)] [INFO] Worker process is ready for action.
[2026-11-21 11:21:34,938] [knowledge_service.worker.actors] [INFO] ping received: 6824b11f1f5b445d828d0566a99176a0
```

With the Redis container stopped, readiness failed in the same second:

```text
$ make worker-ready
not ready: the Redis broker did not answer
make: *** [worker-ready] Error 1
```

The probe itself exits 1; `make` reports a failed recipe as its own exit
code 2. With Redis started again, `make worker-ready` printed `ready`.

While Redis was down, the worker process stayed up. Each of its four
consumers (`maintenance`, `synchronization`, and their delay queues) logged
`Consumer encountered a connection error` and `Restarting consumer in 3.00
seconds` every three seconds. After Redis returned, the same process
consumed a second ping with no restart:

```text
[2026-11-21 11:21:51,177] [knowledge_service.worker.actors] [INFO] ping received: 6c4e761a5c074548bcb0ddf671697620
```

A ping queued while no worker ran (`messages waiting: 1` in
`knowledge-service.v1:maintenance`) was gone after
`docker compose -p knowledge-service-redis restart` (`messages waiting: 0`,
no keys left). Both pings sent while the worker was stopped were lost this
way.

### Deliberate break (Step 10), 2026-11-21

With `ActorOwnsOptions()` removed from `queue_middleware`,
`uv run pytest tests/test_worker.py -q` gave `5 failed, 35 passed`:

- `test_the_broker_runs_only_the_chosen_middleware`
- `test_a_message_cannot_lift_its_actors_time_limit`
- `test_a_message_cannot_turn_retries_back_on`
- `test_a_message_option_cannot_stop_the_next_ping[options0]` (`throws`)
- `test_a_message_option_cannot_stop_the_next_ping[options1]` (`on_retry_exhausted`)

The run took 20.4 s instead of about 6 s: the slow ping ran its full five
seconds under a lifted time limit, and the failing ping was retried with
backoff. `[options2]` (`retry_when: "x"`) passed even without the guard,
so that case alone would not catch its removal. With the line restored,
all 40 tests passed again.

### Gates, 2026-11-21

| Command | Result |
|---|---|
| `uv run pytest tests/test_worker.py -q` | 40 passed |
| `KNOWLEDGE_SERVICE_TEST_REDIS_URL=redis://127.0.0.1:6380/15 uv run pytest tests/test_worker_integration.py -q -m integration` | 2 passed; Redis database 15 empty afterwards |
| `make format-check` | 310 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors, 0 warnings |
| `make test-unit` | 3026 passed, 705 deselected |
| `make test-integration` | 697 passed, 3034 deselected |
| `make docs-check` | passed |
| `make hooks` | all hooks passed (see note) |
| `git diff --check` | clean |

Note: one `make hooks` run failed
`tests/test_markdown_parser.py::test_many_links_parse_in_linear_time` on
its 5 s timeout while another project's type checks and tests held the
machine (load average above 50). The test passes alone in 1.2 s, runs
before `tests/test_worker.py` in the suite, and passed on the next hook
run at the same load. It is not affected by this change.

### Conclusions

- The acceptance criteria hold: a real worker consumes a harmless job, and
  readiness fails at once when its broker is unavailable.
- Liveness and readiness differ in practice. During the outage the worker
  process was alive and never crashed, yet it could do no work. Only the
  readiness probe reports that state, so an orchestrator needs the probe,
  not just "is the process running".
- Dramatiq reconnects by itself every three seconds, so a short Redis
  outage needs no worker restart.
- A Redis restart loses every queued message, as designed: the container
  keeps no data. A caller that saw `sent ping` cannot assume the job will
  run. This is safe only once the stored Job in PostgreSQL is the record
  and lost messages are queued again (M08-T02 persist-before-enqueue,
  M08-T08 recovery). Until then, nothing real may go through this queue.
- Found and fixed during this run: under `uv run`, one Ctrl+C (SIGINT to
  the whole terminal process group) reached Dramatiq twice, once from the
  terminal and once forwarded by `uv`. The second signal made Dramatiq log
  `Killing worker process...` instead of stopping gracefully, which would
  cut a real job short. Started from `.venv/bin/dramatiq`, the same signal
  gives only `Stopping worker process...`. `make worker` now does that,
  checked twice with a simulated Ctrl+C and once with a ping in between.
## Review

- **Adversarial review of the prototype** (independent agent) found, and
  the shipped code fixes, each with a test that fails on the first version:
  a message's own `max_retries`, `time_limit`, `throws`, or
  `on_retry_exhausted` overrode the actor (retries came back, a time limit
  was lifted, a worker thread died for good), fixed by `ActorOwnsOptions`;
  a readiness check that ran for over a minute against a trickling server,
  fixed by one overall deadline; untyped crashes on odd replies, URL
  options, and long host names, fixed by treating any exception as not
  ready; a timeout of `1e-300` accepted, fixed by a 0.05 s floor; invalid
  settings crashing the probe, now exit 2; and a JSON test that passed
  without the line it tested.
- **Found while implementing:** the integration fixture left one
  `__heartbeats__` key per run in database 15 (now deleted in teardown);
  under `uv run`, one Ctrl+C reached Dramatiq twice and killed the worker
  (now run from `.venv`).
- **Completion review, Standards.** One blocking finding, fixed: the
  `worker: setup ##` line broke entry 14 of the `make` menu, whose pattern
  expects `target: ## description`; the sync moved into the recipe. Also
  fixed: the `broker_ready` docstring claimed its thread always ends.
  Non-blocking, left as is: the Make targets export
  `KNOWLEDGE_SERVICE_REDIS_URL`, so a Redis URL set only in `.env` is
  ignored by them; `.env.example` does not list the Redis settings;
  `make test-integration` fails rather than skips when Redis is down; each
  `StubBroker` fixture leaves one sleeping time-limit thread; dead letters
  keep Dramatiq's default seven-day TTL, which `build_broker` does not set
  explicitly.
- **Completion review, Spec.** Code and observable behavior meet every
  criterion; the gaps were these records (gates, Design, Review, Decision,
  Reflection), now written.

## Decision

Accept. A real worker consumed a ping through Redis, readiness failed at
once with the broker down and recovered with it, and every limit is set in
`broker.py` and cannot be overridden by a message. The design is
deliberately unsafe for real work until M08-T02 stores each Job before it
is queued and M08-T08 queues lost work again: a Redis restart loses every
waiting message.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it
in their own words.

1. A caller that saw `sent ping` can still lose the job: Redis restarts
   before a worker takes the message (observed in Step 9), or the message
   waits longer than one day and `AgeLimit` drops it. M08-T02 (store the
   Job first) and M08-T08 (queue lost work again) make this safe.
2. PostgreSQL, through the stored Job. It survives restarts and is
   changed in transactions, so it can say what was asked for and how it
   ended; Redis keeps nothing on disk here by design (ADR 0011).
3. Retries belong to one owner, the stored Job (M08-T04), which can count
   attempts durably and stop. With both retrying, one failure would
   multiply attempts (Dramatiq's default 20 for each Job attempt), and the
   Job's attempt count would no longer describe what ran.
4. `ActorOwnsOptions` deletes `max_retries` before `Retries` reads it, so
   the ping runs once and goes to the dead-letter queue.
   `test_a_message_cannot_turn_retries_back_on` and
   `test_the_broker_runs_only_the_chosen_middleware` fail if it is removed.
5. The design is falsified if Redis must be the record after all: for
   example, if work is lost in a way PostgreSQL cannot detect and queue
   again, or if Redis restarts are frequent enough that recovery delay
   matters more than simplicity; either would call for persistence (AOF)
   or a durable broker. It is also falsified if readiness reports "ready"
   while the worker cannot take messages, for example with Redis up but
   refusing writes under `noeviction`.
