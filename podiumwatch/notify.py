"""Telling you when a discount window opens on something you starred.

This is the moment the whole project exists for. The dashboard answers "what
is worth planning around"; this answers "you can buy it now", which is only
true for a few hours and cannot be discovered by looking at a page you are not
looking at.

Everything goes by email when SMTP is configured: that is the channel that
actually reaches a phone -- ntfy pushes were going unread. ntfy remains as a
fallback for a box with no mail set up. Several windows opening in one poll
arrive as one mail, not a burst.

The same channel carries one more kind of message: a scraper that has been
broken for half a day. Scrapers fail silently, and in September 2026 four
venues lost most of their programme for three weeks while /status, which
nobody opens, was the only place that could have said so.

Nothing is sent twice: `alerts_sent` records each (performance, kind) pair, so
a window that stays open across four polls produces one notification. That
table is also why alerts live in the poller rather than the dashboard -- a page
nobody has open cannot notice anything.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

import requests

from . import categories, clock, deals, health

log = logging.getLogger(__name__)

WINDOW_OPEN = "window_open"

# ntfy: set PODIUM_NTFY_TOPIC to a private topic URL, e.g.
# https://ntfy.sh/podium-<something-unguessable>
NTFY_TOPIC = os.environ.get("PODIUM_NTFY_TOPIC", "").strip()

SMTP_HOST = os.environ.get("PODIUM_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("PODIUM_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("PODIUM_SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("PODIUM_SMTP_PASS", "").strip()
MAIL_TO = os.environ.get("PODIUM_MAIL_TO", "").strip()


def mail_enabled() -> bool:
    return bool(SMTP_HOST and MAIL_TO)


def enabled() -> bool:
    """Any channel at all."""
    return mail_enabled() or bool(NTFY_TOPIC)


def push(title: str, message: str, url: str | None = None,
         priority: str = "default", tags: str = "") -> bool:
    """Send one ntfy notification. Returns False if it did not go out."""
    if not NTFY_TOPIC:
        log.debug("notify: no PODIUM_NTFY_TOPIC set, skipping push")
        return False

    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if url:
        headers["Click"] = url
    try:
        resp = requests.post(
            NTFY_TOPIC, data=message.encode("utf-8"), headers=headers, timeout=15
        )
    except requests.RequestException as exc:
        log.warning("notify: ntfy push failed: %s", exc)
        return False
    if resp.status_code >= 300:
        log.warning("notify: ntfy returned %s", resp.status_code)
        return False
    return True


def send_mail(subject: str, body: str) -> bool:
    if not (SMTP_HOST and MAIL_TO):
        log.debug("notify: SMTP not configured, skipping mail")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or MAIL_TO
    msg["To"] = MAIL_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - mail failing must not kill a poll
        log.warning("notify: mail failed: %s", exc)
        return False
    return True


# --------------------------------------------------------------------------


def _vanished(row, cutoffs: dict[str, str]) -> bool:
    """The venue's latest complete listing no longer has this show."""
    cutoff = cutoffs.get(row["venue_id"])
    return bool(cutoff and row["last_seen"] and row["last_seen"] < cutoff)


def due_window_alerts(conn, now: datetime | None = None) -> list[dict]:
    """Starred performances whose discount window is open and unannounced.

    Only starred ones: you asked for a quiet tool, and a push for every
    Tilburg show whose 48 hours just started would be several a day.
    """
    now = now or clock.now()
    rows = conn.execute(
        """
        SELECT s.id, s.venue_id, s.external_id, s.title, s.subtitle,
               s.starts_at, s.url, s.hall, s.last_seen
        FROM shows s
        JOIN starred k
          ON k.venue_id = s.venue_id AND k.external_id = s.external_id
        WHERE s.starts_at IS NOT NULL AND s.starts_at > ?
        """,
        (now.isoformat(),),
    ).fetchall()

    already = {
        (r["venue_id"], r["external_id"])
        for r in conn.execute(
            "SELECT venue_id, external_id FROM alerts_sent WHERE kind = ?",
            (WINDOW_OPEN,),
        )
    }

    cutoffs = health.seen_cutoffs(conn)
    due = []
    for row in rows:
        key = (row["venue_id"], row["external_id"])
        if key in already:
            continue
        if _vanished(row, cutoffs):
            continue
        starts_at = datetime.fromisoformat(row["starts_at"])
        state, _ = deals.window_state(row["venue_id"], starts_at, now)
        if state != "open":
            continue

        latest = conn.execute(
            "SELECT state, seats_free, seats_total, price_min_cents,"
            " price_student_cents FROM snapshots"
            " WHERE show_id = ? ORDER BY observed_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if latest and latest["state"] == "sold_out":
            continue  # the window opened, but there is nothing to buy

        item = dict(row)
        item["starts_at"] = starts_at
        item["snapshot"] = dict(latest) if latest else {}
        due.append(item)
    return due


def format_window_alert(item: dict) -> tuple[str, str]:
    """-> (title, body) for one opened window."""
    snap = item.get("snapshot") or {}
    deal = deals.deal_for(item["venue_id"])
    fare = deals.deal_price_cents(
        item["venue_id"],
        snap.get("price_min_cents"),
        snap.get("price_student_cents"),
    )

    price = f"€{fare / 100:.2f}".replace(".", ",") if fare is not None else "prijs onbekend"
    title = f"{price} — {item['title']}"

    lines = [
        item["subtitle"] or "",
        f"{item['starts_at']:%a %-d %b %H:%M}".replace("  ", " ")
        if os.name != "nt"
        else f"{item['starts_at']:%d-%m %H:%M}",
    ]
    if item.get("hall"):
        lines.append(item["hall"])
    if deal:
        lines.append(deal.label)

    free, total = snap.get("seats_free"), snap.get("seats_total")
    if free is not None and total:
        pct = 100 * (1 - free / total)
        lines.append(f"nog {free} van {total} plaatsen ({pct:.0f}% verkocht)")
    elif snap.get("state") == "limited":
        lines.append("laatste kaarten")

    return title, "\n".join(x for x in lines if x)


def format_window_mail(due: list[dict]) -> tuple[str, str]:
    """-> (subject, body): every window that opened this poll, in one mail."""
    blocks = []
    for item in due:
        title, body = format_window_alert(item)
        blocks.append("\n".join(x for x in (title, body, item.get("url") or "") if x))
    if len(due) == 1:
        subject = f"Podium: {format_window_alert(due[0])[0]}"
    else:
        subject = f"Podium: {len(due)} kortingsvensters open"
    return subject, "\n\n".join(blocks)


def send_window_alerts(conn, commit=True, now: datetime | None = None) -> int:
    """Announce every due alert and record it. Returns how many went out."""
    due = due_window_alerts(conn, now=now)
    if not due:
        return 0
    if not enabled():
        log.info("notify: %s alerts due but no mail or ntfy configured", len(due))
        return 0

    if mail_enabled():
        delivered = due if send_mail(*format_window_mail(due)) else []
    else:
        delivered = []
        for item in due:
            title, body = format_window_alert(item)
            if push(title, body, url=item.get("url"), priority="high", tags="ticket"):
                delivered.append(item)

    for item in delivered:
        conn.execute(
            "INSERT OR REPLACE INTO alerts_sent"
            " (venue_id, external_id, kind, sent_at) VALUES (?,?,?,?)",
            (
                item["venue_id"], item["external_id"], WINDOW_OPEN,
                clock.now().isoformat(timespec="seconds"),
            ),
        )
    if commit and delivered:
        conn.commit()
    return len(delivered)


# --------------------------------------------------------------------------
# Scraper health
# --------------------------------------------------------------------------

# State lives in alerts_sent under this pseudo show id: a "pending" row when a
# venue first looks broken, a "sent" row once the mail went out. Both go when
# it recovers.
HEALTH_KEY = "__health__"
HEALTH_PENDING = "health_pending"
HEALTH_SENT = "health"

# One failed poll is usually the network -- all nine venues failed together
# on 17 September. Half a day of it is a scraper.
HEALTH_GRACE = timedelta(hours=12)


def _venue_names() -> dict[str, str]:
    from .venues import ADAPTERS

    return {a.venue_id: a.name for a in ADAPTERS}


def format_health_mail(broken: list[dict], recovered: list[dict]) -> tuple[str, str]:
    names = _venue_names()

    def name(v: dict) -> str:
        return names.get(v["venue_id"], v["venue_id"])

    if broken:
        subject = (
            f"Podium: {name(broken[0])} wordt niet goed opgehaald"
            if len(broken) == 1
            else f"Podium: {len(broken)} podia worden niet goed opgehaald"
        )
    else:
        subject = (
            f"Podium: {name(recovered[0])} werkt weer"
            if len(recovered) == 1
            else f"Podium: {len(recovered)} podia werken weer"
        )

    lines = []
    if broken:
        lines.append("Al minstens 12 uur niet in orde:")
        lines.append("")
        for v in broken:
            lines.append(f"  {name(v)}: {v['message']}")
        lines.append("")
        lines.append(
            "Tot dit is opgelost kloppen de beschikbaarheid en de kortingen "
            "voor deze podia niet."
        )
        lines.append("")
    if recovered:
        lines.append("Weer in orde: " + ", ".join(name(v) for v in recovered))
        lines.append("")
    lines.append("Details: https://podium.siem.codes/status")
    return subject, "\n".join(lines)


def send_health_alerts(
    conn, venue_ids: list[str], now: datetime | None = None, commit: bool = True
) -> int:
    """Mail once when a venue has been broken for HEALTH_GRACE, and once when
    it recovers. Returns the number of mails sent (0 or 1)."""
    now = now or clock.now()
    report = health.venue_health(conn, venue_ids, now=now)
    state = {
        (r["venue_id"], r["kind"]): r["sent_at"]
        for r in conn.execute(
            "SELECT venue_id, kind, sent_at FROM alerts_sent WHERE external_id = ?",
            (HEALTH_KEY,),
        )
    }

    broken, recovered = [], []
    for v in report:
        vid = v["venue_id"]
        pending = state.get((vid, HEALTH_PENDING))
        sent = (vid, HEALTH_SENT) in state
        if v["status"] == health.OK:
            if sent:
                recovered.append(v)
            if pending or sent:
                conn.execute(
                    "DELETE FROM alerts_sent WHERE venue_id = ? AND external_id = ?",
                    (vid, HEALTH_KEY),
                )
            continue
        if not pending:
            conn.execute(
                "INSERT INTO alerts_sent (venue_id, external_id, kind, sent_at)"
                " VALUES (?,?,?,?)",
                (vid, HEALTH_KEY, HEALTH_PENDING, now.isoformat(timespec="seconds")),
            )
        elif not sent and now - datetime.fromisoformat(pending) >= HEALTH_GRACE:
            broken.append(v)

    mailed = 0
    if (broken or recovered) and mail_enabled():
        if send_mail(*format_health_mail(broken, recovered)):
            mailed = 1
            for v in broken:
                conn.execute(
                    "INSERT OR REPLACE INTO alerts_sent"
                    " (venue_id, external_id, kind, sent_at) VALUES (?,?,?,?)",
                    (v["venue_id"], HEALTH_KEY, HEALTH_SENT,
                     now.isoformat(timespec="seconds")),
                )
    elif broken or recovered:
        log.info("notify: scraper health changed but no mail configured")
    if commit:
        conn.commit()
    return mailed


# --------------------------------------------------------------------------
# Weekly digest
# --------------------------------------------------------------------------

DIGEST_DAYS = 10


def digest_rows(conn, days: int = DIGEST_DAYS, now: datetime | None = None) -> list[dict]:
    """The cheap, gettable, nearby things worth planning around.

    Deliberately not everything: a digest listing 300 performances is a digest
    nobody reads. Sold-out, externally-sold and hidden productions are out, and
    what is left is ordered by what you would actually pay.
    """
    now = now or clock.now()
    horizon = now + timedelta(days=days)

    rows = conn.execute(
        """
        SELECT s.id, s.venue_id, s.external_id, s.title, s.subtitle,
               s.starts_at, s.url, s.hall, s.ticket_url, s.tags, s.last_seen
        FROM shows s
        WHERE s.starts_at IS NOT NULL AND s.starts_at >= ? AND s.starts_at < ?
        ORDER BY s.starts_at
        """,
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()

    hidden = {
        (r["venue_id"], r["title"])
        for r in conn.execute("SELECT venue_id, title FROM hidden_productions")
    }
    starred = {
        (r["venue_id"], r["external_id"])
        for r in conn.execute("SELECT venue_id, external_id FROM starred")
    }

    cutoffs = health.seen_cutoffs(conn)
    out = []
    for row in rows:
        if (row["venue_id"], row["title"]) in hidden:
            continue
        if _vanished(row, cutoffs):
            continue
        if not deals.deal_applies(row["venue_id"], row["ticket_url"]):
            continue
        tags = json.loads(row["tags"]) if row["tags"] else []
        # Same filters as the dashboard. Without them the digest led with four
        # free showings of a children's play and two screenings of Minoes,
        # because sorting by price alone rewards exactly the wrong things.
        if categories.has_tag(tags, categories.KIDS | categories.FILM):
            continue
        if categories.is_course(row["title"], tags):
            continue
        latest = conn.execute(
            "SELECT state, seats_free, seats_total, price_min_cents,"
            " price_student_cents FROM snapshots WHERE show_id = ?"
            " ORDER BY observed_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if not latest or latest["state"] == "sold_out":
            continue

        fare = deals.deal_price_cents(
            row["venue_id"], latest["price_min_cents"], latest["price_student_cents"]
        )
        # A free listing is usually an open day or a talk, not a night out.
        if fare == 0:
            continue
        item = dict(row)
        item["starts_at"] = datetime.fromisoformat(row["starts_at"])
        item["fare"] = fare
        item["starred"] = (row["venue_id"], row["external_id"]) in starred
        item["snapshot"] = dict(latest)
        out.append(item)

    # Starred first, then cheapest; an unknown fare sorts last rather than
    # pretending to be free.
    out.sort(key=lambda i: (
        not i["starred"],
        i["fare"] if i["fare"] is not None else 10**9,
        i["starts_at"],
    ))
    return out


def format_digest(rows: list[dict], days: int = DIGEST_DAYS) -> tuple[str, str]:
    if not rows:
        return (
            f"Podium — niets gevonden voor de komende {days} dagen",
            "Geen voorstellingen die aan de filters voldoen.",
        )

    lines = [
        f"De komende {days} dagen, gesorteerd op wat je zou betalen.",
        "",
    ]
    for item in rows[:15]:
        fare = (
            f"€{item['fare'] / 100:.2f}".replace(".", ",")
            if item["fare"] is not None else "prijs onbekend"
        )
        mark = "★ " if item["starred"] else "  "
        lines.append(
            f"{mark}{item['starts_at']:%a %d-%m %H:%M}  {fare:>14}  {item['title']}"
        )
        snap = item["snapshot"]
        free, total = snap.get("seats_free"), snap.get("seats_total")
        detail = item["url"] or ""
        if free is not None and total:
            pct = 100 * (1 - free / total)
            lines.append(f"      nog {free} van {total} plaatsen ({pct:.0f}% verkocht)")
        lines.append(f"      {detail}")
        lines.append("")

    if len(rows) > 15:
        lines.append(f"(+{len(rows) - 15} meer op https://podium.siem.codes)")

    subject = f"Podium — {len(rows)} voorstellingen komende {days} dagen"
    return subject, "\n".join(lines)


def send_digest(conn, days: int = DIGEST_DAYS) -> bool:
    subject, body = format_digest(digest_rows(conn, days=days), days=days)
    return send_mail(subject, body)
