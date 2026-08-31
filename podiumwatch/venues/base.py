"""Venue adapter contract plus the Dutch date parsing every adapter needs."""

from __future__ import annotations

import re
from datetime import datetime

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
    r"(?:\s*[’'‘`´]?\s*(?P<year>\d{4}|\d{2})\b)?",
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
    now = today or datetime.now()

    if m.group("year"):
        raw_year = m.group("year")
        year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
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

    def fetch(self) -> list[tuple[Show, Snapshot]]:
        raise NotImplementedError

    def enrich(self, show: Show, snap: Snapshot, cached_price=None) -> bool:
        """Add detail the listing page cannot give (seats, real prices).

        Return True if a network request was spent, so the poller can hold to
        its budget. Default: nothing to add.
        """
        return False
