"""Tests for the synchronization schedule's bounds and its setting."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from knowledge_service.ingestion.scheduling import (
    MAX_INTERVAL,
    MIN_INTERVAL,
    SynchronizationSchedule,
)
from knowledge_service.settings import Settings


def test_every_source_is_synchronized_hourly_by_default() -> None:
    assert SynchronizationSchedule().interval == timedelta(hours=1)
    assert Settings.model_fields["synchronization_interval_minutes"].default == 60


@pytest.mark.parametrize("interval", [MIN_INTERVAL, timedelta(hours=6), MAX_INTERVAL])
def test_an_interval_within_bounds_is_accepted(interval: timedelta) -> None:
    assert SynchronizationSchedule(interval=interval).interval == interval


@pytest.mark.parametrize(
    "interval",
    [
        MIN_INTERVAL - timedelta(seconds=1),
        MAX_INTERVAL + timedelta(seconds=1),
        timedelta(0),
        60,
        None,
    ],
)
def test_an_interval_out_of_bounds_is_refused(interval: object) -> None:
    with pytest.raises(ValueError, match="interval"):
        SynchronizationSchedule(interval=interval)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("minutes", [0, -5, 7 * 24 * 60 + 1])
def test_the_interval_setting_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError):
        Settings(synchronization_interval_minutes=minutes)
