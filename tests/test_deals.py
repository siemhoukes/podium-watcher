"""The pricing rules, and every way they have already been wrong.

Each of these started life as a bug on the live site. They are pinned here so
they cannot come back quietly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from podiumwatch import deals


# --------------------------------------------------------------------------
# What you actually pay
# --------------------------------------------------------------------------

def test_flat_fares():
    assert deals.deal_price_cents("tilburg", None) == 1350
    assert deals.deal_price_cents("verkadefabriek", 2600) == 1000


def test_chasse_percentage_needs_a_base_price():
    assert deals.deal_price_cents("chasse", 1950) == 975
    assert deals.deal_price_cents("chasse", None) is None, (
        "no base price means unknown, not a guess"
    )


def test_parktheater_premium_tier():
    assert deals.deal_price_cents("parktheater", 3000) == 1500
    assert deals.deal_price_cents("parktheater", 5500) == 2500


def test_a_published_student_fare_can_win():
    """Chasse lists 'scholier/student/CJP EUR 10,00' beside 'normaal EUR 40'.

    Half of EUR 40 is EUR 20, so the published fare is genuinely cheaper.
    """
    assert deals.deal_price_cents("chasse", 4000, 1000) == 1000
    assert deals.deal_price_source("chasse", 4000, 1000) == "published"


def test_a_published_cjp_fare_can_lose():
    """Parktheater's CJP category is their EUR 2,50 advance discount.

    EUR 46,00 against a EUR 48,50 normal price is not their EUR 15 last-minute
    fare. Trusting it quoted EUR 46 for a show you can walk into for EUR 15.
    """
    assert deals.deal_price_cents("parktheater", 4850, 4600) == 1500
    assert deals.deal_price_source("parktheater", 4850, 4600) == "rule"


def test_fare_never_exceeds_the_door_price():
    """A 'discount' dearer than the normal price is not a discount.

    Verkadefabriek's flat EUR 10 was being stamped on genuinely free events,
    which also hid them behind the price filter they should have passed.
    """
    assert deals.deal_price_cents("verkadefabriek", 0) == 0
    assert deals.deal_price_cents("verkadefabriek", 800) == 800
    assert deals.deal_price_cents("tilburg", 1000) == 1000


def test_venue_without_a_deal_falls_back_to_the_normal_price():
    """Paradox and 013 run no youth deal; you pay the door price."""
    assert deals.deal_price_cents("paradox", 2000) == 2000
    assert deals.deal_price_cents("013", None) is None
    assert deals.deal_for("013") is None


# --------------------------------------------------------------------------
# Whether the deal applies at all
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "venue,url,expected",
    [
        # Real box offices.
        ("tilburg", "https://apps.ticketmatic.com/widgets/x/addtickets?event=1", True),
        ("chasse", "https://www.chasse.nl/order/add/event/111996", True),
        ("mge", "https://tickets.mge.nl/nl/buyingflow/tickets/1/2/", True),
        # Hosted at the venue, sold by someone else.
        ("tilburg", "https://quizfunevents.com/agenda/pubquiz-09", False),
        ("tilburg", "https://dementieintheater.nl/events/dagmama", False),
        ("mge", "https://feverup.com/m/532823", False),
        # Not a ticketed sale at all -- a bake sale, an open day.
        ("tilburg", "/nl/agenda/2026-open-dag", False),
        ("tilburg", None, False),
        # Verkadefabriek's listing captures no link, so absence says nothing.
        ("verkadefabriek", None, True),
    ],
)
def test_deal_needs_a_real_box_office(venue, url, expected):
    assert deals.deal_applies(venue, url) is expected


# --------------------------------------------------------------------------
# Discount windows
# --------------------------------------------------------------------------

def test_window_opens_at():
    start = datetime(2026, 9, 20, 20, 15)
    assert deals.window_opens_at("tilburg", start) == start - timedelta(hours=48)
    assert deals.window_opens_at("mge", start) == start - timedelta(hours=24)
    # Chasse is day-of, from midnight.
    assert deals.window_opens_at("chasse", start) == datetime(2026, 9, 20, 0, 0)


def test_window_state():
    start = datetime(2026, 9, 20, 20, 15)
    assert deals.window_state("tilburg", start, datetime(2026, 9, 1))[0] == "soon"
    assert deals.window_state("tilburg", start, datetime(2026, 9, 19))[0] == "open"
    assert deals.window_state("tilburg", start, datetime(2026, 9, 21))[0] == "closed"


def test_unknown_venue_has_no_window():
    assert deals.window_state("paradox", datetime(2026, 9, 20, 20, 15))[0] == "unknown"
