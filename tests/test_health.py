"""Detecting a scraper that broke without saying so."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from podiumwatch import health


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
    when = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
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
