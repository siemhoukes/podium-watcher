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


def test_a_selective_deal_is_not_stamped_on_every_show():
    """TivoliVredenburg's under-30 rate is real but not universal.

    None of six sampled concerts carried the label, so a flat €10 across all
    600 rows would repeat the Verkadefabriek mistake. The fare is quoted only
    where the concert page actually published it.
    """
    assert deals.deal_price_cents("tivoli", 4600, 1000) == 1000   # published
    assert deals.deal_price_cents("tivoli", 2600, None) == 2600   # not published
    assert deals.deal_for("tivoli").selective is True
    assert deals.deal_for("tivoli").flat_cents is None


def test_doelen_youth_rate_is_what_the_concert_publishes():
    """It used to say EUR 10 on every concert. The price tables say
    't/m 30 jaar EUR 16', and only on concerts labelled 'jongerentarief'."""
    assert deals.deal_price_cents("doelen", 3500, 1600) == 1600
    assert deals.deal_price_source("doelen", 3500, 1600) == "published"
    # No published youth fare: no deal, rather than a promised one.
    assert deals.deal_price_cents("doelen", 3500, None) == 3500
    assert deals.deal_for("doelen").selective is True


def test_tilburg_youth_fare_is_expected_until_seen_on_sale():
    from datetime import datetime

    starts = datetime(2026, 10, 23, 20, 15)
    before = datetime(2026, 10, 20, 12, 0)
    inside = datetime(2026, 10, 22, 12, 0)
    read_inside = datetime(2026, 10, 22, 11, 5)
    read_before = datetime(2026, 10, 21, 18, 0)

    assert deals.youth_status("tilburg", starts, before, None, read_before) == "verwacht"
    assert deals.youth_status("tilburg", starts, inside, 1350, read_inside) == "bevestigd"
    # Herman van Veen's case: the box office was read inside the window and
    # the youth fare was not there.
    assert deals.youth_status("tilburg", starts, inside, None, read_inside) == "geen"
    # A reading from before the window proves nothing either way.
    assert deals.youth_status("tilburg", starts, inside, None, read_before) == "verwacht"
    assert deals.youth_status("chasse", starts, inside, None, read_inside) is None


def test_a_course_titled_as_one_is_filtered_even_without_the_tag():
    """MgE files 'Cursus: Een goed gestemd gehoor' under Klassiek.

    The tag filter never saw it, so it turned up as the biggest saving on the
    board. 'Masterclass' is deliberately left alone -- MgE's masterclasses are
    talk shows worth going to.
    """
    from podiumwatch import categories

    assert categories.is_course("Cursus: Een goed gestemd gehoor", ["Klassiek"])
    assert categories.is_course("Workshopdag met Lucas van Merwijk", ["Global"])
    assert categories.is_course("Iets", ["Cursus"])
    assert not categories.is_course("Masterclass: Het geheim van Peter Bosz", ["Pop"])
    assert not categories.is_course("Queen Symphonic", ["Klassiek"])
