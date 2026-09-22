"""Tests that prove the pytest loop configuration is active."""

import asyncio

import pytest


async def test_async_test_runs() -> None:
    await asyncio.sleep(0)

    assert asyncio.get_running_loop().is_running()


@pytest.mark.integration
def test_integration_marker_is_registered() -> None:
    assert True


@pytest.mark.live
async def test_live_marker_is_not_enabled_by_default() -> None:
    raise AssertionError("live tests must not run without an explicit opt-in")
