"""Window-open alerting: who gets told, and only once."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from podiumwatch import notify
from podiumwatch.db import SCHEMA

NOW = datetime(2026, 9, 20, 12, 0)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def add_show(conn, venue="tilburg", ext="1", starts=None, state="available",
             price=None, starred=True, seats=None):
    starts = starts or (NOW + timedelta(hours=12))
    cur = conn.execute(
        "INSERT INTO shows (venue_id, external_id, title, starts_at, url,"
        " first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
        (venue, ext, "Testvoorstelling", starts.isoformat(),
         "https://example.test/x", NOW.isoformat(), NOW.isoformat()),
    )
    show_id = cur.lastrowid
    free, total = seats or (None, None)
    conn.execute(
        "INSERT INTO snapshots (show_id, observed_at, state, seats_free,"
        " seats_total, price_min_cents) VALUES (?,?,?,?,?,?)",
        (show_id, NOW.isoformat(), state, free, total, price),
    )
    if starred:
        conn.execute(
            "INSERT INTO starred (venue_id, external_id, created_at) VALUES (?,?,?)",
            (venue, ext, NOW.isoformat()),
        )
    conn.commit()
    return show_id


def test_an_open_window_on_a_starred_show_is_due(conn):
    # Tilburg opens 48h before; 12h out is well inside.
    add_show(conn)
    assert len(notify.due_window_alerts(conn, now=NOW)) == 1


def test_a_window_that_has_not_opened_yet_is_not_due(conn):
    add_show(conn, starts=NOW + timedelta(days=10))
    assert notify.due_window_alerts(conn, now=NOW) == []


def test_unstarred_shows_are_never_alerted(conn):
    """A push per Tilburg show entering its 48 hours would be several a day."""
    add_show(conn, starred=False)
    assert notify.due_window_alerts(conn, now=NOW) == []


def test_a_sold_out_show_is_not_announced(conn):
    """The window opened, but there is nothing to buy."""
    add_show(conn, state="sold_out")
    assert notify.due_window_alerts(conn, now=NOW) == []


def test_an_already_sent_alert_is_not_repeated(conn):
    add_show(conn)
    conn.execute(
        "INSERT INTO alerts_sent (venue_id, external_id, kind, sent_at)"
        " VALUES (?,?,?,?)",
        ("tilburg", "1", notify.WINDOW_OPEN, NOW.isoformat()),
    )
    conn.commit()
    assert notify.due_window_alerts(conn, now=NOW) == []


def test_a_past_show_is_not_announced(conn):
    add_show(conn, starts=NOW - timedelta(hours=1))
    assert notify.due_window_alerts(conn, now=NOW) == []


def test_the_message_leads_with_the_price(conn):
    add_show(conn, price=2450, seats=(142, 886))
    item = notify.due_window_alerts(conn, now=NOW)[0]
    title, body = notify.format_window_alert(item)
    assert title.startswith("€13,50"), "the Tilburg student fare, not the door price"
    assert "142 van 886" in body
    assert "84% verkocht" in body


def test_nothing_is_sent_without_a_configured_topic(conn, monkeypatch):
    add_show(conn)
    monkeypatch.setattr(notify, "NTFY_TOPIC", "")
    assert notify.send_window_alerts(conn, now=NOW) == 0
    # And nothing was recorded as sent, so it can still fire once configured.
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
