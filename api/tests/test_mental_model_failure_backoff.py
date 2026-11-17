"""Automatic refresh backoff for a mental model that keeps failing.

A model that times out on every attempt was retried every worker cycle, filling the
dashboard with failures and burning the LLM budget. `_failure_backoff_until` counts
recent failed refreshes and returns when the next automatic attempt may run.

Deterministic: the database is replaced by a fake connection that returns the rows
the real query would return, so no LLM or PostgreSQL is needed.
"""

from datetime import UTC, datetime, timedelta

import pytest

from evolving_profile_api.engine.memory_engine import MemoryEngine


class _FakeConn:
    def __init__(self, created_ats):
        self._rows = [{"created_at": ts} for ts in created_ats]

    async def fetch(self, *_args, **_kwargs):
        return self._rows


def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


@pytest.mark.asyncio
async def test_fewer_than_three_failures_do_not_back_off():
    engine = MemoryEngine.__new__(MemoryEngine)
    assert await engine._failure_backoff_until(_FakeConn([_ago(0.1), _ago(0.5)]), "b", "m") is None
    assert await engine._failure_backoff_until(_FakeConn([]), "b", "m") is None


@pytest.mark.asyncio
async def test_three_failures_back_off_six_hours_from_latest():
    engine = MemoryEngine.__new__(MemoryEngine)
    latest = _ago(1)
    until = await engine._failure_backoff_until(_FakeConn([latest, _ago(2), _ago(3)]), "b", "m")
    assert until == latest + timedelta(hours=6)


@pytest.mark.asyncio
async def test_six_failures_back_off_a_day_from_latest():
    engine = MemoryEngine.__new__(MemoryEngine)
    latest = _ago(1)
    created = [latest] + [_ago(2 + i) for i in range(5)]
    until = await engine._failure_backoff_until(_FakeConn(created), "b", "m")
    assert until == latest + timedelta(hours=24)


@pytest.mark.asyncio
async def test_naive_timestamps_are_treated_as_utc():
    engine = MemoryEngine.__new__(MemoryEngine)
    latest_naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    until = await engine._failure_backoff_until(
        _FakeConn([latest_naive, _ago(2), _ago(3)]), "b", "m"
    )
    assert until.tzinfo is not None
    assert until == latest_naive.replace(tzinfo=UTC) + timedelta(hours=6)
