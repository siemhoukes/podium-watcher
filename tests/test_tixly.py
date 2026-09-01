"""Live occupancy from the Tixly box office.

MgE's own feed carries numbers that look like a seat count and are not, so
this reads the seat picker instead. What makes it trustworthy is that it
cross-checks exactly against what the venue says about itself: a concert the
feed calls "uitverkocht" comes back 1266/1266.
"""

from __future__ import annotations

import json

import pytest

from podiumwatch.venues import tixly

from .conftest import FakeFetcher, load_json, load_text


def test_ids_come_out_of_the_ticket_url():
    assert tixly.ids_from_ticket_url(
        "https://tickets.mge.nl/nl/buyingflow/tickets/59882/122191/"
    ) == ("59882", "122191")


@pytest.mark.parametrize("url", ["", None, "https://feverup.com/m/532823"])
def test_unusable_ticket_urls(url):
    assert tixly.ids_from_ticket_url(url) is None


def test_taken_seats_counts_the_list():
    fetcher = FakeFetcher(default=load_text("tixly_takenseats.json.gz"))
    expected = len(load_json("tixly_takenseats.json.gz")["si"])
    assert tixly.taken_seats(fetcher, "59882", "122191", referer="x") == expected
    assert expected > 0


def test_taken_seats_asks_the_read_only_endpoint():
    """`reserve` and `sreserve` sit next to it and would hold inventory."""
    fetcher = FakeFetcher(default=load_text("tixly_takenseats.json.gz"))
    tixly.taken_seats(fetcher, "59882", "122191", referer="x")
    asked = " ".join(fetcher.requested)
    assert "takenseats" in asked
    assert "reserve" not in asked


def test_hall_capacity_counts_seats_in_the_plan():
    fetcher = FakeFetcher(default=load_text("tixly_hallsvg.svg.gz"))
    assert tixly.hall_capacity(fetcher, "59882", "122191") == 391


def test_occupancy_from_the_two_together():
    taken = len(load_json("tixly_takenseats.json.gz")["si"])
    fetcher = FakeFetcher(default=load_text("tixly_hallsvg.svg.gz"))
    total = tixly.hall_capacity(fetcher, "59882", "122191")
    assert 0 < taken < total, "a real, partial fill rate"


def test_a_silent_box_office_yields_nothing_rather_than_a_guess():
    empty = FakeFetcher()
    assert tixly.taken_seats(empty, "1", "2", referer="x") is None
    assert tixly.hall_capacity(empty, "1", "2") is None


def test_junk_response_is_not_mistaken_for_zero_taken():
    """An empty hall and a broken endpoint must not look the same."""
    fetcher = FakeFetcher(default="<html>oops</html>")
    assert tixly.taken_seats(fetcher, "1", "2", referer="x") is None
