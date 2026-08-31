"""Dutch dates and money, which every adapter leans on."""

from __future__ import annotations

from datetime import datetime

import pytest

from podiumwatch.venues.base import euros_to_cents, parse_dutch_datetime

TODAY = datetime(2026, 9, 1)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("zo 13 sep", datetime(2026, 9, 13)),
        ("13 september", datetime(2026, 9, 13)),
        ("di 8 sep.", datetime(2026, 9, 8)),
        ("wo 2 okt 2026", datetime(2026, 10, 2)),
        ("zo 7 mrt 2027", datetime(2027, 3, 7)),
    ],
)
def test_dutch_dates(text, expected):
    assert parse_dutch_datetime(text, today=TODAY) == expected


def test_time_is_picked_up_from_a_second_string():
    assert parse_dutch_datetime("zo 13 sep", "20:15", today=TODAY) == datetime(
        2026, 9, 13, 20, 15
    )


def test_time_may_share_the_date_string():
    """Verkadefabriek writes 'zo 8 nov. - 11:00' as one line."""
    text = "zo 8 nov. - 11:00"
    assert parse_dutch_datetime(text, text, today=TODAY) == datetime(2026, 11, 8, 11, 0)


def test_a_dot_separated_time_also_works():
    """Ticketmatic and Peppered both write 20.30."""
    assert parse_dutch_datetime("zo 13 sep", "20.30", today=TODAY) == datetime(
        2026, 9, 13, 20, 30
    )


def test_undated_month_already_past_means_next_year():
    """Agendas list only upcoming shows.

    Without this, every January show scraped in December lands eight months in
    the past and silently drops out of the calendar.
    """
    assert parse_dutch_datetime("15 jan", today=TODAY) == datetime(2027, 1, 15)
    assert parse_dutch_datetime("15 dec", today=datetime(2026, 12, 20)) == datetime(
        2027, 12, 15
    )


def test_today_stays_this_year():
    assert parse_dutch_datetime("1 sep", today=TODAY) == datetime(2026, 9, 1)


@pytest.mark.parametrize("text", ["", "volgende week", "32 sep", "13 foo"])
def test_unparsable_dates_return_none(text):
    assert parse_dutch_datetime(text, today=TODAY) is None


@pytest.mark.parametrize(
    "text,cents",
    [
        ("€13,50", 1350),
        ("13.50", 1350),
        ("€ 26,00", 2600),
        ("60", 6000),
        ("€ 0,00", 0),
        ("9,5", 950),
    ],
)
def test_euros_to_cents(text, cents):
    assert euros_to_cents(text) == cents


def test_euros_to_cents_on_nonsense():
    assert euros_to_cents(None) is None
    assert euros_to_cents("gratis") is None
