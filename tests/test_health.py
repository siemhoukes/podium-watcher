"""Detecting a scraper that broke without saying so."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from podiumwatch import clock, health
from podiumwatch.db import SCHEMA


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE poll_runs (id INTEGER PRIMARY KEY, venue_id TEXT,"
        " started_at TEXT, ok INTEGER, shows_found INTEGER, error TEXT)"
    )
    return c


def add(conn, venue, found, ok=1, minutes_ago=5, error=None):
    when = (clock.now() - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, error)"
        " VALUES (?,?,?,?,?)",
        (venue, when, ok, found, error),
    )


def only(conn, venue):
    return next(v for v in health.venue_health(conn) if v["venue_id"] == venue)


def test_a_healthy_venue(conn):
    for i in range(5):
        add(conn, "chasse", 750, minutes_ago=100 * (i + 1))
    add(conn, "chasse", 758)
    assert only(conn, "chasse")["status"] == health.OK


def test_a_run_that_failed(conn):
    add(conn, "chasse", 0, ok=0, error="HTTP 500")
    v = only(conn, "chasse")
    assert v["status"] == health.FAIL
    assert "HTTP 500" in v["message"]


def test_finding_nothing_is_a_failure_not_a_quiet_venue(conn):
    add(conn, "chasse", 0)
    assert only(conn, "chasse")["status"] == health.FAIL


def test_a_collapse_in_row_count_is_caught(conn):
    """The nastiest failure: markup changed but still parses.

    A venue returning a third of its usual rows looks like data, not an error.
    """
    for i in range(6):
        add(conn, "chasse", 750, minutes_ago=100 * (i + 1))
    add(conn, "chasse", 120)
    v = only(conn, "chasse")
    assert v["status"] == health.WARN
    assert "120" in v["message"]


def test_small_venues_are_exempt_from_the_collapse_rule(conn):
    """Fifteen rows is healthy for Paradox and alarming for Chasse."""
    for i in range(6):
        add(conn, "paradox", 15, minutes_ago=100 * (i + 1))
    add(conn, "paradox", 7)
    assert only(conn, "paradox")["status"] == health.OK


def test_a_stale_venue(conn):
    add(conn, "chasse", 750, minutes_ago=60 * 30)
    v = only(conn, "chasse")
    assert v["status"] == health.WARN
    assert "uur" in v["message"]


def test_a_venue_that_never_ran(conn):
    report = health.venue_health(conn, ["tilburg"])
    assert report[0]["status"] == health.FAIL


def test_worst_first(conn):
    add(conn, "good_one", 500)
    add(conn, "bad_one", 0)
    statuses = [v["status"] for v in health.venue_health(conn)]
    assert statuses[0] == health.FAIL


def test_a_collapse_that_has_lasted_ten_days_is_still_caught(conn):
    """The September 2026 failure, replayed.

    Seventeen days at ~760 rows, then ten days at ~100 after Peppered renamed
    its page parameter. The old rule compared against the last ten runs --
    by then all collapsed -- and said "ok" for three weeks.
    """
    for day in range(11, 28):
        for run in range(4):
            add(conn, "chasse", 760, minutes_ago=day * 1440 + run * 360)
    for i in range(40):
        add(conn, "chasse", 100, minutes_ago=5 + i * 360)
    v = only(conn, "chasse")
    assert v["status"] == health.WARN
    assert "760" in v["message"]


def test_an_incomplete_run_is_a_warning_with_its_reason(conn):
    for i in range(4):
        add(conn, "chasse", 750, minutes_ago=400 * (i + 1))
    add(conn, "chasse", 300,
        error="Pagina 4 niet opgehaald (HTTP 403); 300 voorstellingen gelezen")
    v = only(conn, "chasse")
    assert v["status"] == health.WARN
    assert "Onvolledig" in v["message"] and "Pagina 4" in v["message"]


# --------------------------------------------------------------------------
# Shows that drop out of the listing
# --------------------------------------------------------------------------

@pytest.fixture
def full():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def utc(minutes_ago: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return stamp.isoformat(timespec="seconds")


def complete_run(c, venue, minutes_ago, found, complete=1):
    c.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, error,"
        " complete) VALUES (?,?,1,?,NULL,?)",
        (venue, utc(minutes_ago), found, complete),
    )


def listed(c, venue, n, last_seen, prefix):
    starts = (clock.now() + timedelta(days=5)).isoformat()
    for i in range(n):
        c.execute(
            "INSERT INTO shows (venue_id, external_id, title, starts_at,"
            " first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            (venue, f"{prefix}{i}", f"{prefix}{i}", starts, last_seen, last_seen),
        )


def test_shows_dropping_out_of_the_listing_are_caught(full):
    """Row counts can look plausible while most of the programme is gone."""
    complete_run(full, "chasse", 10, 30)
    listed(full, "chasse", 30, utc(5), "seen")
    listed(full, "chasse", 70, utc(60 * 24 * 21), "gone")
    v = only(full, "chasse")
    assert v["status"] == health.WARN
    assert "70 komende voorstellingen" in v["message"]


def test_a_few_cancellations_are_not_an_alarm(full):
    complete_run(full, "chasse", 10, 30)
    listed(full, "chasse", 30, utc(5), "seen")
    listed(full, "chasse", 3, utc(60 * 24 * 3), "gone")
    assert only(full, "chasse")["status"] == health.OK


def test_a_run_from_before_the_fix_never_decides_what_vanished(full):
    """Old runs logged success while reading one page in twenty.

    Trusting this afternoon's Chasse run hid 575 shows that were simply on
    pages the bot check now refuses. Without a complete run, nothing is
    hidden; the dashboard shows those with the date they were last read.
    """
    complete_run(full, "chasse", 10, 88, complete=None)
    listed(full, "chasse", 30, utc(5), "seen")
    listed(full, "chasse", 70, utc(60 * 24 * 21), "unseen")
    assert health.vanished_counts(full, clock.now()) == {}
    assert health.seen_cutoffs(full) == {}
