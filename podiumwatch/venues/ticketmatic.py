"""Reading real seat counts and prices out of a Ticketmatic ticket shop.

This is the good stuff. A venue's own website may publish nothing useful --
Tilburg shows neither a price nor a seat count anywhere -- but the signed
widget URL its "koop tickets" button points at embeds the whole event as JSON,
and a second call returns the seating plan's occupancy.

    GET <signed widget url>
        -> events[0].contingents[]            unseated: 4-level availability
        -> events[0].seatingplancontingents[] seated: 4-level per rank
        -> events[0].prices.contingents[]     real prices, with conditions
        -> account.id

    GET /widgets/backend/{account}/nl/eventseatingplan/{event_id}/availability/1
        -> {"sections":[{"rank":10000,"av":427,"tot":619}, ...]}

`av`/`tot` are free and total seats per rank. Summed, that is an exact fill
rate with a real denominator -- the thing the forecast was missing.

Two details that cost time to find:

- The **short** event id in the listing URL (`event=40274`) is rejected on its
  own; the widget only answers for the full signed URL, which carries an
  `accesskey` and `signature`. That URL is already stored as `shows.ticket_url`.
- The `{code}` in the seating-plan path is not any numeric id but the event's
  own hashed `id` field from the embedded JSON.

Unseated events ("vrije zit", "staanplaats") have no seating plan at all, so
they yield the 4-level word and no number. That is reported honestly rather
than converted into a fake count.
"""

from __future__ import annotations

import json
import logging
import re

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Snapshot

log = logging.getLogger(__name__)

BACKEND = "https://apps.ticketmatic.com/widgets/backend"

# Ticketmatic's four-level scale, best to worst.
AVAILABILITY_MAP = {
    "good": AVAILABLE,
    "reduced": AVAILABLE,
    "last": LIMITED,
    "none": SOLD_OUT,
}
_RANK = {"good": 3, "reduced": 2, "last": 1, "none": 0}

_ACCOUNT_RE = re.compile(r'"account":\{"name":"[^"]*","id":"([^"]+)"')


def parse_widget(html: str) -> tuple[dict | None, str | None]:
    """-> (event object, account id). Both None if the widget had no event."""
    account = None
    m = _ACCOUNT_RE.search(html)
    if m:
        account = m.group(1)

    i = html.find('"events":[{')
    if i < 0:
        return None, account
    start = html.index("[", i)
    depth = 0
    for k in range(start, len(html)):
        if html[k] == "[":
            depth += 1
        elif html[k] == "]":
            depth -= 1
            if depth == 0:
                try:
                    events = json.loads(html[start : k + 1])
                except ValueError:
                    log.warning("ticketmatic: events array did not parse")
                    return None, account
                return (events[0] if events else None), account
    return None, account


def availability_state(event: dict) -> tuple[str, str | None]:
    """-> (our state, raw Ticketmatic word).

    A seated event carries one entry per rank and they disagree often --
    '1e rang sold out, 3e rang plenty' is normal. The best rank wins, because
    one sold-out rank does not stop you getting in.
    """
    entries = (event.get("seatingplancontingents") or []) + (
        event.get("contingents") or []
    )
    words = [e.get("availability") for e in entries if e.get("availability")]
    if not words:
        return UNKNOWN, None
    best = max(words, key=lambda w: _RANK.get(w, -1))
    return AVAILABILITY_MAP.get(best, UNKNOWN), best


def prices(event: dict) -> tuple[int | None, int | None]:
    """-> (min, max) in cents, over price types anyone can buy.

    Price types carrying `conditions` are gated -- Tilburg's 'ambassadeurs'
    fare needs a Vriend voucher -- so they are not the normal price.
    """
    open_prices: list[int] = []
    for contingent in event.get("prices", {}).get("contingents", []):
        for pt in contingent.get("pricetypes", []):
            channels = pt.get("saleschannels") or []
            gated = any(ch.get("conditions") for ch in channels)
            price = pt.get("price")
            if gated or price is None:
                continue
            open_prices.append(int(round(float(price) * 100)))
    if not open_prices:
        return None, None
    return min(open_prices), max(open_prices)


def seat_counts(fetcher, account: str, event_id: str) -> tuple[int | None, int | None]:
    """-> (free, total) summed over ranks, or (None, None) if unseated."""
    url = f"{BACKEND}/{account}/nl/eventseatingplan/{event_id}/availability/1"
    resp = fetcher.get(url, headers={"Accept": "application/json"})
    if resp is None:
        return None, None
    try:
        data = resp.json()
    except ValueError:
        log.warning("ticketmatic: availability was not JSON for %s", event_id[:12])
        return None, None

    sections = data.get("sections") or []
    if not sections:
        return None, None
    free = sum(s.get("av", 0) for s in sections)
    total = sum(s.get("tot", 0) for s in sections)
    return (free, total) if total else (None, None)


# The event JSON sits in the first ~60 KB. Verkadefabriek's widget is 5.7 MB
# in total, almost all of it bundle, so the read is capped well past where the
# data ends but far short of the whole document.
WIDGET_HEAD_BYTES = 1_500_000


def enrich_from_widget(fetcher, ticket_url: str, snap: Snapshot) -> bool:
    """Fill `snap` with prices, availability and seat counts. True if fetched."""
    html = fetcher.get_text(ticket_url, max_bytes=WIDGET_HEAD_BYTES)
    if not html:
        return False

    event, account = parse_widget(html)
    if not event:
        if len(html) >= WIDGET_HEAD_BYTES:
            log.warning(
                "ticketmatic: no event found within the first %s bytes of %s",
                WIDGET_HEAD_BYTES, ticket_url[:90],
            )
        return True  # request spent, but nothing to read

    state, word = availability_state(event)
    if state != UNKNOWN:
        snap.state = state
    snap.note = word

    lo, hi = prices(event)
    if lo is not None:
        snap.price_min_cents, snap.price_max_cents = lo, hi

    # Only seated events have a plan worth asking about.
    if account and event.get("id") and event.get("seatingplancontingents"):
        free, total = seat_counts(fetcher, account, event["id"])
        if total:
            snap.seats_free, snap.seats_total = free, total
    return True
