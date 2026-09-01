"""Live occupancy from a Tixly box office.

MgE's own feed carries `tickets_free` / `tickets_total`, which look like a seat
count and are not -- they never differ, sold-out concerts included. This is the
real thing, and it comes from the seat picker rather than the feed:

    GET /nl/buyingflow/takenseats/?eventId=…&groupId=…&split=false
        -> {"si": [70968096, 70968097, …]}   the seats already gone

    GET /nl/buyingflow/hallsvg/{eventId}/{groupId}/
        -> the hall plan; every seat is an element with class "seat …"

Taken over total is the fill rate. It cross-checks exactly against what the
venue says about itself, which is the whole reason to trust it:

    uitverkocht      1266 / 1266   100%
    laatste kaarten  1240 / 1322    94%
    tickets           183 /  391    47%

Both are plain reads. `takenseats` asks which seats are *already* gone; it
holds nothing. The endpoints next to it in the same bundle -- `reserve`,
`sreserve` -- would, and are deliberately never called.

The hall plan is ~450 KB and never changes for a given performance, so it is
fetched once to learn the capacity and then read back from the database.
`takenseats` is a couple of kilobytes and is re-read every poll, because
watching that number move is the entire point.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

BASE = "https://tickets.mge.nl"
_URL_IDS = re.compile(r"/(\d+)/(\d+)/?$")
_SEAT = re.compile(r'class="seat[^"]*"')


def ids_from_ticket_url(ticket_url: str) -> tuple[str, str] | None:
    """'…/buyingflow/tickets/59882/122191/' -> (groupId, eventId)."""
    m = _URL_IDS.search(ticket_url or "")
    return (m.group(1), m.group(2)) if m else None


def taken_seats(fetcher, group_id: str, event_id: str, referer: str) -> int | None:
    """How many seats are already gone. None if the box office did not answer."""
    url = (
        f"{BASE}/nl/buyingflow/takenseats/"
        f"?eventId={event_id}&groupId={group_id}&split=false"
    )
    resp = fetcher.get(
        url,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*",
            "Referer": referer,
        },
    )
    if resp is None:
        return None
    try:
        data = resp.json() if hasattr(resp, "json") else json.loads(resp.text)
    except ValueError:
        log.warning("tixly: takenseats was not JSON for event %s", event_id)
        return None
    seats = data.get("si")
    return len(seats) if isinstance(seats, list) else None


def hall_capacity(fetcher, group_id: str, event_id: str) -> int | None:
    """Total seats in the plan for this performance.

    Static for a given performance, so the caller should ask once and keep it.
    """
    url = f"{BASE}/nl/buyingflow/hallsvg/{event_id}/{group_id}/"
    svg = fetcher.get_text(url)
    if not svg:
        return None
    total = len(_SEAT.findall(svg))
    return total or None
