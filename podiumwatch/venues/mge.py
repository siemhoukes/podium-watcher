"""Muziekgebouw Eindhoven.

Deal: EUR 10 Spotticket, under 30, from 24h before, on selected concerts.

The agenda page is a dead end and stayed marked "blocked" for a while: it
renders client-side, and the 264 event links sitting in the HTML are hidden
SEO placeholders carrying neither a date nor availability. `wp-json` answers
401, and `tickets.mge.nl/nl/events/` redirects to Tixly's corporate site.

The way in is `https://mge.nl/event_feed_json/` -- a single request that
returns the entire programme, found by listing the live page's own loaded
resources rather than by guessing URLs. It gives, per performance:

    program_start   exact datetime
    location        which hall
    price           per rank, with a `visible` flag
    status_label    Tickets / Laatste kaarten / Uitverkocht / Externe
                    verkoop / Gratis / Geannuleerd
    ticket_link     Tixly, or a third party for external sales

**`tickets_free` and `tickets_total` are deliberately ignored.** They look
like a live seat count and are not: across the whole feed there is not one
record where they differ, including eight marked *Uitverkocht* that still
claim every seat free. They are static capacity. Treating them as occupancy
would have produced a confident "0% sold" on a sold-out Marillion.

"Externe verkoop" links to feverup.com and the like. The concert happens in
the building but the venue is not selling it, so a Spotticket cannot apply --
the same rule already used for Tilburg's pub quizzes.
"""

from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents

log = logging.getLogger(__name__)

FEED = "https://mge.nl/event_feed_json/"

STATUS_MAP = {
    "tickets": AVAILABLE,
    "laatste kaarten": LIMITED,
    "uitverkocht": SOLD_OUT,
    "externe verkoop": AVAILABLE,  # on sale, just not by the venue
    "gratis": AVAILABLE,
    "reserveer gratis ticket": AVAILABLE,
    "reserveer gratis tickets": AVAILABLE,
    "geannuleerd": UNKNOWN,
}

# Labels that mean the performance is not a normal ticketed sale.
FREE_LABELS = ("gratis", "reserveer gratis ticket", "reserveer gratis tickets")
CANCELLED_LABELS = ("geannuleerd",)


class MgeAdapter(VenueAdapter):
    venue_id = "mge"
    name = "Muziekgebouw Eindhoven"
    city = "Eindhoven"
    travel_minutes = 25

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        resp = self.fetcher.get(FEED, headers={"Accept": "application/json"})
        if resp is None:
            return []
        try:
            payload = resp.json()
        except ValueError:
            log.warning("mge: feed was not JSON")
            return []

        events = payload.get("events")
        if not isinstance(events, dict):
            log.warning("mge: feed had no events object")
            return []

        out: list[tuple[Show, Snapshot]] = []
        seen: set[str] = set()
        for slug, event in events.items():
            for parsed in self._performances(slug, event):
                show, _ = parsed
                if show.external_id in seen:
                    continue
                seen.add(show.external_id)
                out.append(parsed)

        log.info("mge: %s performances from one request", len(out))
        return out

    def _performances(self, slug: str, event: dict):
        # The feed ships HTML-escaped text -- 63 of 224 titles carry entities
        # like &amp; and &#8217;. Scraped venues get this decoding free from
        # BeautifulSoup; a JSON feed does not, so it happens here.
        title = _text(event.get("title"))
        if not title:
            return

        subtitle = _text(event.get("subtitle"))
        url = event.get("permalink") or f"https://mge.nl/agenda/{slug}/"
        tags = sorted(
            {
                str(v)
                for group in ("main_genre", "tags")
                for v in (event.get(group) or {}).values()
                if v
            }
        )

        for time in event.get("times") or []:
            starts_at = _parse_stamp(time.get("program_start"))
            if starts_at is None:
                continue

            # Free events and third-party sales carry `event_id: false` -- 40
            # of 264 performances. They are still real concerts in the
            # building, so they get a slug+time key rather than being dropped.
            event_id = time.get("event_id")
            external_id = (
                str(event_id) if event_id else f"{slug}-{starts_at:%Y%m%d%H%M}"
            )

            label = (time.get("status_label") or "").strip().lower()
            state = STATUS_MAP.get(label, UNKNOWN)
            # A cancelled concert is not a ticket you failed to get.
            if label in CANCELLED_LABELS:
                state = UNKNOWN

            lo, hi = _prices(time.get("price"))
            if lo is None and label in FREE_LABELS:
                lo = hi = 0

            yield (
                Show(
                    venue_id=self.venue_id,
                    external_id=external_id,
                    title=title,
                    subtitle=subtitle,
                    starts_at=starts_at,
                    url=url,
                    hall=_text(time.get("location")),
                    tags=tags,
                    ticket_url=time.get("ticket_link") or None,
                    ticket_ref=str(event_id) if event_id else None,
                ),
                Snapshot(
                    state=state,
                    price_min_cents=lo,
                    price_max_cents=hi,
                    note=label or None,
                ),
            )


def _text(value) -> str | None:
    """Decode HTML entities, then collapse whitespace."""
    if value is None:
        return None
    return clean(html_mod.unescape(str(value)))


def _parse_stamp(value) -> datetime | None:
    """'202609021930' -> datetime. The feed uses no separators at all."""
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y%m%d%H%M", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    log.debug("mge: unparsable timestamp %r", value)
    return None


def _prices(block) -> tuple[int | None, int | None]:
    """Rank prices, skipping any the venue marks not visible."""
    if not isinstance(block, dict):
        return None, None
    cents = []
    for entry in block.values():
        if not isinstance(entry, dict) or not entry.get("visible"):
            continue
        value = euros_to_cents(entry.get("price"))
        if value:
            cents.append(value)
    if not cents:
        return None, None
    return min(cents), max(cents)
