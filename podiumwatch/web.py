"""Read-only Flask dashboard.

Never writes to the database, so it cannot disturb the poller no matter what
a page does.
"""

from __future__ import annotations

import calendar as calmod
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, render_template, request

from . import deals, forecast
from .models import AVAILABLE, LIMITED, SOLD_OUT

DB_PATH = Path(
    os.environ.get(
        "PODIUM_DB", Path(__file__).resolve().parent.parent / "data" / "podium.sqlite3"
    )
)

VENUE_META = {
    "tilburg": ("Schouwburg & Concertzaal", "Tilburg", 5),
    "verkadefabriek": ("Verkadefabriek", "Den Bosch", 25),
    "parktheater": ("Parktheater", "Eindhoven", 25),
    "chasse": ("Chassé", "Breda", 20),
    "mge": ("Muziekgebouw", "Eindhoven", 25),
}

# Templates live at the project root, not inside the package, so Flask's
# default of `podiumwatch/templates` has to be overridden.
app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_for(conn, start: datetime, end: datetime) -> list[dict]:
    shows = conn.execute(
        """
        SELECT * FROM shows
        WHERE starts_at IS NOT NULL AND starts_at >= ? AND starts_at < ?
        ORDER BY starts_at
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    if not shows:
        return []

    ids = [s["id"] for s in shows]
    marks = ",".join("?" * len(ids))
    snaps: dict[int, list[dict]] = {i: [] for i in ids}
    for row in conn.execute(
        f"SELECT * FROM snapshots WHERE show_id IN ({marks}) ORDER BY observed_at",
        ids,
    ):
        snaps[row["show_id"]].append(dict(row))

    now = datetime.now()
    out = []
    for s in shows:
        history = snaps.get(s["id"], [])
        latest = history[-1] if history else {}
        starts_at = datetime.fromisoformat(s["starts_at"])

        price_min = latest.get("price_min_cents")
        price_student = latest.get("price_student_cents")
        deal = deals.deal_for(s["venue_id"])
        deal_ok = deals.deal_applies(s["venue_id"], s["ticket_url"])
        # Sold through a third party: quote no student fare rather than a
        # wrong one.
        fare = (
            deals.deal_price_cents(s["venue_id"], price_min, price_student)
            if deal_ok else None
        )
        wstate, until = deals.window_state(s["venue_id"], starts_at, now)
        look = forecast.outlook(history, starts_at)

        name, city, travel = VENUE_META.get(
            s["venue_id"], (s["venue_id"], "", 30)
        )

        out.append(
            {
                "id": s["id"],
                "title": s["title"],
                "subtitle": s["subtitle"],
                "url": s["url"],
                "ticket_url": s["ticket_url"],
                "hall": s["hall"],
                "tags": json.loads(s["tags"]) if s["tags"] else [],
                "starts_at": starts_at,
                "venue_id": s["venue_id"],
                "venue": name,
                "city": city,
                "travel": travel,
                "deal": deal,
                "deal_ok": deal_ok,
                "fare": fare,
                "base_price": price_min,
                "published_student": price_student,
                "base_price_max": latest.get("price_max_cents"),
                # Only ever set when a venue really told us a number. Still
                # always None today -- see the note in the template.
                "seats_free": latest.get("seats_free"),
                "seats_total": latest.get("seats_total"),
                "state": latest.get("state", "unknown"),
                "outlook": look,
                "window_state": wstate,
                "window_in": until,
                "rank": _rank(
                    look, fare, travel,
                    max(0.0, (starts_at - now).total_seconds() / 86400),
                ),
            }
        )
    return out


# Categories hidden by default. Kids' theatre is the big one -- it is a
# sizeable share of every venue's programme and none of it is for you.
KIDS_TAGS = {"jeugd & familie", "kinderen"}
FILM_TAGS = {"movie", "film"}
COURSE_TAGS = {"cursus", "club"}


def _has_tag(tags: list[str], wanted: set[str]) -> bool:
    return any(t.strip().lower() in wanted for t in tags)


def _rank(look, fare: int | None, travel: int, days_away: float) -> float:
    """Best bet = you can probably get in, it is cheap, close, and soon.

    'Soon' is in here because every deal is last-minute: a show three days
    out is something you can actually act on, one in April is trivia. It also
    keeps the list from being a flat tie between forty identical Tilburg
    fares while the forecast has no history to differentiate them yet.
    """
    score = look.score * 0.45

    # Cheapest realistic fare here is €10, dearest worth showing ~€28.
    if fare is not None:
        score += 0.25 * max(0.0, min(1.0, (2800 - fare) / 1800))

    # Tilburg is a five-minute walk; that genuinely beats a train.
    score += 0.15 * max(0.0, min(1.0, (30 - travel) / 25))

    # Decays over about six weeks.
    score += 0.15 * max(0.0, min(1.0, 1.0 - days_away / 42.0))
    return score


PRICE_STEPS = [1000, 1500, 2000, 3000]


@app.route("/")
def index():
    days = max(1, min(60, request.args.get("days", 14, type=int)))
    sort = request.args.get("sort", "rank")
    hide_film = request.args.get("film", "0") != "1"
    venues = [v for v in request.args.getlist("venue") if v in VENUE_META]
    max_price = request.args.get("max", type=int)  # in cents
    hide_free = request.args.get("free", "0") != "1"

    now = datetime.now()
    with connect() as conn:
        rows = _rows_for(conn, now, now + timedelta(days=days))

    total_found = len(rows)

    # Sold-out performances stay in the database -- they are the most
    # informative thing that can happen to a show -- but there is no point
    # offering you a ticket that does not exist.
    rows = [r for r in rows if r["outlook"].band != forecast.GONE]
    # Externally sold events are in the building but not part of any deal.
    rows = [r for r in rows if r["deal_ok"]]
    rows = [r for r in rows if not _has_tag(r["tags"], KIDS_TAGS | COURSE_TAGS)]
    if hide_film:
        rows = [r for r in rows if not _has_tag(r["tags"], FILM_TAGS)]
    if venues:
        rows = [r for r in rows if r["venue_id"] in venues]
    if max_price:
        # A show whose fare we cannot compute is kept: hiding it would be
        # pretending we know it is expensive.
        rows = [r for r in rows if r["fare"] is None or r["fare"] <= max_price]
    if hide_free:
        rows = [r for r in rows if r["fare"] is None or r["fare"] > 0]

    if sort == "date":
        rows.sort(key=lambda r: r["starts_at"])
    else:
        rows.sort(key=lambda r: (-r["rank"], r["starts_at"]))

    return render_template(
        "index.html",
        rows=rows,
        days=days,
        sort=sort,
        hide_film=hide_film,
        venues=venues,
        max_price=max_price,
        hide_free=hide_free,
        price_steps=PRICE_STEPS,
        venue_meta=VENUE_META,
        hidden_count=total_found - len(rows),
        stats=_stats(),
        now=now,
    )


@app.route("/calendar")
def calendar_view():
    now = datetime.now()
    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    if not 1 <= month <= 12:
        month = now.month

    first = datetime(year, month, 1)
    last = datetime(year + (month == 12), (month % 12) + 1, 1)
    with connect() as conn:
        rows = _rows_for(conn, first, last)

    by_day: dict[int, list] = {}
    for r in rows:
        if r["outlook"].band == forecast.GONE or not r["deal_ok"]:
            continue
        if _has_tag(r["tags"], FILM_TAGS | KIDS_TAGS | COURSE_TAGS):
            continue
        by_day.setdefault(r["starts_at"].day, []).append(r)
    for day in by_day.values():
        day.sort(key=lambda r: (-r["rank"], r["starts_at"]))

    prev_month = (month - 1) or 12
    prev_year = year - (month == 1)
    next_month = (month % 12) + 1
    next_year = year + (month == 12)

    return render_template(
        "calendar.html",
        weeks=calmod.Calendar(firstweekday=0).monthdayscalendar(year, month),
        by_day=by_day,
        year=year,
        month=month,
        month_name=[
            "", "januari", "februari", "maart", "april", "mei", "juni", "juli",
            "augustus", "september", "oktober", "november", "december",
        ][month],
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        today=now,
        stats=_stats(),
    )


def _stats() -> dict:
    with connect() as conn:
        q = conn.execute
        total = q("SELECT COUNT(*) c FROM shows").fetchone()["c"]
        snaps = q("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
        last = q(
            "SELECT MAX(observed_at) t FROM snapshots"
        ).fetchone()["t"]
        venues = q(
            "SELECT COUNT(DISTINCT venue_id) c FROM shows"
        ).fetchone()["c"]
    last_dt = None
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            pass
    return {
        "shows": total,
        "snapshots": snaps,
        "venues": venues,
        "last_poll": last_dt,
    }


@app.context_processor
def _url_helpers():
    """Build links that keep every other filter intact."""

    def qs(**overrides) -> str:
        args = request.args.to_dict(flat=False)
        for key, value in overrides.items():
            if value is None:
                args.pop(key, None)
            elif isinstance(value, (list, tuple)):
                args[key] = list(value)
            else:
                args[key] = [str(value)]
        parts = [
            f"{k}={quote_plus(str(v))}" for k, vals in args.items() for v in vals
        ]
        return "?" + "&".join(parts) if parts else "?"

    def toggle_venue(venue_id: str) -> str:
        current = [v for v in request.args.getlist("venue") if v in VENUE_META]
        if venue_id in current:
            current = [v for v in current if v != venue_id]
        else:
            current = current + [venue_id]
        return qs(venue=current or None)

    return {"qs": qs, "toggle_venue": toggle_venue}


@app.template_filter("euro")
def euro(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"€{cents / 100:.2f}".replace(".", ",")


@app.template_filter("nl_date")
def nl_date(dt: datetime) -> str:
    days = ["ma", "di", "wo", "do", "vr", "za", "zo"]
    months = [
        "", "jan", "feb", "mrt", "apr", "mei", "jun",
        "jul", "aug", "sep", "okt", "nov", "dec",
    ]
    return f"{days[dt.weekday()]} {dt.day} {months[dt.month]}"


@app.template_filter("countdown")
def countdown(delta: timedelta | None) -> str:
    if delta is None:
        return ""
    total = int(delta.total_seconds())
    if total <= 0:
        return "nu"
    d, rem = divmod(total, 86400)
    h = rem // 3600
    if d:
        return f"{d}d {h}u"
    m = (rem % 3600) // 60
    return f"{h}u {m}m" if h else f"{m}m"
