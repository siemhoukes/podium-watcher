"""Venue adapter contract plus the Dutch date parsing every adapter needs."""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime
from typing import Callable
from urllib.parse import urldefrag, urljoin

from .. import clock
from ..http import Fetcher
from ..models import Show, Snapshot

DUTCH_MONTHS = {
    "jan": 1, "januari": 1,
    "feb": 2, "februari": 2,
    "mrt": 3, "maa": 3, "maart": 3,
    "apr": 4, "april": 4,
    "mei": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "augustus": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_DATE_RE = re.compile(
    r"(?:(?P<dow>ma|di|wo|do|vr|za|zo)\w*\s+)?"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<month>[a-z]{3,9})\.?"
    # Peppered writes the year two ways: "2026" and "’26" with a curly
    # apostrophe. Missing the short form silently threw dates a whole year:
    # "wo 2 sep ’27" fell through to inference and came back as 2026.
    #
    # The short form *requires* the apostrophe. Without it, "ma 7 sep 12:00"
    # reads its own start time as a two-digit year and lands in 2012 -- a date
    # far in the past, so the performance drops out of every view silently.
    r"(?:\s*(?:(?P<year>\d{4})|[’'‘`´]\s*(?P<short_year>\d{2}))\b)?",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(?P<h>\d{1,2})[:.](?P<m>\d{2})")


def parse_dutch_datetime(
    date_text: str, time_text: str | None = None, today: datetime | None = None
) -> datetime | None:
    """Parse 'zo 13 sep' / 'zo 13 sep 2026' plus an optional '20:15'.

    Agenda listings usually omit the year, so an undated month that has
    already passed is read as next year. Without that, every January show
    scraped in December lands eight months in the past and silently drops
    out of the calendar.
    """
    if not date_text:
        return None
    m = _DATE_RE.search(date_text.strip().lower())
    if not m:
        return None
    month = DUTCH_MONTHS.get(m.group("month"))
    if not month:
        return None
    day = int(m.group("day"))
    now = today or clock.now()

    if m.group("year") or m.group("short_year"):
        year = (
            int(m.group("year"))
            if m.group("year")
            else 2000 + int(m.group("short_year"))
        )
    else:
        # Agendas only list upcoming shows, so a date already past this year
        # must mean next year.
        year = now.year
        if (month, day) < (now.month, now.day):
            year += 1

    hour = minute = 0
    if time_text:
        tm = _TIME_RE.search(time_text)
        if tm:
            hour, minute = int(tm.group("h")), int(tm.group("m"))
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def clean(text: str | None) -> str | None:
    """Collapse the whitespace that templated HTML leaves everywhere."""
    if text is None:
        return None
    out = re.sub(r"\s+", " ", text).strip()
    return out or None


def euros_to_cents(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d+)(?:[.,](\d{1,2}))?", text.replace("€", " "))
    if not m:
        return None
    cents = int(m.group(1)) * 100
    if m.group(2):
        cents += int(m.group(2).ljust(2, "0"))
    return cents


class FetchError(RuntimeError):
    """The listing could not be read at all; the poller records it as failed."""


_NEXT_LINK = re.compile(r"<a\b[^>]*\brel=[\"']next[\"'][^>]*>", re.I)
_HREF = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.I)


def next_link(html: str, current_url: str) -> str | None:
    """The page's own `rel="next"` link, as an absolute URL without fragment.

    Following the site's link instead of building `?page=N` ourselves is the
    whole point. Peppered renamed its parameter to `p54_page` in late
    September; every page after the first then quietly came back as page one,
    and Chasse, Parktheater, de Doelen and Theater Rotterdam lost 85-95% of
    their programme for three weeks without a single error.
    """
    tag = _NEXT_LINK.search(html)
    if not tag:
        return None
    href = _HREF.search(tag.group(0))
    if not href:
        return None
    url = urljoin(current_url, htmllib.unescape(href.group(1)).strip())
    return urldefrag(url)[0]


class VenueAdapter:
    """One per venue. `fetch` returns (Show, Snapshot) pairs."""

    venue_id: str = ""
    name: str = ""
    city: str = ""
    travel_minutes: int = 0

    # How many per-show detail fetches this adapter may spend in one run.
    # Listing pages are one request for hundreds of shows; per-show detail is
    # one request each, so it is rationed and spent on the soonest shows,
    # which is where accuracy actually matters.
    enrich_budget: int = 0

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        # Things that went wrong without stopping the run -- a page that
        # would not load halfway through, say. The poller records the run as
        # incomplete instead of as a quiet success.
        self.problems: list[str] = []
        self.pages_read = 0

    def paginate(
        self,
        first_url: str,
        parse: Callable[[str], list[tuple[Show, Snapshot]]],
        max_pages: int,
    ) -> list[tuple[Show, Snapshot]]:
        """Walk a listing by following each page's own "next" link.

        Strict on purpose, because every way this used to fail was silent:

        - page one unreadable, or holding no shows: raise -- that is a broken
          scraper or a waiting room, not a venue with nothing on;
        - a later page unreadable: keep what was read, note the gap;
        - a page that only repeats shows already seen: the site ignored the
          page address, which is exactly how the Peppered breakage looked;
        - still a "next" link after `max_pages`: say the listing was cut off.
        """
        results: list[tuple[Show, Snapshot]] = []
        seen: set[str] = set()
        visited: set[str] = set()
        url: str | None = first_url
        self.pages_read = 0

        for page in range(1, max_pages + 1):
            html = self.fetcher.get_text(url)
            if html is None:
                why = self.fetcher.last_problem or "onbekende fout"
                if page == 1:
                    raise FetchError(f"Agenda niet opgehaald: {why}")
                self.problems.append(
                    f"Pagina {page} niet opgehaald: {why}; "
                    f"{len(results)} voorstellingen gelezen"
                )
                break
            self.pages_read = page
            visited.add(url)

            found = parse(html)
            if not found and page == 1:
                raise FetchError(
                    "Geen voorstellingen op de eerste agendapagina; "
                    "is de opmaak veranderd?"
                )

            added = 0
            for show, snap in found:
                # Checked inside the loop: a repeated id within one page must
                # not land two snapshots on one show in a single poll.
                if show.external_id in seen:
                    continue
                seen.add(show.external_id)
                results.append((show, snap))
                added += 1

            nxt = next_link(html, url)
            if found and not added:
                self.problems.append(
                    f"Pagina {page} herhaalde alleen eerdere voorstellingen; "
                    "de site negeert het paginanummer"
                )
                break
            if not nxt or nxt in visited:
                break
            url = nxt
        else:
            self.problems.append(
                f"Na {max_pages} pagina's afgekapt; er waren er meer"
            )
        return results

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        raise NotImplementedError

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        """Add detail the listing page cannot give (seats, real prices).

        Return True if a network request was spent, so the poller can hold to
        its budget. Default: nothing to add.
        """
        return False
