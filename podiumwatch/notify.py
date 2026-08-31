"""Telling you when a discount window opens on something you starred.

This is the moment the whole project exists for. The dashboard answers "what
is worth planning around"; this answers "you can buy it now", which is only
true for a few hours and cannot be discovered by looking at a page you are not
looking at.

Push goes to ntfy because it needs no account or bot token and the Pi can
reach it with one POST. The weekly digest goes over SMTP, matching every other
watcher in this collection.

Nothing is sent twice: `alerts_sent` records each (performance, kind) pair, so
a window that stays open across four polls produces one notification. That
table is also why alerts live in the poller rather than the dashboard -- a page
nobody has open cannot notice anything.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

import requests

from . import deals

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


def enabled() -> bool:
    return bool(NTFY_TOPIC)


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


def due_window_alerts(conn, now: datetime | None = None) -> list[dict]:
    """Starred performances whose discount window is open and unannounced.

    Only starred ones: you asked for a quiet tool, and a push for every
    Tilburg show whose 48 hours just started would be several a day.
    """
    now = now or datetime.now()
    rows = conn.execute(
        """
        SELECT s.id, s.venue_id, s.external_id, s.title, s.subtitle,
               s.starts_at, s.url, s.hall
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

    due = []
    for row in rows:
        key = (row["venue_id"], row["external_id"])
        if key in already:
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


def send_window_alerts(conn, commit=True, now: datetime | None = None) -> int:
    """Push every due alert and record it. Returns how many went out."""
    due = due_window_alerts(conn, now=now)
    if not due:
        return 0
    if not enabled():
        log.info("notify: %s alerts due but no ntfy topic configured", len(due))
        return 0

    sent = 0
    for item in due:
        title, body = format_window_alert(item)
        ok = push(
            title, body, url=item.get("url"),
            priority="high", tags="ticket",
        )
        if not ok:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO alerts_sent"
            " (venue_id, external_id, kind, sent_at) VALUES (?,?,?,?)",
            (
                item["venue_id"], item["external_id"], WINDOW_OPEN,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        sent += 1
    if commit and sent:
        conn.commit()
    return sent
