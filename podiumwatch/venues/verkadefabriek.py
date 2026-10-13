"""Verkadefabriek, Den Bosch.

Deal: EUR 10 for cabaret, dance, theatre and concerts (film is EUR 8), for
students up to and including 29, online from 24h before. The age ceiling is
the most generous of the five venues, and the flat EUR 10 is the cheapest
fare -- so this is usually the best value on the board when something good
is on.

Only the podium programme is scraped, which is exactly the EUR 10 category.
The cinema programme is deliberately left out: it is dozens of daily
screenings at a different price, and it would swamp the calendar.

`/podium` itself used to be that listing. Since early October 2026 it is a
landing page showing six shows, with a button to the real listing at
`/agenda?genre=...`. Paging through the landing page skipped most of the
programme -- page one ended on the 15th, page two began on the 20th -- so
about half of all performances silently dropped out. The listing URL is now
read from that button, so a genre they add is picked up too.

Availability here is the weakest signal of any venue. There is no status
attribute -- a show on sale simply has an *empty* `.ticket-status` div, and a
sold-out one has a badge inside it. See `_availability` for why that is
guarded rather than trusted.
"""

from __future__ import annotations

import html as htmllib
import logging
import re

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from . import ticketmatic
from .base import VenueAdapter, clean, euros_to_cents as _euro_cents, parse_dutch_datetime

log = logging.getLogger(__name__)

BASE = "https://www.verkadefabriek.nl"
PODIUM = BASE + "/podium"
# What the landing page linked to in October 2026; used only if that link
# cannot be found.
PODIUM_GENRES = (
    "Cabaret", "Club", "Cursus", "Dans", "Festival", "Flaneerprogramma",
    "Kinderen", "Masterclass", "Muziek", "Open-repetitie", "Talkshow",
    "theater", "Training", "Woord", "Workshop",
)
AGENDA_FALLBACK = BASE + "/agenda?" + "&".join(f"genre={g}" for g in PODIUM_GENRES)
MAX_PAGES = 40

_LISTING_LINK = re.compile(r'href="(/agenda\?genre=[^"]+)"')

SOLD_OUT_HINTS = ("uitverkocht", "volgeboekt", "wachtlijst")
LIMITED_HINTS = ("laatste kaarten", "bijna uitverkocht", "beperkt")

_ID_RE = re.compile(r"-(\d+)/?$")


class VerkadefabriekAdapter(VenueAdapter):
    venue_id = "verkadefabriek"
    name = "Verkadefabriek"
    city = "Den Bosch"
    travel_minutes = 25

    # One detail page per show, but prices never change, so a show is only
    # ever fetched once and the whole programme fills in over a few runs.
    enrich_budget = 40

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        """Verkadefabriek runs on Ticketmatic too.

        Its own /koop-ticket link 302s to the same signed widget Tilburg uses,
        so the identical reader gives graded availability, real prices and --
        for seated halls -- an exact seat count. Better than the detail-page
        price scrape, which is kept only as a fallback.
        """
        if show.ticket_url:
            if ticketmatic.enrich_from_widget(self.fetcher, show.ticket_url, snap):
                if snap.price_min_cents is not None:
                    return True
                # Widget gave no price; fall through to the detail page.
        if snap.price_min_cents is not None:
            return True
        if cached_price is not None:
            snap.price_min_cents, snap.price_max_cents = cached_price
            return bool(show.ticket_url)
        lo, hi = self.fetch_detail_price(show)
        if lo is not None:
            snap.price_min_cents, snap.price_max_cents = lo, hi
        return True

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        results = self.paginate(self._listing_url(), self._parse_page, MAX_PAGES)
        log.info(
            "verkadefabriek: %s shows across %s pages", len(results), self.pages_read
        )
        return results

    def _listing_url(self) -> str:
        """The full podium listing, as linked from the `/podium` landing page."""
        html = self.fetcher.get_text(PODIUM) or ""
        m = _LISTING_LINK.search(html)
        if not m:
            log.warning("verkadefabriek: no listing link on /podium, using fallback")
            return AGENDA_FALLBACK
        return BASE + htmllib.unescape(m.group(1)).strip()

    def _parse_page(self, html: str) -> list[tuple[Show, Snapshot]]:
        soup = BeautifulSoup(html, "html.parser")
        wrappers = soup.select(".article-wrapper")

        # An empty `.ticket-status` means "on sale". If the class disappeared
        # in a redesign, every show would silently read as available and the
        # forecast would quietly rot, so notice it out loud instead.
        if wrappers and not soup.select(".ticket-status"):
            log.warning(
                "verkadefabriek: %s events but no .ticket-status elements - "
                "markup may have changed; availability is unreliable",
                len(wrappers),
            )

        out = []
        for wrapper in wrappers:
            parsed = self._parse_wrapper(wrapper)
            if parsed:
                out.append(parsed)
        return out

    def _parse_wrapper(self, wrapper) -> tuple[Show, Snapshot] | None:
        anchor = wrapper.find_parent("a")
        href = anchor.get("href") if anchor else None
        if not href:
            return None

        m = _ID_RE.search(href)
        if not m:
            return None
        production_id = m.group(1)

        title_el = wrapper.select_one(".article-title")
        title = clean(title_el.get_text()) if title_el else None
        if not title:
            return None

        date_el = wrapper.select_one("span.date")
        date_text = clean(date_el.get_text()) if date_el else None
        # "zo 8 nov. - 11:00" carries both halves in one string.
        starts_at = parse_dutch_datetime(date_text or "", date_text)

        # The link only identifies the *production*, and a run of four nights
        # shares one id. Keying on production alone collapses them into a
        # single row that collects four snapshots per poll, which destroys the
        # fill curve. The performance is the unit we care about, so the start
        # time is part of its identity.
        if starts_at is None:
            return None  # undated listing cannot be tracked as a performance
        external_id = f"{production_id}-{starts_at:%Y%m%d%H%M}"

        type_el = wrapper.select_one(".article-type")
        tags = []
        if type_el:
            tags = [t.strip() for t in (clean(type_el.get_text()) or "").split(",") if t.strip()]

        return (
            Show(
                venue_id=self.venue_id,
                external_id=external_id,
                title=title,
                starts_at=starts_at,
                url=BASE + href if href.startswith("/") else href,
                tags=tags,
                # Redirects to the signed Ticketmatic widget for this event.
                ticket_url=f"{BASE}/agenda/koop-ticket?programId={production_id}",
                ticket_ref=production_id,
            ),
            self._availability(wrapper),
        )

    # The listing carries no price at all; the detail page does, labelled by
    # category ("€ 26,00 Normaal", "€ 24,00 Supporter"). Fetching one page per
    # show is only affordable because prices do not change, so the poller asks
    # for this once per show and never again.
    def fetch_detail_price(self, show: Show) -> tuple[int | None, int | None]:
        html = self.fetcher.get_text(show.url)
        if not html:
            return None, None
        soup = BeautifulSoup(html, "html.parser")
        value = soup.select_one(".price .ticket-value")
        if value is None:
            return None, None

        text = value.get_text(" ", strip=True).replace("\xa0", " ")
        # "€ 26,00 Normaal € 24,00 Supporter" -> [(2600, 'normaal'), ...]
        pairs = re.findall(r"€\s*([\d.,]+)\s*([A-Za-zÀ-ÿ ]*)", text)
        priced = []
        for amount, label in pairs:
            cents = _euro_cents(amount)
            if cents:
                priced.append((cents, label.strip().lower()))
        if not priced:
            return None, None

        # "Normaal" is the standard adult fare and the honest thing to show
        # as the undiscounted price; fall back to the range otherwise.
        for cents, label in priced:
            if label.startswith("normaal"):
                return cents, max(c for c, _ in priced)
        return min(c for c, _ in priced), max(c for c, _ in priced)

    @staticmethod
    def _availability(wrapper) -> Snapshot:
        status = wrapper.select_one(".ticket-status")
        if status is None:
            return Snapshot(state=UNKNOWN, note="no ticket-status element")

        label = (clean(status.get_text()) or "").lower()
        if not label:
            # Empty badge area is this site's "normal, on sale" state.
            return Snapshot(state=AVAILABLE)
        if any(h in label for h in SOLD_OUT_HINTS):
            return Snapshot(state=SOLD_OUT, note=label)
        if any(h in label for h in LIMITED_HINTS):
            return Snapshot(state=LIMITED, note=label)
        return Snapshot(state=UNKNOWN, note=label)
