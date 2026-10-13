"""One clock for the whole project, in the venues' own time zone.

Every time on every agenda is Dutch local time, but the Pi runs on
Europe/London, an hour behind Amsterdam. Asking `datetime.now()` there meant a
24-hour discount window opened an hour late by the code's reckoning, and a
show that started at 20:15 stayed "upcoming" until 21:15. Anything that is
compared with a show time asks this module instead.

The stamps the database writes itself (`observed_at`, `last_seen`,
`poll_runs.started_at`) stay UTC; `local()` turns them into wall-clock time.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")


def now() -> datetime:
    """Naive Amsterdam wall-clock time, directly comparable with `starts_at`."""
    return datetime.now(TZ).replace(tzinfo=None)


def local(stamp: str | datetime | None) -> datetime | None:
    """A stored stamp as naive Amsterdam time.

    Aware stamps (everything the poller writes) are converted. Naive ones were
    written as local time already and pass through unchanged.
    """
    if stamp is None:
        return None
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp)
        except ValueError:
            return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(TZ).replace(tzinfo=None)
    return stamp
