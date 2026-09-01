"""TivoliVredenburg, Utrecht.

Deal: **"Onder 30 jaar"**, and it is the steepest on the whole board. Its own
concert pages put it plainly:

    Rang 1  Regulier € 46,-    Onder 30 jaar € 10,-

Maximum six under-30 tickets per order, and a limited number per concert, so
it can run out well before the hall does.

Forty minutes from Tilburg CS. Worth the trip for a €10 seat at a €46 concert.

Its listing is the thinnest of any venue here: an id, a title, a subtitle and
a *date with no clock time*. Everything that matters -- the start time, the
prices, the under-30 rate -- lives on the concert page.

That shapes the adapter. A show is yielded with `starts_at=None` until its
detail page has been read, because storing it at midnight would be the same
mistake that put 26 Parktheater rows at 00:00: a wrong time throws the
discount-window countdown out by most of a day. Unresolved shows sit in the
database without a date and stay out of the dashboard until they are real.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents

log = logging.getLogger(__name__)

BASE = "https://www.tivolivredenburg.nl"
AGENDA = BASE + "/agenda/"
MAX_PAGES = 30

_ID_RE = re.compile(r"/agenda/(\d+)/")
_START_RE = re.compile(r'"startDate"\s*:\s*"([^"]+)"')

SOLD_OUT_HINTS = ("uitverkocht", "sold out")
LIMITED_HINTS = ("laatste kaarten", "bijna uitverkocht", "bijna vol")

# Price rows are labelled; only these are open to a walk-up buyer under 30.
YOUTH_LABELS = ("onder 30", "onder de 30", "t/m 29", "jongeren")


class TivoliAdapter(VenueAdapter):
    venue_id = "tivoli"
    name = "TivoliVredenburg"
    city = "Utrecht"
    travel_minutes = 40

    # Every show needs one detail page before it can be shown at all, so the
    # programme fills in over a few runs rather than all at once.
    enrich_budget = 40

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        results: list[tuple[Show, Snapshot]] = []
        seen: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            url = AGENDA if page == 1 else f"{AGENDA}page/{page}/"
            html = self.fetcher.get_text(url)
            if not html:
                break

            added = 0
            for show, snap in self._parse_page(html):
                if show.external_id in seen:
                    continue
                seen.add(show.external_id)
                results.append((show, snap))
                added += 1
            if not added:
                break

        log.info("tivoli: %s shows", len(results))
        return results

    def _parse_page(self, html: str) -> list[tuple[Show, Snapshot]]:
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for item in soup.select("li.agenda-list-item"):
            link = item.select_one("a.agenda-list-item__title-link")
            if link is None:
                continue
            href = link.get("href") or ""
            m = _ID_RE.search(href)
            title = clean(link.get_text())
            if not m or not title:
                continue

            text_el = item.select_one(".agenda-list-item__text")
            out.append((
                Show(
                    venue_id=self.venue_id,
                    external_id=m.group(1),
                    title=title,
                    subtitle=clean(text_el.get_text()) if text_el else None,
                    # Deliberately unset: the listing has a date but no clock
                    # time, and a midnight guess would be worse than nothing.
                    starts_at=None,
                    url=href,
                    ticket_url=href,
                    ticket_ref=m.group(1),
                ),
                Snapshot(state=AVAILABLE),
            ))
        return out

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        """Read the concert page for the time, the prices and the status."""
        html = self.fetcher.get_text(show.url)
        if not html:
            return False

        starts_at = _start_time(html)
        if starts_at is not None:
            show.starts_at = starts_at

        soup = BeautifulSoup(html, "html.parser")
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

        normal, youth = _prices(text)
        if normal is not None:
            snap.price_min_cents = snap.price_max_cents = normal
        if youth is not None:
            snap.price_student_cents = youth

        lowered = text.lower()
        if any(h in lowered for h in SOLD_OUT_HINTS):
            snap.state = SOLD_OUT
        elif any(h in lowered for h in LIMITED_HINTS):
            snap.state = LIMITED
        return True


def _start_time(html: str) -> datetime | None:
    """The concert page carries a real ISO start in its structured data."""
    for raw in _START_RE.findall(html):
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _prices(text: str) -> tuple[int | None, int | None]:
    """-> (regular, under-30). Both from the labelled price table.

    'Rang 1 Regulier € 46,- Onder 30 jaar € 10,-' -- the labels are what make
    the two distinguishable, exactly as at Chasse.
    """
    pairs = re.findall(r"([A-Za-zÀ-ÿ0-9 /]{3,28}?)\s*€\s*([\d.,]+)", text)
    regular, youth = [], []
    for label, amount in pairs:
        cents = euros_to_cents(amount)
        if not cents:
            continue
        low = label.strip().lower()
        if any(y in low for y in YOUTH_LABELS):
            youth.append(cents)
        elif "regulier" in low or "rang" in low:
            regular.append(cents)
    return (min(regular) if regular else None, min(youth) if youth else None)
