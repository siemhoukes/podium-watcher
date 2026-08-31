"""Shared adapter for venues running on the Peppered ticketing platform.

Chasse (Breda) and Parktheater (Eindhoven) both use it, and their markup is
identical enough that one parser handles both unchanged -- only the base URL
and the venue metadata differ.

Peppered is by far the most generous source we have. Every performance anchor
carries machine-readable attributes, so nothing has to be guessed:

    data-event-id          stable per-performance id
    data-event-start       "2026-08-31 17:00:00"
    data-event-hall        which room, which matters for capacity later
    data-production-type   movie / default
    class="... status-*"   three-level availability, not just sold-out y/n

A production holds many performances; we store one row per *performance*,
because that is the thing that sells out and the thing you can attend.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import AVAILABLE, LIMITED, SOLD_OUT, UNKNOWN, Show, Snapshot
from .base import VenueAdapter, clean, euros_to_cents

log = logging.getLogger(__name__)

# Peppered's per-performance sale states.
STATUS_MAP = {
    "normaal": AVAILABLE,
    "laatste_kaarten": LIMITED,
    "bijna_uitverkocht": LIMITED,
    "uitverkocht": SOLD_OUT,
    "wachtlijst": SOLD_OUT,  # a waiting list means the seats are gone
    "geannuleerd": UNKNOWN,
    "info": UNKNOWN,  # no direct sale, info page only
}


class PepperedAdapter(VenueAdapter):
    base_url: str = ""
    programme_path: str = "/programma"
    max_pages: int = 40

    @property
    def programme_url(self) -> str:
        return self.base_url + self.programme_path

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        results: list[tuple[Show, Snapshot]] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            url = (
                self.programme_url
                if page == 1
                else f"{self.programme_url}?page={page}"
            )
            html = self.fetcher.get_text(url)
            if not html:
                break

            added = 0
            for show, snap in self._parse_page(html):
                # Checked inside the loop: a repeated id within one page must
                # not slip through, or it lands two snapshots on one show in
                # a single poll.
                if show.external_id in seen:
                    continue
                seen.add(show.external_id)
                results.append((show, snap))
                added += 1
            if not added:
                break

        log.info("%s: %s performances", self.venue_id, len(results))
        return results

    def _parse_page(self, html: str) -> list[tuple[Show, Snapshot]]:
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for card in soup.select("li.eventCard"):
            # Price sits on the production; the dates hang underneath it.
            price_el = card.select_one(".price")
            price_min, price_max = self._prices(
                clean(price_el.get_text()) if price_el else None
            )
            for anchor in card.select("a[data-event-id]"):
                parsed = self._parse_performance(anchor, price_min, price_max)
                if parsed:
                    out.append(parsed)
        return out

    def _parse_performance(
        self, a, price_min: int | None, price_max: int | None
    ) -> tuple[Show, Snapshot] | None:
        event_id = a.get("data-event-id")
        title = clean(a.get("data-production-title"))
        if not event_id or not title:
            return None

        starts_at = None
        raw_start = a.get("data-event-start")
        if raw_start:
            try:
                starts_at = datetime.strptime(raw_start, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                log.debug("%s: unparsable start %r", self.venue_id, raw_start)

        prod_type = a.get("data-production-type") or "default"
        prod_id = a.get("data-production-id")
        url = (
            f"{self.programme_url}?production={prod_id}"
            if prod_id
            else self.programme_url
        )

        state = UNKNOWN
        for cls in a.get("class") or []:
            if cls.startswith("status-"):
                state = STATUS_MAP.get(cls[len("status-") :], UNKNOWN)
                break

        return (
            Show(
                venue_id=self.venue_id,
                external_id=str(event_id),
                title=title,
                subtitle=clean(a.get("data-production-subtitle")),
                starts_at=starts_at,
                url=url,
                hall=clean(a.get("data-event-hall")),
                tags=[prod_type],
                ticket_url=a.get("href"),
                ticket_ref=a.get("data-event-boxofficeid"),
            ),
            Snapshot(
                state=state,
                price_min_cents=price_min,
                price_max_cents=price_max,
            ),
        )

    @staticmethod
    def _prices(text: str | None) -> tuple[int | None, int | None]:
        """'€ 10,00 – € 13,00' -> (1000, 1300). Single price -> (p, p).

        Peppered renders a '€ 0,00' floor on productions covered by a pass.
        Nobody pays that, and letting it become the minimum would make every
        such show look like the best deal in the region, so zeros are dropped
        before taking the range.
        """
        if not text:
            return None, None
        nums = re.findall(r"\d+(?:[.,]\d{1,2})?", text)
        cents = [c for c in (euros_to_cents(n) for n in nums) if c]
        if not cents:
            return None, None
        return min(cents), max(cents)
