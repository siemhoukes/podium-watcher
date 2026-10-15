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
from datetime import datetime

from .. import clock
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

# The widget carries a lookup of every price type the account uses, separate
# from the event's prices: {"id":"...","name":"t\/m 26 jaar & student",
# "typeid":2301,"remark":"last minute",...}. The event itself only has ids.
_PRICETYPE_NAME_RE = re.compile(
    r'\{"id":"([A-Za-z0-9_-]{30,})","name":"((?:[^"\\]|\\.)*)",'
    r'"typeid":\d+,"remark":"((?:[^"\\]|\\.)*)"'
)

# A youth or student fare, by its own name. Never the door price.
_YOUTH_NAME_RE = re.compile(
    r"student|jong|cjp|t/m\s*\d{2}\s*jaar|tot\s*\d{2}\s*jaar|onder\s*\d{2}|last.?minute",
    re.I,
)


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


def price_type_names(html: str) -> dict[str, str]:
    """-> {price type id: "name remark"} from the widget's lookup table."""
    out: dict[str, str] = {}
    for m in _PRICETYPE_NAME_RE.finditer(html or ""):
        try:
            name = json.loads(f'"{m.group(2)}"')
            remark = json.loads(f'"{m.group(3)}"')
        except ValueError:
            continue
        out[m.group(1)] = f"{name} {remark}".strip()
    return out


def _price_types(event: dict):
    """Yield (price type id, cents, conditions) for every price on offer."""
    # `"prices": null` happens -- five Verkadefabriek jazz concerts in October
    # 2026 -- and `.get("prices", {})` only covers a missing key.
    for contingent in (event.get("prices") or {}).get("contingents") or []:
        for pt in contingent.get("pricetypes") or []:
            price = pt.get("price")
            if price is None:
                continue
            conditions = [
                cd for ch in pt.get("saleschannels") or []
                for cd in ch.get("conditions") or []
            ]
            yield pt.get("pricetypeid"), int(round(float(price) * 100)), conditions


def _is_youth(type_id: str | None, names: dict[str, str] | None) -> bool:
    return bool(names and type_id in names and _YOUTH_NAME_RE.search(names[type_id]))


def prices(event: dict, names: dict[str, str] | None = None) -> tuple[int | None, int | None]:
    """-> (min, max) in cents of the normal fare: what anyone pays at the door.

    Two kinds of price type are not that:

    - anything with `conditions`. Tilburg's 'ambassadeurs' fare needs a Vriend
      voucher, and its EUR 13,50 youth fare carries a *date* condition -- it
      only exists from 48 hours before curtain;
    - a youth fare without conditions. Philzuid sells 't/m 26 jaar EUR 12,50'
      on every concert, unconditionally, and read as the cheapest normal price
      it made a EUR 42 seat look like a EUR 12,50 one with nothing to save --
      the Theater Rotterdam 't/m 30 jaar' mistake again, here.
    """
    normal = [
        cents for type_id, cents, conditions in _price_types(event)
        if not conditions and not _is_youth(type_id, names)
    ]
    if not normal:
        return None, None
    return min(normal), max(normal)


def _date_window(condition: dict) -> tuple[datetime | None, datetime | None]:
    value = condition.get("value") or {}
    out = []
    for key in ("start", "end"):
        try:
            out.append(datetime.fromisoformat(str(value.get(key))))
        except ValueError:
            out.append(None)
    return out[0], out[1]


def youth_fare(
    event: dict, names: dict[str, str] | None = None, now: datetime | None = None
) -> int | None:
    """The cheapest youth/student fare on offer right now, in cents.

    Either a youth fare sold without conditions (Philzuid's EUR 12,50), or a
    last-minute one whose date window is open. Tilburg's 't/m 26 jaar &
    student' EUR 13,50 is the second kind: it is added to the event exactly 48
    hours before curtain ("relativeStart": 2 days), which is also why it was
    never seen before -- every conditioned price type used to be discarded as
    voucher-gated. Its presence is the venue confirming the deal for that show.
    """
    now = now or clock.now()
    found: list[int] = []
    for type_id, cents, conditions in _price_types(event):
        if not cents:
            continue
        kinds = {c.get("type") for c in conditions}
        if not conditions:
            if _is_youth(type_id, names):
                found.append(cents)
        elif kinds == {"date"}:
            start, end = _date_window(next(c for c in conditions if c.get("type") == "date"))
            in_window = (start is None or start <= now) and (end is None or now <= end)
            # Without the name lookup, a window that runs up to curtain is the
            # last-minute fare; an early-bird window ends long before.
            last_minute = all(
                (c.get("value") or {}).get("relativeEnd") == 0 for c in conditions
            )
            if in_window and (_is_youth(type_id, names) or (not names and last_minute)):
                found.append(cents)
    return min(found) if found else None


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

    names = price_type_names(html)
    lo, hi = prices(event, names)
    if lo is not None:
        snap.price_min_cents, snap.price_max_cents = lo, hi
    youth = youth_fare(event, names)
    if youth is not None:
        snap.price_student_cents = youth

    # Only seated events have a plan worth asking about.
    if account and event.get("id") and event.get("seatingplancontingents"):
        free, total = seat_counts(fetcher, account, event["id"])
        if total:
            snap.seats_free, snap.seats_total = free, total
    return True
