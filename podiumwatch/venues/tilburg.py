"""Schouwburg & Concertzaal Tilburg.

Deal: EUR 13,50 including a drink, for anyone up to 26 *or* any student
(no age ceiling on the student route), from 48h before a non-sold-out show.

The agenda is server-rendered and paginated. Each tile carries a stable
`data-detail-link` id, a date, a time, and a ticket button that is either a
live Ticketmatic link or a disabled "Uitverkocht" stub -- which is the
availability signal.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from . import ticketmatic
from .base import VenueAdapter, clean, parse_dutch_datetime

log = logging.getLogger(__name__)

BASE = "https://www.schouwburgconcertzaaltilburg.nl"
AGENDA = BASE + "/nl/agenda"
MAX_PAGES = 25

# Wording the site uses on a ticket button when stock is tight rather than gone.
LIMITED_HINTS = ("laatste kaarten", "bijna uitverkocht", "beperkt")
SOLD_OUT_HINTS = ("uitverkocht", "volgeboekt", "wachtlijst")


class TilburgAdapter(VenueAdapter):
    venue_id = "tilburg"
    name = "Schouwburg & Concertzaal Tilburg"
    city = "Tilburg"
    travel_minutes = 5  # on foot

    # Tilburg publishes neither price nor seat count on its own site, so
    # every useful number costs one or two Ticketmatic requests. Worth it:
    # this is the only venue that yields an exact fill rate.
    enrich_budget = 60

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        """Real prices and, for seated halls, an exact seat count.

        Unlike prices, seat counts must be re-read every run -- the whole
        point is watching them move -- so nothing here is cached.
        """
        if not show.ticket_url or "ticketmatic.com" not in show.ticket_url:
            return False
        return ticketmatic.enrich_from_widget(self.fetcher, show.ticket_url, snap)

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        results: list[tuple[Show, Snapshot]] = []
        seen: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            url = AGENDA if page == 1 else f"{AGENDA}?page={page}"
            html = self.fetcher.get_text(url)
            if not html:
                break

            found = self._parse_page(html)
            if not found:
                break

            added = 0
            for show, snap in found:
                # Checked inside the loop so a repeated id within one page is
                # dropped too, not just one that appeared on an earlier page.
                if show.external_id in seen:
                    continue
                seen.add(show.external_id)
                results.append((show, snap))
                added += 1
            if not added:
                # Pagination looping back on itself; stop rather than spin.
                break

        log.info("tilburg: %s shows across %s pages", len(results), page)
        return results

    def _parse_page(self, html: str) -> list[tuple[Show, Snapshot]]:
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for tile in soup.select("div.event"):
            parsed = self._parse_tile(tile)
            if parsed:
                out.append(parsed)
        return out

    def _parse_tile(self, tile) -> tuple[Show, Snapshot] | None:
        link = tile.select_one("h3.title a[data-detail-link]")
        if not link:
            return None  # non-event tile (restaurant promo, etc.)

        external_id = link.get("data-detail-link")
        title = clean(link.get_text())
        if not external_id or not title:
            return None

        href = link.get("href") or ""
        url = href if href.startswith("http") else BASE + href

        date_el = tile.select_one("p.date")
        time_el = tile.select_one("p.time")
        starts_at = parse_dutch_datetime(
            clean(date_el.get_text()) if date_el else "",
            clean(time_el.get_text()) if time_el else None,
        )

        tags = [
            t for t in (clean(x.get_text()) for x in tile.select("span.tag")) if t
        ]
        subtitle = clean(
            tile.select_one("p.subtitle").get_text()
        ) if tile.select_one("p.subtitle") else None

        snap, ticket_url, ticket_ref = self._availability(tile)

        return (
            Show(
                venue_id=self.venue_id,
                external_id=str(external_id),
                title=title,
                subtitle=subtitle,
                starts_at=starts_at,
                url=url,
                tags=tags,
                ticket_url=ticket_url,
                ticket_ref=ticket_ref,
            ),
            snap,
        )

    def _availability(self, tile) -> tuple[Snapshot, str | None, str | None]:
        btn = tile.select_one("a.btn-ticket")
        if btn is None:
            return Snapshot(state=UNKNOWN, note="no ticket button"), None, None

        label = (clean(btn.get_text()) or "").lower()
        classes = btn.get("class") or []
        href = btn.get("href") or ""

        if any(h in label for h in SOLD_OUT_HINTS):
            state = SOLD_OUT
        elif any(h in label for h in LIMITED_HINTS):
            state = LIMITED
        elif "btn-disabled" in classes or not href:
            # Disabled without a recognised label: not buyable, but we do not
            # know why (sale not open yet, external sale, cancelled).
            state = UNKNOWN
        else:
            state = AVAILABLE

        ticket_url = href or None
        ticket_ref = None
        if href:
            m = re.search(r"[?&]event=(\d+)", href)
            if m:
                ticket_ref = m.group(1)

        return Snapshot(state=state, note=label or None), ticket_url, ticket_ref
