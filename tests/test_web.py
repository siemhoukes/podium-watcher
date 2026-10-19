"""What the dashboard shows when the data behind it is not fresh."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from podiumwatch import clock, web
from podiumwatch.db import Database


def iso(stamp: datetime) -> str:
    return stamp.isoformat(timespec="seconds")


def test_cancelled_shows_go_and_stale_ones_are_dated(tmp_path, monkeypatch):
    path = tmp_path / "w.sqlite3"
    db = Database(path)
    c = db.conn
    now_utc = datetime.now(timezone.utc)
    starts = (clock.now() + timedelta(days=2)).replace(
        hour=20, minute=15, second=0, microsecond=0
    )

    def show(venue, ext, title, last_seen):
        cur = c.execute(
            "INSERT INTO shows (venue_id, external_id, title, starts_at, url,"
            " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (venue, ext, title, starts.isoformat(), "https://x.test/",
             "https://x.test/order/add/1", last_seen, last_seen),
        )
        c.execute(
            "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents)"
            " VALUES (?,?,?,?)",
            (cur.lastrowid, last_seen, "available", 2500),
        )

    # Chasse: a complete run an hour ago listed one show but not the other.
    c.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, complete)"
        " VALUES ('chasse', ?, 1, 1, 1)", (iso(now_utc - timedelta(hours=1)),),
    )
    show("chasse", "a", "Nog te zien", iso(now_utc - timedelta(minutes=55)))
    show("chasse", "b", "Geschrapt", iso(now_utc - timedelta(days=9)))

    # Parktheater: stuck behind its waiting room for two days.
    c.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, complete)"
        " VALUES ('parktheater', ?, 1, 1, 1)",
        (iso(now_utc - timedelta(days=2, minutes=5)),),
    )
    c.execute(
        "INSERT INTO poll_runs (venue_id, started_at, ok, shows_found, error)"
        " VALUES ('parktheater', ?, 0, 0, ?)",
        (iso(now_utc - timedelta(hours=1)),
         "Agenda niet opgehaald: virtuele wachtrij (HTTP 200)"),
    )
    show("parktheater", "c", "Oude stand", iso(now_utc - timedelta(days=2)))
    db.commit()
    db.close()

    monkeypatch.setattr(web, "DB_PATH", path.as_posix())
    html = web.app.test_client().get("/?days=7").get_data(as_text=True)

    assert "Nog te zien" in html
    # No longer on the venue's own listing: not offered as if it were.
    assert "Geschrapt" not in html
    # Still offered, but with the date its status was last read.
    assert "Oude stand" in html and "stand van" in html
    # And the banner says why.
    assert "wachtrij" in html


def test_music_filter_and_a_concert_without_its_youth_rate(tmp_path, monkeypatch):
    import json as _json

    path = tmp_path / "m.sqlite3"
    db = Database(path)
    c = db.conn
    now_utc = datetime.now(timezone.utc)
    seen = iso(now_utc - timedelta(minutes=30))
    starts = (clock.now() + timedelta(days=3)).replace(hour=20, minute=15, second=0, microsecond=0)

    def show(venue, ext, title, tags, ticket_url, door, youth=None):
        cur = c.execute(
            "INSERT INTO shows (venue_id, external_id, title, starts_at, url, tags,"
            " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (venue, ext, title, starts.isoformat(), "https://x.test/", _json.dumps(tags),
             ticket_url, seen, seen),
        )
        c.execute(
            "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents,"
            " price_student_cents) VALUES (?,?,?,?,?)",
            (cur.lastrowid, seen, "available", door, youth),
        )

    tm = "https://apps.ticketmatic.com/widgets/tilburg_theaters/addtickets?event=1"
    show("tilburg", "orc", "Philzuid speelt Ravel", ["klassieke muziek"], tm, 3200)
    show("tilburg", "cab", "Een cabaretier", ["cabaret & comedy"], tm, 2200)
    show("doelen", "rec", "Pianoavond zonder tarief", ["klassiek"],
         "https://www.dedoelen.nl/order/add/1", 3500)
    db.commit()
    db.close()

    monkeypatch.setattr(web, "DB_PATH", path.as_posix())
    client = web.app.test_client()

    html = client.get("/?days=7&muziek=orkest").get_data(as_text=True)
    assert "Philzuid speelt Ravel" in html
    assert "Een cabaretier" not in html
    assert ">Orkest<" in html  # the genre label on the row

    html = client.get("/?days=7&muziek=alle").get_data(as_text=True)
    assert "Pianoavond zonder tarief" in html
    # de Doelen publishes no youth rate for it: say so, do not promise one.
    assert "geen jongerentarief bij dit concert" in html


def test_free_concerts_and_labelled_doelen_concerts_are_not_called_dealless(tmp_path, monkeypatch):
    import json as _json

    path = tmp_path / "f.sqlite3"
    db = Database(path)
    c = db.conn
    now_utc = datetime.now(timezone.utc)
    seen = iso(now_utc - timedelta(minutes=30))
    soon = (clock.now() + timedelta(hours=30)).replace(second=0, microsecond=0)

    def show(venue, ext, title, tags, ticket_url, door, note=None):
        cur = c.execute(
            "INSERT INTO shows (venue_id, external_id, title, starts_at, url, tags,"
            " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (venue, ext, title, soon.isoformat(), "https://x.test/", _json.dumps(tags),
             ticket_url, seen, seen),
        )
        c.execute(
            "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents, note)"
            " VALUES (?,?,?,?,?)",
            (cur.lastrowid, seen, "available", door, note),
        )

    tm = "https://apps.ticketmatic.com/widgets/tilburg_theaters/addtickets?event=1"
    # Free lunch concert, inside Tilburg's window, box office read: free, not "no deal".
    show("tilburg", "lunch", "Gratis lunchconcert", ["klassieke muziek"], tm, 0, note="good")
    # de Doelen labels it, but the card shows no price yet (Andreas Scholl).
    show("doelen", "scholl", "Andreas Scholl", ["klassiek", "jongerentarief"],
         "https://www.dedoelen.nl/order/add/9", None)
    db.commit()
    db.close()

    monkeypatch.setattr(web, "DB_PATH", path.as_posix())
    html = web.app.test_client().get("/?days=7&free=1").get_data(as_text=True)
    assert "Gratis lunchconcert" in html and "Andreas Scholl" in html
    assert "geen jongerenprijs in de verkoop" not in html
    assert "geen jongerentarief bij dit concert" not in html
    assert 'rel="icon"' in html


def _switch_href(html: str, label: str) -> str:
    import re as _re

    m = _re.search(r'<a class="switch[^"]*" href="([^"]*)">\s*<span class="track"><i></i></span>\s*'
                   + _re.escape(label), html)
    assert m, f"no switch labelled {label!r}"
    return m.group(1).replace("&amp;", "&")


def test_the_show_free_and_show_film_switches_actually_switch(tmp_path, monkeypatch):
    """'gratis tonen' linked to free=0 while free shows were hidden -- which
    keeps them hidden -- so it never did anything. Reported in the very first
    round of feedback and wrongly put down to there being no free shows."""
    path = tmp_path / "s.sqlite3"
    Database(path).close()
    monkeypatch.setattr(web, "DB_PATH", path.as_posix())
    client = web.app.test_client()

    for page, label, param in [("/", "gratis tonen", "free"), ("/", "film tonen", "film"),
                               ("/calendar", "film tonen", "film")]:
        off = client.get(page).get_data(as_text=True)
        turn_on = _switch_href(off, label)
        assert f"{param}=1" in turn_on, (page, label, turn_on)
        on = client.get(page + turn_on).get_data(as_text=True)
        assert f"{param}=" not in _switch_href(on, label), (page, label)


def test_calendar_leaves_out_past_and_dealless_shows_and_filters_music(tmp_path, monkeypatch):
    import json as _json

    path = tmp_path / "c.sqlite3"
    db = Database(path)
    c = db.conn
    now = clock.now()
    seen = iso(datetime.now(timezone.utc) - timedelta(minutes=30))
    tm = "https://apps.ticketmatic.com/widgets/tilburg_theaters/addtickets?event=1"

    def show(ext, title, tags, starts, venue="tilburg", url=tm, door=2500, youth=None):
        cur = c.execute(
            "INSERT INTO shows (venue_id, external_id, title, starts_at, url, tags,"
            " ticket_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (venue, ext, title, starts.isoformat(), "https://x.test/", _json.dumps(tags),
             url, seen, seen),
        )
        c.execute(
            "INSERT INTO snapshots (show_id, observed_at, state, price_min_cents,"
            " price_student_cents) VALUES (?,?,?,?,?)",
            (cur.lastrowid, seen, "available", door, youth),
        )

    later = now + timedelta(days=3)
    show("past", "Al gespeeld", ["klassieke muziek"], now - timedelta(hours=3))
    show("orc", "Orkestavond", ["klassieke muziek"], later)
    show("cab", "Cabaretavond", ["cabaret & comedy"], later)
    show("rec", "Doelen zonder tarief", ["klassiek"], later, venue="doelen",
         url="https://www.dedoelen.nl/order/add/1", door=3500)
    db.commit()
    db.close()

    monkeypatch.setattr(web, "DB_PATH", path.as_posix())
    client = web.app.test_client()
    month = f"?year={later.year}&month={later.month}"
    html = client.get("/calendar" + month).get_data(as_text=True)
    assert "Orkestavond" in html and "Cabaretavond" in html
    assert "Doelen zonder tarief" not in html
    if (now - timedelta(hours=3)).month == later.month:
        assert "Al gespeeld" not in html

    html = client.get("/calendar" + month + "&muziek=orkest").get_data(as_text=True)
    assert "Orkestavond" in html and "Cabaretavond" not in html
