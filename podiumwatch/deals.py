"""The discount rules, and what you would actually pay.

Each venue prices its youth/student deal differently, and they are not
comparable by headline number. Chasse's "50% off" sounds like the best offer
here and frequently is the worst: half of a EUR 34-56 production is EUR 17-28,
against a flat EUR 10 at Verkadefabriek. Computing the real fare per show is
the point of this module.

Eligibility below assumes a student card, which is the backbone: only MgE's
deal is age-gated in a way that expires soon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Deal:
    venue_id: str
    label: str
    # Hours before curtain that the discount becomes buyable. `None` means
    # "from midnight on the day of the show" (Chasse).
    window_hours: int | None
    eligibility: str
    # Flat fare in cents, or None when the deal is a percentage.
    flat_cents: int | None = None
    percent_off: int | None = None
    # Parktheater charges more on expensive productions.
    premium_threshold_cents: int | None = None
    premium_cents: int | None = None
    includes_drink: bool = False
    # Not every show is eligible; MgE picks specific concerts.
    selective: bool = False
    note: str = ""


DEALS: dict[str, Deal] = {
    "tilburg": Deal(
        venue_id="tilburg",
        label="€13,50 incl. drankje",
        window_hours=48,
        eligibility="t/m 26 of student",
        flat_cents=1350,
        includes_drink=True,
        note="Alleen bij niet-uitverkochte voorstellingen.",
    ),
    "verkadefabriek": Deal(
        venue_id="verkadefabriek",
        label="€10 studentenkorting",
        window_hours=24,
        eligibility="student t/m 29",
        flat_cents=1000,
        note="Cabaret, dans, theater en concerten. Film is €8.",
    ),
    "parktheater": Deal(
        venue_id="parktheater",
        label="€15 incl. drankje",
        window_hours=24,
        eligibility="student / CJP",
        flat_cents=1500,
        premium_threshold_cents=5000,
        premium_cents=2500,
        includes_drink=True,
        note="€25 bij voorstellingen vanaf €50.",
    ),
    "chasse": Deal(
        venue_id="chasse",
        label="50% korting",
        window_hours=None,  # day of show only
        eligibility="student / CJP",
        percent_off=50,
        note="Alleen op de dag zelf, zolang er plaats is.",
    ),
    "mge": Deal(
        venue_id="mge",
        label="€10 Spotticket",
        window_hours=24,
        eligibility="onder 30",
        flat_cents=1000,
        selective=True,
        note="Alleen bij geselecteerde concerten.",
    ),
}


def deal_for(venue_id: str) -> Deal | None:
    return DEALS.get(venue_id)


# Fragments that mark a link as a genuine box-office channel for the venue.
# Only listed for venues whose adapter actually captures ticket links.
SALE_LINK_HINTS: dict[str, tuple[str, ...]] = {
    "tilburg": ("ticketmatic.com",),
    "chasse": ("/order/add/",),
    "parktheater": ("/order/add/",),
}


def deal_applies(venue_id: str, ticket_url: str | None) -> bool:
    """Whether the venue's own discount can plausibly apply to this event.

    Two different things masquerade as bookable performances:

    - Events *hosted at* the venue but sold by someone else — a pub quiz via
      quizfunevents.com, a touring programme via its own site. The venue's
      student fare cannot reach those.
    - Entries that are not ticketed sales at all — a bake sale in the venue's
      restaurant, an open day, a listing whose only link points back to its
      own page. Tilburg's terms exclude horeca outright.

    Both are caught by the same rule: it counts only if the link goes to a
    real box office. Getting this wrong does not make the site optimistic, it
    makes it wrong, so the check is strict rather than generous.

    Venues whose adapters capture no ticket link at all (Verkadefabriek) are
    exempt, since absence there carries no information.
    """
    hints = SALE_LINK_HINTS.get(venue_id)
    if hints is None:
        return True
    if not ticket_url:
        return False
    return any(h in ticket_url for h in hints)


def window_opens_at(venue_id: str, starts_at: datetime | None) -> datetime | None:
    """When the discount becomes buyable for this performance."""
    if starts_at is None:
        return None
    deal = DEALS.get(venue_id)
    if deal is None:
        return None
    if deal.window_hours is None:
        # Chasse: from the start of the day of the show.
        return starts_at.replace(hour=0, minute=0, second=0, microsecond=0)
    return starts_at - timedelta(hours=deal.window_hours)


def deal_price_cents(
    venue_id: str, price_min_cents: int | None
) -> int | None:
    """What you would actually pay, or None if it cannot be computed.

    Percentage deals need a base price; without one we return None rather
    than inventing a figure, so the UI can say "unknown" instead of showing
    a confident wrong number.
    """
    deal = DEALS.get(venue_id)
    if deal is None:
        return None

    if deal.percent_off is not None:
        if not price_min_cents:
            return None
        return round(price_min_cents * (100 - deal.percent_off) / 100)

    if (
        deal.premium_threshold_cents is not None
        and price_min_cents
        and price_min_cents >= deal.premium_threshold_cents
    ):
        return deal.premium_cents

    return deal.flat_cents


def window_state(
    venue_id: str, starts_at: datetime | None, now: datetime | None = None
) -> tuple[str, timedelta | None]:
    """('open' | 'soon' | 'closed' | 'unknown', time until it opens)."""
    now = now or datetime.now()
    opens = window_opens_at(venue_id, starts_at)
    if opens is None or starts_at is None:
        return "unknown", None
    if now >= starts_at:
        return "closed", None
    if now >= opens:
        return "open", timedelta(0)
    return "soon", opens - now
