"""013, Tilburg -- the big pop venue, also a five-minute walk.

No youth deal, so nothing is discounted here either; it is tracked because it
is walkable and things sell out. Mostly outside the listed taste, which is why
its own genre tags matter for filtering it back down.

The markup is Tailwind, so there is not a single semantic class name to hang a
parser on. What it does have is better: a real `<time datetime>` per event and
a small "ribbon" vocabulary for status. The event id comes from the URL.

Ribbons mix two unrelated things -- status (`Uitverkocht`, `Laatste kaarten`,
`Afgelast`) and marketing (`Enige NL show`, `013 op locatie`). Only the first
kind is read; the rest is ignored rather than mistaken for availability.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents

log = logging.getLogger(__name__)

BASE = "https://www.013.nl"
PROGRAMMA = BASE + "/programma"

_ID_RE = re.compile(r"/programma/(\d+)/")

# Only ribbons that say something about getting in.
RIBBON_STATES = {
    "uitverkocht": SOLD_OUT,
    "laatste kaarten": LIMITED,
    "bijna uitverkocht": LIMITED,
    "afgelast": UNKNOWN,
    "geannuleerd": UNKNOWN,
    "verplaatst": UNKNOWN,
    "nieuwe datum": AVAILABLE,
}


class Poppodium013Adapter(VenueAdapter):
    venue_id = "013"
    name = "013"
    city = "Tilburg"
    travel_minutes = 5  # on foot

    # The listing carries no price at all, but detail pages do and prices do
    # not change, so each show is fetched once and never again.
    enrich_budget = 30

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        if snap.price_min_cents is not None:
            return False
        if cached_price is not None:
            snap.price_min_cents, snap.price_max_cents = cached_price
            return False
        price = self.fetch_detail_price(show)
        if price is not None:
            snap.price_min_cents = snap.price_max_cents = price
        return True

    def fetch_detail_price(self, show: Show) -> int | None:
        """The 'Entree' figure, which already includes service costs."""
        html = self.fetcher.get_text(show.url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one(".price_table")
        if table is None:
            return None
        amounts = [
            cents
            for cents in (euros_to_cents(b.get_text()) for b in table.select("b"))
            if cents
        ]
        return min(amounts) if amounts else None

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        html = self.fetcher.get_text(PROGRAMMA)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select('a[class*="group/item"]')
        if not items:
            log.warning(
                "013: no event items matched; the Tailwind markup may have changed"
            )
            return []

        results, seen = [], set()
        for item in items:
            parsed = self._parse_item(item)
            if not parsed:
                continue
            show, _ = parsed
            if show.external_id in seen:
                continue
            seen.add(show.external_id)
            results.append(parsed)

        log.info("013: %s shows", len(results))
        return results

    def _parse_item(self, item) -> tuple[Show, Snapshot] | None:
        href = item.get("href") or ""
        m = _ID_RE.search(href)
        if not m:
            return None
        event_id = m.group(1)

        heading = item.select_one("h2")
        title = clean(heading.get_text()) if heading else None
        if not title:
            return None

        paragraphs = [clean(p.get_text(" ")) for p in item.select("p")]
        paragraphs = [p for p in paragraphs if p]
        subtitle = paragraphs[0] if paragraphs else None

        time_el = item.select_one("[datetime]")
        starts_at = _parse_iso(time_el.get("datetime")) if time_el else None
        if starts_at is None:
            return None

        state, note = AVAILABLE, None
        for ribbon in item.select('[class*="ribbon"]'):
            label = (clean(ribbon.get_text()) or "").lower().rstrip("!")
            if label in RIBBON_STATES:
                state, note = RIBBON_STATES[label], label
                break

        return (
            Show(
                venue_id=self.venue_id,
                external_id=event_id,
                title=title,
                subtitle=subtitle,
                starts_at=starts_at,
                url=href,
                ticket_url=href,
                ticket_ref=event_id,
            ),
            Snapshot(state=state, note=note),
        )


def _parse_iso(value: str | None) -> datetime | None:
    """'2026-09-03T20:00:00+02:00' -> naive local datetime.

    Everything else in the database is naive local time, so the offset is
    dropped rather than mixing aware and naive values in one column.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        log.debug("013: unparsable datetime %r", value)
        return None
