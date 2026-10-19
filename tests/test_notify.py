"""Window-open alerting: who gets told, and only once."""

from __future__ import annotations

import json
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


# --------------------------------------------------------------------------
# Weekly digest
# --------------------------------------------------------------------------

def add_tagged(conn, ext, title, tags, price, days=2, state="available"):
    starts = NOW + timedelta(days=days)
    cur = conn.execute(
        "INSERT INTO shows (venue_id, external_id, title, starts_at, url, tags,"
        " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
        ("verkadefabriek", ext, title, starts.isoformat(), "https://e.test/x",
         tags, None, NOW.isoformat(), NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents)"
        " VALUES (?,?,?,?)",
        (cur.lastrowid, NOW.isoformat(), state, price),
    )
    conn.commit()


def test_digest_hides_what_the_dashboard_hides(conn):
    """It used to lead with free children's shows and Minoes screenings.

    Sorting by price alone rewards exactly the wrong things, so the digest
    applies the same category filters as the list.
    """
    add_tagged(conn, "kid", "Kinderding", '["Kinderen"]', 500)
    add_tagged(conn, "film", "Filmpje", '["Film"]', 800)
    add_tagged(conn, "real", "Echte voorstelling", '["Theater"]', 2600)

    titles = [r["title"] for r in notify.digest_rows(conn, now=NOW)]
    assert titles == ["Echte voorstelling"]


def test_digest_skips_free_listings(conn):
    """A EUR 0 listing is an open day or a talk, not a night out."""
    add_tagged(conn, "free", "Gratis ding", '["Theater"]', 0)
    assert notify.digest_rows(conn, now=NOW) == []


def test_digest_puts_starred_first(conn):
    add_tagged(conn, "cheap", "Goedkoop", '["Theater"]', 1000)
    add_tagged(conn, "fav", "Favoriet", '["Theater"]', 4000)
    conn.execute(
        "INSERT INTO starred (venue_id, external_id, created_at) VALUES (?,?,?)",
        ("verkadefabriek", "fav", NOW.isoformat()),
    )
    conn.commit()
    titles = [r["title"] for r in notify.digest_rows(conn, now=NOW)]
    assert titles[0] == "Favoriet"


def test_digest_skips_sold_out(conn):
    add_tagged(conn, "gone", "Uitverkocht", '["Theater"]', 2000, state="sold_out")
    assert notify.digest_rows(conn, now=NOW) == []


def test_an_empty_digest_still_formats(conn):
    subject, body = notify.format_digest([])
    assert "niets gevonden" in subject
    assert body


# --------------------------------------------------------------------------
# Email first
# --------------------------------------------------------------------------

@pytest.fixture
def mailbox(monkeypatch):
    """Mail configured, and every message captured instead of sent."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(notify, "MAIL_TO", "me@example.test")
    monkeypatch.setattr(notify, "NTFY_TOPIC", "")

    def fake_send(subject, body):
        sent.append((subject, body))
        return True

    monkeypatch.setattr(notify, "send_mail", fake_send)
    return sent


def test_window_alerts_arrive_as_one_mail(conn, mailbox):
    """Email is what reaches the phone; two windows are one message, not two."""
    add_show(conn, ext="1")
    add_show(conn, ext="2")
    assert notify.send_window_alerts(conn, now=NOW) == 2
    assert len(mailbox) == 1
    assert "2 kortingsvensters" in mailbox[0][0]
    # Recorded as sent, so the next poll stays quiet.
    assert notify.send_window_alerts(conn, now=NOW) == 0
    assert len(mailbox) == 1


def test_a_failed_mail_is_retried_next_poll(conn, monkeypatch, mailbox):
    add_show(conn)
    monkeypatch.setattr(notify, "send_mail", lambda subject, body: False)
    assert notify.send_window_alerts(conn, now=NOW) == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0


# --------------------------------------------------------------------------
# Scraper health by mail
# --------------------------------------------------------------------------

def poll_run(conn, venue, found, at, ok=1, error=None):
    conn.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, error,"
        " complete) VALUES (?,?,?,?,?,?)",
        (venue, at.isoformat(), ok, found, error, 1 if ok and not error else 0),
    )


def health_rows(conn):
    return conn.execute(
        "SELECT kind FROM alerts_sent WHERE external_id = ?", (notify.HEALTH_KEY,)
    ).fetchall()


def test_a_broken_scraper_is_mailed_once_after_half_a_day(conn, mailbox):
    h = timedelta(hours=1)
    err = "Agenda niet opgehaald: virtuele wachtrij (HTTP 200)"
    for hours in (0, 6):
        poll_run(conn, "chasse", 0, NOW + hours * h, ok=0, error=err)
        assert notify.send_health_alerts(conn, ["chasse"], now=NOW + hours * h) == 0
    assert mailbox == []

    poll_run(conn, "chasse", 0, NOW + 12 * h, ok=0, error=err)
    assert notify.send_health_alerts(conn, ["chasse"], now=NOW + 12 * h) == 1
    subject, body = mailbox[0]
    assert "Chass" in subject and "wachtrij" in body

    poll_run(conn, "chasse", 0, NOW + 18 * h, ok=0, error=err)
    assert notify.send_health_alerts(conn, ["chasse"], now=NOW + 18 * h) == 0
    assert len(mailbox) == 1

    poll_run(conn, "chasse", 700, NOW + 24 * h)
    assert notify.send_health_alerts(conn, ["chasse"], now=NOW + 24 * h) == 1
    assert "werkt weer" in mailbox[-1][0]
    assert health_rows(conn) == []


def test_a_single_failed_poll_never_mails(conn, mailbox):
    """All nine venues failed together on 17 September: that was the network."""
    poll_run(conn, "chasse", 0, NOW, ok=0, error="netwerkfout: ConnectionError")
    notify.send_health_alerts(conn, ["chasse"], now=NOW)
    poll_run(conn, "chasse", 700, NOW + timedelta(hours=6))
    notify.send_health_alerts(conn, ["chasse"], now=NOW + timedelta(hours=6))
    assert mailbox == []
    assert health_rows(conn) == []


# --------------------------------------------------------------------------
# Shows the venue no longer lists
# --------------------------------------------------------------------------

def test_digest_leaves_out_shows_the_venue_no_longer_lists(conn):
    add_tagged(conn, "1", "Nog op de agenda", "[]", 1500)
    add_tagged(conn, "2", "Geschrapt", "[]", 1500)
    # A complete run after both were first seen, which listed only the first.
    poll_run(conn, "verkadefabriek", 1, NOW + timedelta(minutes=30))
    conn.execute(
        "UPDATE shows SET last_seen = ? WHERE external_id = '1'",
        ((NOW + timedelta(minutes=31)).isoformat(),),
    )
    titles = [r["title"] for r in notify.digest_rows(conn, now=NOW)]
    assert titles == ["Nog op de agenda"]


# --------------------------------------------------------------------------
# Concerts with a discount on sale -- mailed without a star
# --------------------------------------------------------------------------

def concert(conn, venue, ext, title, tags, hours, ticket_url, door, youth=None,
            note="good", state="available", read_minutes_ago=10):
    starts = NOW + timedelta(hours=hours)
    cur = conn.execute(
        "INSERT INTO shows (venue_id, external_id, title, starts_at, url, tags,"
        " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
        (venue, ext, title, starts.isoformat(), f"https://e.test/{ext}",
         json.dumps(tags), ticket_url, NOW.isoformat(), NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents,"
        " price_max_cents, price_student_cents, note, seats_free, seats_total)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (cur.lastrowid, (NOW - timedelta(minutes=read_minutes_ago)).isoformat(),
         state, door, door, youth, note, 300, 766),
    )
    conn.commit()


TM = "https://apps.ticketmatic.com/widgets/tilburg_theaters/addtickets?event=1"


def test_concerts_with_a_discount_on_sale_are_mailed_without_a_star(conn, mailbox):
    # Tilburg, inside the 48h window, youth fare seen in the box office.
    concert(conn, "tilburg", "phil", "Philzuid", ["klassieke muziek"], 30, TM, 3200, youth=1350)
    # Tilburg, inside the window, box office read -- and no youth fare.
    concert(conn, "tilburg", "hvv", "Groot orkest", ["klassieke muziek"], 30, TM, 4450)
    # Tilburg cabaret: a discount, but not music.
    concert(conn, "tilburg", "cab", "Martijn Koning", ["cabaret & comedy"], 30, TM, 2150, youth=1350)
    # de Doelen: only where the concert publishes its youth rate.
    concert(conn, "doelen", "bach", "Bach Collegium Japan", ["klassiek", "jongerentarief"],
            72, "https://www.dedoelen.nl/order/add/1", 3500, youth=1600, note=None)
    concert(conn, "doelen", "noyouth", "Pianoavond", ["klassiek"],
            72, "https://www.dedoelen.nl/order/add/2", 3500, note=None)
    concert(conn, "doelen", "far", "Te ver weg", ["klassiek", "jongerentarief"],
            24 * 10, "https://www.dedoelen.nl/order/add/3", 3500, youth=1600, note=None)
    # MgE: Spotticket window open, but they pick which concerts carry it.
    concert(conn, "mge", "mge1", "Strijkkwartet avond", ["Klassiek", "Strijkkwartet"],
            20, "https://tickets.mge.nl/x", 2500, note=None)

    due = notify.due_music_alerts(conn, now=NOW)
    assert [d["external_id"] for d in due] == ["phil", "bach", "mge1"]
    assert due[0]["confirmed"] and not due[0]["maybe"]
    assert due[2]["maybe"]

    assert notify.send_music_alerts(conn, now=NOW) == 3
    assert len(mailbox) == 1
    subject, body = mailbox[0]
    assert "3 concerten" in subject
    assert "bevestigd" in body and "mogelijk" in body
    assert "Martijn Koning" not in body and "Groot orkest" not in body
    # Recorded: the next poll stays quiet.
    assert notify.send_music_alerts(conn, now=NOW) == 0


def test_concert_alerts_wait_for_mail_to_be_configured(conn, monkeypatch):
    concert(conn, "tilburg", "phil", "Philzuid", ["klassieke muziek"], 30, TM, 3200, youth=1350)
    monkeypatch.setattr(notify, "SMTP_HOST", "")
    assert notify.send_music_alerts(conn, now=NOW) == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
