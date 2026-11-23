"""Tests for the progress of one Job attempt: nothing in it goes back."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.job_progress import (
    MAX_COUNT,
    AttemptFinished,
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    attempt_number,
    outcome_of,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(seconds=1)
LATER = STARTED_AT + timedelta(seconds=30)


def running_job(*, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
        max_attempts=max_attempts,
    )
    return job.start(started_at=STARTED_AT)


def begun() -> JobProgress:
    return JobProgress.begin(running_job())


def test_an_attempt_begins_at_its_start_with_nothing_counted() -> None:
    progress = begun()

    assert progress.attempt == 1
    assert progress.stage is JobStage.STARTING
    assert progress.counters == JobCounters()
    assert progress.warnings == frozenset()
    assert progress.heartbeat_at == STARTED_AT
    assert progress.outcome is None


def test_only_a_running_job_begins_an_attempt() -> None:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )

    with pytest.raises(InvalidJob):
        JobProgress.begin(queued)


def test_a_report_moves_the_stage_counters_warnings_and_heartbeat() -> None:
    advanced = begun().advance(
        stage=JobStage.WRITING,
        counters=JobCounters(processed=10, changed=3, skipped=1),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        at=LATER,
    )

    assert advanced.stage is JobStage.WRITING
    assert advanced.counters == JobCounters(processed=10, changed=3, skipped=1)
    assert advanced.warnings == {JobWarning.QUARANTINED_ITEMS}
    assert advanced.heartbeat_at == LATER


def test_the_same_report_twice_changes_nothing() -> None:
    counters = JobCounters(processed=4)
    once = begun().advance(stage=JobStage.READING, counters=counters, at=LATER)

    twice = once.advance(stage=JobStage.READING, counters=counters, at=LATER)

    assert twice == once


def test_the_stage_may_go_back_page_after_page() -> None:
    writing = begun().advance(
        stage=JobStage.WRITING, counters=JobCounters(processed=1), at=LATER
    )

    reading = writing.advance(
        stage=JobStage.READING, counters=JobCounters(processed=1), at=LATER
    )

    assert reading.stage is JobStage.READING


@pytest.mark.parametrize("name", ["processed", "changed", "skipped"])
def test_a_counter_never_goes_down(name: str) -> None:
    ahead = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(processed=5, changed=5, skipped=5),
        at=LATER,
    )
    lower = {"processed": 5, "changed": 5, "skipped": 5} | {name: 4}

    with pytest.raises(ProgressWentBackwards):
        ahead.advance(stage=JobStage.READING, counters=JobCounters(**lower), at=LATER)


def test_a_warning_never_disappears() -> None:
    warned = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(),
        warnings=[JobWarning.UNRESOLVED_READERS],
        at=LATER,
    )

    with pytest.raises(ProgressWentBackwards):
        warned.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_the_heartbeat_never_goes_back_in_time() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    with pytest.raises(ProgressWentBackwards):
        progress.advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER - timedelta(microseconds=1),
        )


def test_an_ended_attempt_takes_no_more_reports() -> None:
    ended = begun().finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_ending_twice_the_same_way_changes_nothing() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    assert ended.finish(outcome=AttemptOutcome.FAILED, at=LATER) is ended


def test_an_ended_attempt_cannot_end_another_way() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)


@pytest.mark.parametrize("value", [-1, MAX_COUNT + 1, True, 1.0, "1"])
def test_a_counter_is_a_bounded_whole_number(value: object) -> None:
    with pytest.raises(InvalidJob):
        JobCounters(processed=value)  # type: ignore[arg-type]


def test_a_report_time_must_be_utc() -> None:
    kyiv = timezone(timedelta(hours=2))

    with pytest.raises(InvalidJob):
        begun().advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER.astimezone(kyiv),
        )


def test_a_warning_must_be_a_known_one() -> None:
    with pytest.raises(InvalidJob):
        JobProgress(
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            warnings=frozenset({"token=secret"}),  # type: ignore[arg-type]
            started_at=STARTED_AT,
            heartbeat_at=STARTED_AT,
        )


def test_the_attempt_number_follows_the_failed_attempts() -> None:
    first = running_job()
    failed = first.fail(
        error_code=JobFailure.SOURCE_UNAVAILABLE.value,
        finished_at=LATER,
        retry_at=LATER + timedelta(minutes=1),
    )
    second = failed.retry(queued_at=LATER + timedelta(minutes=1)).start(
        started_at=LATER + timedelta(minutes=2)
    )

    assert attempt_number(first) == 1
    assert attempt_number(failed) == 1
    assert attempt_number(second) == 2
    assert attempt_number(second.succeed(finished_at=second.started_at)) == 2  # type: ignore[arg-type]


def test_a_queued_job_has_no_attempt_under_way() -> None:
    with pytest.raises(InvalidJob):
        attempt_number(
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=uuid4(),
                queued_at=QUEUED_AT,
            )
        )


def test_the_outcome_of_a_finished_job_is_typed() -> None:
    job = running_job()
    retry_at = LATER + timedelta(minutes=1)

    def failed(code: JobFailure, retry: datetime | None = None) -> Job:
        return job.fail(error_code=code.value, finished_at=LATER, retry_at=retry)

    assert outcome_of(job.succeed(finished_at=LATER)) is AttemptOutcome.SUCCEEDED
    assert (
        outcome_of(job.succeed(finished_at=LATER), [JobWarning.UNRESOLVED_READERS])
        is AttemptOutcome.SUCCEEDED_WITH_WARNINGS
    )
    assert (
        outcome_of(failed(JobFailure.SOURCE_UNAVAILABLE, retry_at))
        is AttemptOutcome.RETRY_SCHEDULED
    )
    assert outcome_of(failed(JobFailure.SUPERSEDED)) is AttemptOutcome.SUPERSEDED
    assert outcome_of(failed(JobFailure.PROGRAMMING_ERROR)) is AttemptOutcome.FAILED


def test_a_running_job_has_no_outcome_yet() -> None:
    with pytest.raises(InvalidJob):
        outcome_of(running_job())


# --- Found by the adversarial review ---------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "reading"},
        {"counters": {"processed": 1}},
        {"warnings": ["unresolved_readers"]},
        {"outcome": "succeeded", "finished_at": LATER},
        {"attempt": 2**31},
    ],
)
def test_every_field_of_an_attempt_has_its_own_type(change: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "job_id": JobId.new(),
        "attempt": 1,
        "stage": JobStage.READING,
        "counters": JobCounters(),
        "warnings": frozenset(),
        "started_at": STARTED_AT,
        "heartbeat_at": LATER,
    }

    with pytest.raises(InvalidJob):
        JobProgress(**(fields | change))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "report",
    [
        {"stage": "reading", "counters": JobCounters()},
        {"stage": JobStage.READING, "counters": {"processed": 1}},
        {"stage": JobStage.READING, "counters": JobCounters(), "warnings": None},
    ],
)
def test_a_report_of_the_wrong_type_is_a_typed_error(report: dict[str, object]) -> None:
    with pytest.raises(InvalidJob):
        begun().advance(at=LATER, **report)  # type: ignore[arg-type]


def test_an_attempt_ending_before_its_heartbeat_pulls_the_heartbeat_back() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    ended = progress.finish(outcome=AttemptOutcome.SUCCEEDED, at=STARTED_AT)

    assert ended.finished_at == ended.heartbeat_at == STARTED_AT
