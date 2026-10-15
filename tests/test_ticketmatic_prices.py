"""Which Ticketmatic price is the door price, and which is the youth fare.

The shapes below are copied from live Tilburg widgets in October 2026: Eric
Vloeimans inside its 48-hour window, Philzuid with the orchestra's own youth
fare, and Herman van Veen eight days out with neither.
"""

from __future__ import annotations

from datetime import datetime

from podiumwatch.venues import ticketmatic

NORMAAL = "N" * 40
AMBASSADEURS = "A" * 40
JONG_LASTMINUTE = "L" * 40
JONG_VAST = "J" * 40

LOOKUP = (
    '"pricetypes":{'
    f'"{NORMAAL}":{{"id":"{NORMAAL}","name":"normaal","typeid":2301,"remark":"",'
    '"c_altservicecosts":null},'
    f'"{AMBASSADEURS}":{{"id":"{AMBASSADEURS}","name":"ambassadeurs","typeid":2301,'
    '"remark":"geldig voor Vriend en\\/of Partner","c_altservicecosts":12067},'
    f'"{JONG_LASTMINUTE}":{{"id":"{JONG_LASTMINUTE}","name":"t\\/m 26 jaar & student",'
    '"typeid":2301,"remark":"last minute","c_altservicecosts":0},'
    f'"{JONG_VAST}":{{"id":"{JONG_VAST}","name":"t\\/m 26 jaar","typeid":2301,'
    '"remark":"","c_altservicecosts":0}}'
)
NAMES = ticketmatic.price_type_names(LOOKUP)

VOUCHER = [{"type": "voucherids", "value": [10074, 10075]}]


def last_minute(start="2026-10-13 20:00:00", end="2026-10-15 20:00:00", relative_end=0):
    return [{"type": "date", "value": {
        "relativeStart": 2, "relativeEnd": relative_end, "start": start, "end": end,
    }}]


def event(*types):
    """types: (pricetype id, euros, conditions) per price, one contingent."""
    return {"prices": {"contingents": [{"pricetypes": [
        {"pricetypeid": pid, "price": euros,
         "saleschannels": [{"price": euros, "conditions": conds}]}
        for pid, euros, conds in types
    ]}]}}


VLOEIMANS = event(
    (NORMAAL, 32.5, []), (AMBASSADEURS, 29.25, VOUCHER), (JONG_LASTMINUTE, 13.5, last_minute()),
    (NORMAAL, 24.5, []), (AMBASSADEURS, 22.05, VOUCHER), (JONG_LASTMINUTE, 13.5, last_minute()),
)
PHILZUID = event(
    (NORMAAL, 42.0, []), (JONG_VAST, 12.5, []),
    (NORMAAL, 32.0, []), (JONG_VAST, 12.5, []),
)
VAN_VEEN = event(
    (NORMAAL, 52.5, []), (AMBASSADEURS, 47.25, VOUCHER), (NORMAAL, 44.5, []),
)

IN_WINDOW = datetime(2026, 10, 15, 17, 0)
BEFORE_WINDOW = datetime(2026, 10, 12, 12, 0)


def test_the_name_lookup_is_read_including_escaped_slashes():
    assert NAMES[NORMAAL] == "normaal"
    assert NAMES[JONG_LASTMINUTE] == "t/m 26 jaar & student last minute"
    assert NAMES[AMBASSADEURS].startswith("ambassadeurs")


def test_tilburg_last_minute_fare_is_found_inside_its_window():
    """The EUR 13,50 carries a date condition, so it used to be thrown away
    together with the voucher-only 'ambassadeurs' fare."""
    assert ticketmatic.prices(VLOEIMANS, NAMES) == (2450, 3250)
    assert ticketmatic.youth_fare(VLOEIMANS, NAMES, now=IN_WINDOW) == 1350


def test_a_last_minute_fare_outside_its_window_does_not_count():
    assert ticketmatic.youth_fare(VLOEIMANS, NAMES, now=BEFORE_WINDOW) is None


def test_an_unconditional_youth_fare_is_not_the_door_price():
    """Philzuid: 't/m 26 jaar EUR 12,50' made a EUR 42 seat look like EUR 12,50."""
    assert ticketmatic.prices(PHILZUID, NAMES) == (3200, 4200)
    assert ticketmatic.youth_fare(PHILZUID, NAMES, now=BEFORE_WINDOW) == 1250


def test_a_show_without_youth_fares_has_none():
    """Herman van Veen, eight days out: the youth price appears at 48 hours."""
    assert ticketmatic.prices(VAN_VEEN, NAMES) == (4450, 5250)
    assert ticketmatic.youth_fare(VAN_VEEN, NAMES, now=IN_WINDOW) is None


def test_without_the_name_lookup_a_window_ending_at_curtain_still_counts():
    """Verkadefabriek's widget is read only partly; the lookup may be cut off."""
    assert ticketmatic.youth_fare(VLOEIMANS, None, now=IN_WINDOW) == 1350
    early_bird = event(
        (NORMAAL, 30.0, []),
        (JONG_LASTMINUTE, 20.0, last_minute(end="2026-09-01 00:00:00", relative_end=45)),
    )
    assert ticketmatic.youth_fare(
        early_bird, None, now=datetime(2026, 8, 20)
    ) is None
