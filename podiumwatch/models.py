"""What a venue adapter hands back to the poller.

The important design decision lives here: a venue reports availability at
whatever depth its website actually allows, and never pretends to more.
Ticketmatic can tell us "37 stoelen beschikbaar"; Chasse can only tell us
"not sold out". Both are useful, and inventing a number for the second kind
would poison the forecast, so `seats_free` stays None rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Coarse availability, ordered worst-to-best. Every venue can produce these.
SOLD_OUT = "sold_out"
LIMITED = "limited"  # "laatste kaarten", waitlist offered, etc.
AVAILABLE = "available"
UNKNOWN = "unknown"

STATES = (SOLD_OUT, LIMITED, AVAILABLE, UNKNOWN)


@dataclass
class Show:
    venue_id: str
    external_id: str  # stable id on the venue's own site
    title: str
    starts_at: datetime | None
    url: str
    subtitle: str | None = None
    tags: list[str] = field(default_factory=list)
    hall: str | None = None
    ticket_url: str | None = None
    # Id in the ticketing platform (Ticketmatic/Tixly), when we can see it.
    ticket_ref: str | None = None


@dataclass
class Snapshot:
    state: str = UNKNOWN
    seats_free: int | None = None
    seats_total: int | None = None
    price_min_cents: int | None = None
    price_max_cents: int | None = None
    note: str | None = None

    @property
    def fraction_sold(self) -> float | None:
        """Only meaningful when the venue gave us real numbers."""
        if self.seats_free is None or not self.seats_total:
            return None
        return 1.0 - (self.seats_free / self.seats_total)
