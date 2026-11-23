"""Tests for lease names, times to live, and how lease failures are classified."""

from datetime import timedelta

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
)
from knowledge_service.ingestion.sources import SynchronizationSuperseded
from knowledge_service.job_failures import JobFailure, classify_job_failure
from knowledge_service.leases import (
    MAX_TTL,
    MIN_TTL,
    InvalidLease,
    check_name,
    check_ttl,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")


@pytest.mark.parametrize("name", ["source-sync:abc", "a", "x" * 200, "a:b_c-1"])
def test_a_short_safe_name_is_accepted(name: str) -> None:
    assert check_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "Upper", "a b", "x" * 201, ":leading", "new\nline", None, 7]
)
def test_any_other_name_is_refused(name: object) -> None:
    with pytest.raises(InvalidLease, match="lease name"):
        check_name(name)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("ttl", [MIN_TTL, timedelta(minutes=2), MAX_TTL])
def test_a_ttl_within_bounds_is_accepted(ttl: timedelta) -> None:
    assert check_ttl(ttl) == ttl


@pytest.mark.parametrize(
    "ttl",
    [
        MIN_TTL - timedelta(milliseconds=1),
        MAX_TTL + timedelta(seconds=1),
        timedelta(0),
        -timedelta(seconds=1),
        5,
        None,
    ],
)
def test_a_ttl_out_of_bounds_is_refused(ttl: object) -> None:
    with pytest.raises(InvalidLease, match="lease lives"):
        check_ttl(ttl)  # pyright: ignore[reportArgumentType]


def test_every_pass_of_one_source_shares_one_valid_lease_name() -> None:
    name = lease_name(SOURCE_ID)

    assert name == f"source-sync:{SOURCE_ID.value}"
    assert check_name(name) == name


@pytest.mark.parametrize(
    "error",
    [
        SourceBusy("busy"),
        SourceLeaseLost("lost"),
        SourceCoordinationUnavailable("down"),
    ],
)
def test_a_lease_failure_is_retried_later_like_an_unavailable_source(
    error: Exception,
) -> None:
    failure = classify_job_failure(error)

    assert failure is JobFailure.SOURCE_UNAVAILABLE
    assert failure.transient


def test_a_superseded_pass_is_never_retried() -> None:
    # A newer pass already covers the work; a retry could only be refused.
    failure = classify_job_failure(SynchronizationSuperseded("newer"))

    assert failure is JobFailure.SUPERSEDED
    assert not failure.transient
