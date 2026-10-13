"""Migrations that run when the poller opens an older database."""

from __future__ import annotations

from podiumwatch.db import Database

URL = "https://www.paradoxtilburg.nl/agenda/soft-machine-uk-3/"
NEW_KEY = "soft-machine-uk-3-202610162030"


def _show(conn, ext, first, last):
    cur = conn.execute(
        "INSERT INTO shows (venue_id, external_id, title, starts_at, url,"
        " first_seen, last_seen) VALUES ('paradox', ?, 'Soft Machine (UK)',"
        " '2026-10-16T20:30:00', ?, ?, ?)",
        (ext, URL, first, last),
    )
    return cur.lastrowid


def test_paradox_rows_are_rekeyed_and_a_split_show_is_merged(tmp_path):
    """Soft Machine lost its ticket button, and with it its identity.

    The old row (keyed on the Stager id) and the new one (keyed on the slug)
    must become one performance with one unbroken history.
    """
    path = tmp_path / "p.sqlite3"
    db = Database(path)
    c = db.conn
    old = _show(c, "111628493", "2026-10-03T10:00:00+00:00", "2026-10-12T10:00:00+00:00")
    twin = _show(c, NEW_KEY, "2026-10-13T10:00:00+00:00", "2026-10-13T17:00:00+00:00")
    c.execute(
        "INSERT INTO snapshots (show_id, observed_at, state)"
        " VALUES (?, '2026-10-03T10:00:00+00:00', 'available')", (old,),
    )
    c.execute(
        "INSERT INTO snapshots (show_id, observed_at, state)"
        " VALUES (?, '2026-10-13T10:00:00+00:00', 'sold_out')", (twin,),
    )
    c.execute(
        "INSERT INTO starred (venue_id, external_id, created_at)"
        " VALUES ('paradox', '111628493', '2026-10-05')"
    )
    db.commit()
    db.close()

    db = Database(path)  # the migration runs on open
    rows = db.conn.execute("SELECT * FROM shows WHERE venue_id = 'paradox'").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == old
    assert row["external_id"] == NEW_KEY
    assert row["last_seen"] == "2026-10-13T17:00:00+00:00"

    states = [
        r["state"] for r in db.conn.execute(
            "SELECT state FROM snapshots WHERE show_id = ? ORDER BY observed_at", (old,)
        )
    ]
    assert states == ["available", "sold_out"]
    assert db.conn.execute("SELECT external_id FROM starred").fetchone()[0] == NEW_KEY

    # And running it again changes nothing.
    db.close()
    db = Database(path)
    assert db.conn.execute("SELECT COUNT(*) FROM shows").fetchone()[0] == 1
