"""Is each scraper still working?

A broken scraper does not raise. A venue redesigns, the selector stops
matching, `fetch` returns an empty list, and the poller records a clean run
finding nothing. On a dashboard that already hides sold-out and filtered shows,
"this venue has nothing on" looks entirely plausible -- so the failure is
invisible for as long as you do not happen to notice a familiar name missing.

`poll_runs` has recorded every attempt from the start. This reads it back and
turns it into a verdict, which is the part that was missing.

The interesting rules are the last two. A venue that suddenly returns a third
of its usual rows has almost certainly changed its markup in a way that still
parses -- the nastiest failure, because it looks like data.

That rule used to compare against the last ten runs, and it failed exactly
when it mattered. In late September 2026 Peppered renamed its page parameter
and four venues dropped to a tenth of their programme. Ten runs is two and a
half days: by the third day the collapse *was* the baseline, and /status said
"ok" for three weeks. So "normal" now reaches back four weeks, and a second,
independent check counts upcoming shows that a complete run no longer lists --
performances do not leave an agenda in their hundreds unless the scraper lost
them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import clock

OK = "ok"
WARN = "warn"
FAIL = "fail"

# The poller runs four times a day; nothing for this long means the timer or
# the Pi is down, not that a venue is quiet.
STALE_AFTER = timedelta(hours=14)

# Below this share of its usual size, a venue is assumed broken rather than
# quiet.
COLLAPSE_RATIO = 0.5

# Venues legitimately this small are exempt from the collapse rule; Paradox
# publishes about fifteen shows at a time.
SMALL_VENUE_ROWS = 25

# "Usual size" is the 75th percentile of four weeks of runs. A collapse has to
# last three weeks before it starts to look normal, while a programme that
# genuinely thins out as the season runs on moves the baseline with it.
BASELINE_DAYS = 28
BASELINE_PERCENTILE = 0.75
BASELINE_MIN_RUNS = 3

# Upcoming shows (within this horizon) that the latest complete run no longer
# listed. A handful is cancellations; this many is a scraper missing pages.
VANISHED_HORIZON = timedelta(days=60)
VANISHED_MIN = 10
VANISHED_SHARE = 0.2

# Runs logged before October 2026 were stamped when they *finished*, seconds
# after their last upsert. This margin lets those serve as a reference too.
SEEN_MARGIN = timedelta(minutes=10)


def venue_health(
    conn, venue_ids: list[str] | None = None, now: datetime | None = None
) -> list[dict]:
    """One verdict per venue, worst first."""
    now = now or clock.now()
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

    vanished = vanished_counts(conn, now)
    out = [
        _verdict(venue_id, runs, now, vanished.get(venue_id))
        for venue_id, runs in by_venue.items()
    ]
    out.sort(key=lambda v: ({FAIL: 0, WARN: 1, OK: 2}[v["status"]], v["venue_id"]))
    return out


def seen_cutoffs(conn) -> dict[str, str]:
    """{venue: stamp}: a show last seen before its venue's stamp was not
    listed by that venue's latest complete run.

    A run is complete when it succeeded without noting a problem; a run cut
    short halfway says nothing about which shows are really gone. Stamps are
    in the stored format (UTC ISO), so they compare directly with `last_seen`.
    """
    try:
        rows = conn.execute(
            "SELECT venue_id, MAX(started_at) t FROM poll_runs"
            " WHERE ok = 1 AND error IS NULL GROUP BY venue_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for r in rows:
        try:
            stamp = datetime.fromisoformat(r["t"]) - SEEN_MARGIN
        except (TypeError, ValueError):
            continue
        out[r["venue_id"]] = stamp.isoformat(timespec="seconds")
    return out


def vanished_counts(conn, now: datetime) -> dict[str, tuple[int, int]]:
    """-> {venue: (vanished, still listed)} for shows in the next two months.

    A show has vanished when the venue's latest complete run did not list it:
    its `last_seen` is older than that run's start.
    """
    complete = seen_cutoffs(conn)
    if not complete:
        return {}
    try:
        rows = conn.execute(
            "SELECT venue_id, last_seen FROM shows"
            " WHERE starts_at >= ? AND starts_at < ?",
            (now.isoformat(), (now + VANISHED_HORIZON).isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}  # a bare poll_runs table, as in the tests

    out: dict[str, tuple[int, int]] = {}
    for r in rows:
        ref = complete.get(r["venue_id"])
        if not ref:
            continue
        gone, listed = out.get(r["venue_id"], (0, 0))
        if r["last_seen"] < ref:
            gone += 1
        else:
            listed += 1
        out[r["venue_id"]] = (gone, listed)
    return out


def _baseline(previous_runs: list, now: datetime) -> float | None:
    """This venue's usual row count, or None while there is too little history."""
    since = now - timedelta(days=BASELINE_DAYS)
    counts = sorted(
        r["shows_found"]
        for r in previous_runs
        if r["ok"] and r["shows_found"]
        and (clock.local(r["started_at"]) or since) >= since
    )
    if len(counts) < BASELINE_MIN_RUNS:
        return None
    return counts[round(BASELINE_PERCENTILE * (len(counts) - 1))]


def _verdict(venue_id: str, runs: list, now: datetime, vanished=None) -> dict:
    if not runs:
        return _row(venue_id, FAIL, "Nog nooit gedraaid.", None, None)

    latest = runs[0]
    when = clock.local(latest["started_at"])
    found = latest["shows_found"] or 0

    if not latest["ok"]:
        return _row(
            venue_id, FAIL,
            f"Laatste run mislukt: {(latest['error'] or 'onbekende fout')[:120]}",
            when, found,
        )

    if found == 0:
        return _row(venue_id, FAIL, "Laatste run vond niets.", when, found)

    if when and now - when > STALE_AFTER:
        hours = int((now - when).total_seconds() // 3600)
        return _row(venue_id, WARN, f"Al {hours} uur geen run.", when, found)

    if latest["error"]:
        return _row(
            venue_id, WARN, f"Onvolledig: {latest['error'][:120]}", when, found
        )

    # Compare against this venue's own history, not a fixed threshold:
    # 15 rows is healthy for Paradox and alarming for Chasse.
    usual = _baseline(runs[1:], now)
    if usual and usual >= SMALL_VENUE_ROWS and found < usual * COLLAPSE_RATIO:
        return _row(
            venue_id, WARN,
            f"Nog maar {found} rijen, normaal ~{int(usual)}. "
            "Waarschijnlijk een aangepaste opmaak of paginering.",
            when, found,
        )

    if vanished:
        gone, listed = vanished
        if gone >= VANISHED_MIN and gone > VANISHED_SHARE * (gone + listed):
            return _row(
                venue_id, WARN,
                f"{gone} komende voorstellingen staan niet meer in de opgehaalde "
                "agenda. Mist de scraper pagina's?",
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
