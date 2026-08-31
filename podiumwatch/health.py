"""Is each scraper still working?

A broken scraper does not raise. A venue redesigns, the selector stops
matching, `fetch` returns an empty list, and the poller records a clean run
finding nothing. On a dashboard that already hides sold-out and filtered shows,
"this venue has nothing on" looks entirely plausible -- so the failure is
invisible for as long as you do not happen to notice a familiar name missing.

`poll_runs` has recorded every attempt from the start. This reads it back and
turns it into a verdict, which is the part that was missing.

The interesting rule is the last one. A venue that suddenly returns a third of
its usual rows has almost certainly changed its markup in a way that still
parses -- the nastiest failure, because it looks like data.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

OK = "ok"
WARN = "warn"
FAIL = "fail"

# The poller runs four times a day; nothing for this long means the timer or
# the Pi is down, not that a venue is quiet.
STALE_AFTER = timedelta(hours=14)

# Below this share of its own recent median, a venue is assumed broken rather
# than quiet.
COLLAPSE_RATIO = 0.5

# Venues legitimately this small are exempt from the collapse rule; Paradox
# publishes about fifteen shows at a time.
SMALL_VENUE_ROWS = 25


def venue_health(conn, venue_ids: list[str] | None = None) -> list[dict]:
    """One verdict per venue, worst first."""
    rows = conn.execute(
        """
        SELECT venue_id, started_at, ok, shows_found, error
        FROM poll_runs ORDER BY started_at DESC
        """
    ).fetchall()

    by_venue: dict[str, list] = {}
    for r in rows:
        by_venue.setdefault(r["venue_id"], []).append(r)

    for vid in venue_ids or []:
        by_venue.setdefault(vid, [])

    now = datetime.now()
    out = []
    for venue_id, runs in by_venue.items():
        out.append(_verdict(venue_id, runs, now))
    out.sort(key=lambda v: ({FAIL: 0, WARN: 1, OK: 2}[v["status"]], v["venue_id"]))
    return out


def _verdict(venue_id: str, runs: list, now: datetime) -> dict:
    if not runs:
        return _row(venue_id, FAIL, "Nog nooit gedraaid.", None, None)

    latest = runs[0]
    when = _parse(latest["started_at"])
    found = latest["shows_found"] or 0

    if not latest["ok"]:
        return _row(
            venue_id, FAIL,
            f"Laatste run mislukt: {(latest['error'] or 'onbekende fout')[:90]}",
            when, found,
        )

    if found == 0:
        return _row(venue_id, FAIL, "Laatste run vond niets.", when, found)

    if when and now - when > STALE_AFTER:
        hours = int((now - when).total_seconds() // 3600)
        return _row(venue_id, WARN, f"Al {hours} uur geen run.", when, found)

    # Compare against this venue's own recent history, not a fixed threshold:
    # 15 rows is healthy for Paradox and alarming for Chassé.
    history = [r["shows_found"] for r in runs[1:11] if r["ok"] and r["shows_found"]]
    if len(history) >= 3:
        median = statistics.median(history)
        if median >= SMALL_VENUE_ROWS and found < median * COLLAPSE_RATIO:
            return _row(
                venue_id, WARN,
                f"Nog maar {found} rijen, normaal ~{int(median)}. "
                "Waarschijnlijk aangepaste opmaak.",
                when, found,
            )

    return _row(venue_id, OK, f"{found} rijen.", when, found)


def _row(venue_id, status, message, when, found) -> dict:
    return {
        "venue_id": venue_id,
        "status": status,
        "message": message,
        "last_run": when,
        "shows_found": found,
    }


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # poll_runs stores UTC; the dashboard thinks in local time.
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    return stamp
