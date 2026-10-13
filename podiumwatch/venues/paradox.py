"""Paradox, Tilburg -- jazz, 175 seats, a five-minute walk.

No youth deal here, so nothing is discounted and the fare shown is simply the
door price. It is tracked because it is walkable and small enough that a good
night genuinely sells out, which is the same question the rest of the site
answers.

Runs on WP Theatre, so the listing carries schema.org microdata: a machine
date, a price with a `content` attribute, genres, and a Stager ticket link.

One trap: the `content` attribute on `.event-date` disagrees with the visible
programme. A concert whose page says "Aanvang 16:00" carries
`content="2026-09-10 11:00"`. The date half is right and the time half is not,
so the date comes from the attribute and the time from the "Aanvang HH:MM"
line, falling back to the attribute only when that is missing.

Paradox publishes about three weeks ahead. A short list is the venue being
small, not the scraper failing.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import AVAILABLE, SOLD_OUT, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents

log = logging.getLogger(__name__)

BASE = "https://www.paradoxtilburg.nl"
AGENDA = BASE + "/agenda/"

_AANVANG_RE = re.compile(r"aanvang\s*(\d{1,2})[:.](\d{2})", re.IGNORECASE)
_STAGER_RE = re.compile(r"/events/(\d+)")


def show_key(url: str, starts_at) -> str:
    """Production slug + start time.

    Not the Stager event id behind the ticket button: that button disappears
    when a show sells out, so the same performance came back as a new row at
    exactly the moment its history mattered most -- the Peppered mistake
    again. Soft Machine on 16 October was the first to split in two.
    """
    return f"{url.rstrip('/').rsplit('/', 1)[-1]}-{starts_at:%Y%m%d%H%M}"


class ParadoxAdapter(VenueAdapter):
    venue_id = "paradox"
    name = "Paradox"
    city = "Tilburg"
    travel_minutes = 5  # on foot

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        html = self.fetcher.get_text(AGENDA)
        if not html:
            return []
        results, seen = [], set()
        for card in BeautifulSoup(html, "html.parser").select(".wp_theatre_event"):
            parsed = self._parse_card(card)
            if not parsed:
                continue
            show, _ = parsed
            if show.external_id in seen:
                continue
            seen.add(show.external_id)
            results.append(parsed)
        log.info("paradox: %s shows", len(results))
        return results

    def _parse_card(self, card) -> tuple[Show, Snapshot] | None:
        link = card.select_one(".event-title a")
        if link is None:
            return None

        # The title element also holds the subtitle; pull it out first so the
        # title does not swallow it.
        small = link.select_one(".titleSmallText")
        subtitle = clean(small.get_text()) if small else None
        if small:
            small.extract()
        title = clean(link.get_text())
        if not title:
            return None

        url = link.get("href") or AGENDA
        starts_at = self._when(card)
        if starts_at is None:
            return None

        ticket = card.select_one("a.ticket-button")
        ticket_url = ticket.get("href") if ticket else None
        ref = None
        if ticket_url:
            m = _STAGER_RE.search(ticket_url)
            if m:
                ref = m.group(1)

        price_el = card.select_one(".event-price")
        price = None
        if price_el is not None:
            # `content="28"` is plain euros; the visible text is "€28,00".
            price = euros_to_cents(price_el.get("content")) or euros_to_cents(
                price_el.get_text()
            )

        genres_el = card.select_one(".event-genres")
        tags = []
        if genres_el:
            tags = [
                t.strip()
                for t in re.split(r"[|,]", clean(genres_el.get_text()) or "")
                if t.strip()
            ]

        sold_out = card.select_one(".soldout") is not None

        return (
            Show(
                venue_id=self.venue_id,
                external_id=show_key(url, starts_at),
                title=title,
                subtitle=subtitle,
                starts_at=starts_at,
                url=url,
                tags=tags,
                ticket_url=ticket_url,
                ticket_ref=ref,
            ),
            Snapshot(
                state=SOLD_OUT if sold_out else AVAILABLE,
                price_min_cents=price,
                price_max_cents=price,
            ),
        )

    @staticmethod
    def _when(card) -> datetime | None:
        date_el = card.select_one(".event-date[content]")
        if date_el is None:
            return None
        raw = (date_el.get("content") or "").strip()
        try:
            stamp = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                stamp = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                return None

        meta = card.select_one(".event-meta")
        if meta is not None:
            m = _AANVANG_RE.search(meta.get_text(" ", strip=True))
            if m:
                return stamp.replace(hour=int(m.group(1)), minute=int(m.group(2)))
        return stamp
