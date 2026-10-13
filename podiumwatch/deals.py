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

from . import clock


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
    "doelen": Deal(
        venue_id="doelen",
        label="€10 jongerentarief",
        # Not a last-minute deal at all: bookable from the moment tickets go
        # on sale, which is why the window is the whole run-up.
        window_hours=24 * 365,
        eligibility="t/m 30",
        flat_cents=1000,
        note="Beperkt aantal jongerenplaatsen; kan eerder op zijn dan de zaal.",
    ),
    "theaterrotterdam": Deal(
        venue_id="theaterrotterdam",
        label="€10 t/m 30 jaar",
        # No window at all: bookable the moment tickets go on sale.
        window_hours=24 * 365,
        # The only venue here that does not care what card you hold. Their
        # own words: "Of je nu student bent, CJP-er, of al afgestudeerd."
        eligibility="t/m 30, geen pas nodig",
        flat_cents=1000,
        note="Geldt op alle voorstellingen, ook ver vooruit te boeken.",
    ),
    "tivoli": Deal(
        venue_id="tivoli",
        label="€10 onder 30",
        # Bookable from the moment tickets go on sale, like De Doelen.
        window_hours=24 * 365,
        eligibility="onder 30",
        # Deliberately no flat_cents. The rate is real -- "Rang 1 Regulier
        # € 46,- Onder 30 jaar € 10,-" -- but it is not on every concert:
        # none of six sampled shows carried the label. Stamping €10 on all
        # 600 rows would be the same mistake as quoting Verkadefabriek's flat
        # fare on free events. So the €10 is only ever shown when the concert
        # page actually published it.
        selective=True,
        note="Alleen bij geselecteerde concerten; max 6 per bestelling. Niet automatisch te volgen: hun site weert de poller.",
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
    # MgE's feed marks third-party concerts "Externe verkoop" and links them
    # to feverup.com and the like -- in the building, but not their sale.
    "mge": ("tickets.mge.nl",),
    # A third of De Doelen's programme is sold by the Rotterdams
    # Philharmonisch or Ticketmaster; the youth rate cannot reach those.
    "doelen": ("/order/add/",),
    "theaterrotterdam": ("/order/add/",),
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
    venue_id: str,
    price_min_cents: int | None,
    published_student_cents: int | None = None,
) -> int | None:
    """The cheapest fare you could actually get, or None if unknowable.

    Two real options can exist at once and neither dominates:

    - the venue's **last-minute rule** (a flat fare, or a percentage), and
    - a **student/CJP category the venue publishes** in its price block.

    Neither can simply win. Chasse's 'scholier/student/CJP € 10,00' beside
    'normaal € 14,50' is a genuine student fare, so applying the 50% rule to
    the €10 would bill a €14,50 seat as €5. But Parktheater's CJP category is
    only €2,50 off — €46,00 against a €48,50 normal price — which is their
    advance discount, not their €15 last-minute fare. Treating that as *the*
    student price quoted €46 for a show you can walk into for €15.

    So the honest answer is whichever is cheaper: both are published, both are
    real, and you would take the better one.

    Percentage deals need a base price; without one we return None rather than
    inventing a figure, so the UI can say "unknown" instead of a confident
    wrong number.
    """
    rule = _rule_price_cents(venue_id, price_min_cents)
    candidates = [c for c in (rule, published_student_cents) if c]
    fare = min(candidates) if candidates else None

    # A flat "discount" fare must never exceed what anyone pays at the door.
    # Verkadefabriek's EUR 10 student price was being quoted on genuinely free
    # events, and on anything cheaper than EUR 10 it would overcharge you on
    # paper and hide the show behind a price filter it should have passed.
    if price_min_cents is not None and (fare is None or fare > price_min_cents):
        fare = price_min_cents
    return fare


def deal_price_source(
    venue_id: str,
    price_min_cents: int | None,
    published_student_cents: int | None = None,
) -> str | None:
    """'rule' or 'published' -- which option produced the quoted fare."""
    rule = _rule_price_cents(venue_id, price_min_cents)
    if published_student_cents and (rule is None or published_student_cents < rule):
        return "published"
    return "rule" if rule else None


def _rule_price_cents(venue_id: str, price_min_cents: int | None) -> int | None:
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
    now = now or clock.now()
    opens = window_opens_at(venue_id, starts_at)
    if opens is None or starts_at is None:
        return "unknown", None
    if now >= starts_at:
        return "closed", None
    if now >= opens:
        return "open", timedelta(0)
    return "soon", opens - now
