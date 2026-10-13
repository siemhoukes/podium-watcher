"""Show times are Dutch; the Pi's clock is British."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from podiumwatch import clock


def test_now_is_amsterdam_wall_clock_time():
    expected = (
        datetime.now(timezone.utc)
        .astimezone(ZoneInfo("Europe/Amsterdam"))
        .replace(tzinfo=None)
    )
    assert abs((clock.now() - expected).total_seconds()) < 5


def test_a_utc_stamp_becomes_dutch_time_in_summer_and_winter():
    # Summer time: two hours ahead of UTC. On the Pi, `astimezone()` gave one.
    assert clock.local("2026-10-13T17:21:32+00:00") == datetime(2026, 10, 13, 19, 21, 32)
    assert clock.local("2026-12-01T19:00:00+00:00") == datetime(2026, 12, 1, 20, 0)


def test_naive_stamps_pass_through_and_junk_is_none():
    assert clock.local("2026-10-13T20:15:00") == datetime(2026, 10, 13, 20, 15)
    assert clock.local(datetime(2026, 1, 2, 3, 4)) == datetime(2026, 1, 2, 3, 4)
    assert clock.local(None) is None
    assert clock.local("not a date") is None
