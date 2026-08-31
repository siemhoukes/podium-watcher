"""Each adapter, against a captured copy of the venue's real markup.

Where a test asserts an exact number it is a number read off that fixture, not
a guess. If a venue redesigns, these fail loudly -- which is the entire point,
because the alternative is a poller that records "nothing on" for a fortnight.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from podiumwatch.models import AVAILABLE, LIMITED, SOLD_OUT
from podiumwatch.venues.chasse import ChasseAdapter
from podiumwatch.venues.mge import MgeAdapter
from podiumwatch.venues.paradox import ParadoxAdapter
from podiumwatch.venues.parktheater import ParktheaterAdapter
from podiumwatch.venues.poppodium013 import Poppodium013Adapter
from podiumwatch.venues.tilburg import TilburgAdapter
from podiumwatch.venues.verkadefabriek import VerkadefabriekAdapter

from .conftest import FakeFetcher, load_text


# --------------------------------------------------------------------------
# Tilburg
# --------------------------------------------------------------------------

def test_tilburg_parses_a_full_page():
    pairs = TilburgAdapter(None)._parse_page(load_text("tilburg_agenda.html.gz"))
    assert len(pairs) == 30

    shows = {s.title: (s, n) for s, n in pairs}
    assert all(s.starts_at is not None for s, _ in pairs), "every tile has a date"

    show, snap = shows["Open Monumentendag"]
    assert snap.state == SOLD_OUT
    # A sold-out tile has a disabled button and therefore no ticket link.
    assert not show.ticket_url


def test_tilburg_keeps_the_signed_ticket_url():
    """The seat counts hang off this URL; the bare event id is rejected."""
    pairs = TilburgAdapter(None)._parse_page(load_text("tilburg_agenda.html.gz"))
    signed = [s for s, _ in pairs if s.ticket_url and "signature=" in s.ticket_url]
    assert signed, "at least one tile links to the signed Ticketmatic widget"


# --------------------------------------------------------------------------
# Peppered: Chasse and Parktheater
# --------------------------------------------------------------------------

def test_chasse_captures_sold_out_performances():
    """The bug that cost every sold-out show.

    An on-sale performance is an order anchor with data attributes; a sold-out
    one is a bare `status-info status-uitverkocht` link with none. Selecting
    only `a[data-event-id]` dropped the second kind entirely.
    """
    pairs = ChasseAdapter(None)._parse_page(load_text("chasse_soldout.html.gz"))
    states = [n.state for _, n in pairs]
    assert SOLD_OUT in states, "sold-out performances must be recorded, not dropped"


def test_peppered_identity_is_stable_across_sale_states():
    """Ids key on production + start time, never `data-event-id`.

    That attribute disappears exactly when a show sells out, so keying on it
    would file the same night as a brand new row at the worst moment.
    """
    pairs = ChasseAdapter(None)._parse_page(load_text("chasse_programma.html.gz"))
    for show, _ in pairs:
        production, _, stamp = show.external_id.rpartition("-")
        assert production.isdigit(), show.external_id
        assert len(stamp) == 12 and stamp.isdigit(), show.external_id
        assert show.starts_at.strftime("%Y%m%d%H%M") == stamp


def test_peppered_links_to_the_production_page_not_the_basket():
    pairs = ChasseAdapter(None)._parse_page(load_text("chasse_programma.html.gz"))
    for show, _ in pairs:
        assert "/programma/" in show.url
        assert "?production=" not in show.url, "the old guessed URL never resolved"


def test_peppered_ignores_subscription_price_tiers():
    """`normaal` sets the base; Cineville and friends are not buyable.

    Taking the minimum across all categories made half of a EUR 14,50 seat
    read as EUR 2,25.
    """
    text = (
        "volloopplaats normaal € 14,50 scholier/student/CJP € 10,00 "
        "Cineville € 4,50 Cineville Kidspas € 4,50 BredaPas € 4,50"
    )
    assert ChasseAdapter._prices(text) == (1450, 1450, 1000)


def test_peppered_drops_placeholder_price_floors():
    """Chasse stamps a EUR 1,00 floor on most rows; nobody pays it."""
    assert ChasseAdapter._prices("€ 1,00–€ 52,00") == (5200, 5200, None)
    assert ChasseAdapter._prices("€ 0,00–€ 13,00") == (1300, 1300, None)


def test_peppered_reads_rank_prices_as_a_range():
    lo, hi, student = ChasseAdapter._prices(
        "1e rang normaal € 29,00 2e rang normaal € 25,00 3e rang normaal € 18,00"
    )
    assert (lo, hi, student) == (1800, 2900, None)


def test_parktheater_shares_the_peppered_parser():
    pairs = ParktheaterAdapter(None)._parse_page(
        load_text("parktheater_programma.html.gz")
    )
    assert pairs, "identical markup, so the same parser must work unchanged"
    assert all(s.venue_id == "parktheater" for s, _ in pairs)


# --------------------------------------------------------------------------
# Verkadefabriek
# --------------------------------------------------------------------------

def test_verkadefabriek_keys_on_production_and_time():
    """A four-night run shares one production id in the link.

    Keyed on that alone, the run collapsed to a single row collecting four
    snapshots per poll, which destroys the fill curve.
    """
    pairs = VerkadefabriekAdapter(None)._parse_page(
        load_text("verkadefabriek_podium.html.gz")
    )
    assert pairs
    ids = [s.external_id for s, _ in pairs]
    assert len(ids) == len(set(ids)), "no duplicate keys within one page"
    for show, _ in pairs:
        assert show.external_id.endswith(show.starts_at.strftime("%Y%m%d%H%M"))


def test_verkadefabriek_reads_the_normal_price_from_a_detail_page():
    adapter = VerkadefabriekAdapter(
        FakeFetcher(default=load_text("verkadefabriek_detail.html.gz"))
    )
    from podiumwatch.models import Show

    show = Show(
        venue_id="verkadefabriek", external_id="x", title="t",
        starts_at=None, url="https://www.verkadefabriek.nl/agenda/x-1",
    )
    # "€ 26,00 Normaal" beside "€ 24,00 Supporter" -- Normaal is the door price.
    assert adapter.fetch_detail_price(show)[0] == 2600


# --------------------------------------------------------------------------
# MgE
# --------------------------------------------------------------------------

def test_mge_reads_the_whole_programme_in_one_request():
    adapter = MgeAdapter(FakeFetcher(default=load_text("mge_feed.json.gz")))
    pairs = adapter.fetch()
    assert len(pairs) == 264
    assert len(adapter.fetcher.requested) == 1, "one feed, not 264 detail pages"


def test_mge_decodes_html_entities():
    """The feed ships escaped text; 63 of 224 titles carried entities."""
    adapter = MgeAdapter(FakeFetcher(default=load_text("mge_feed.json.gz")))
    titles = [s.title for s, _ in adapter.fetch()]
    assert not [t for t in titles if "&amp;" in t or "&#" in t]
    assert any("’" in t for t in titles), "curly apostrophes survive decoding"


def test_mge_never_uses_the_fake_seat_counts():
    """`tickets_free` equals `tickets_total` everywhere, sold-out included.

    It is static capacity dressed as availability. Using it would have printed
    a confident '0% sold' on a sold-out Marillion.
    """
    adapter = MgeAdapter(FakeFetcher(default=load_text("mge_feed.json.gz")))
    for _, snap in adapter.fetch():
        assert snap.seats_free is None
        assert snap.seats_total is None


def test_mge_status_vocabulary():
    adapter = MgeAdapter(FakeFetcher(default=load_text("mge_feed.json.gz")))
    by_state = {}
    for _, snap in adapter.fetch():
        by_state.setdefault(snap.state, 0)
        by_state[snap.state] += 1
    assert by_state.get(SOLD_OUT), "uitverkocht must map to sold out"
    assert by_state.get(LIMITED), "laatste kaarten must map to limited"


def test_mge_external_sales_link_off_site():
    """'Externe verkoop' points at feverup.com, so no Spotticket applies."""
    from podiumwatch import deals

    adapter = MgeAdapter(FakeFetcher(default=load_text("mge_feed.json.gz")))
    external = [
        s for s, n in adapter.fetch()
        if n.note == "externe verkoop"
    ]
    assert external
    assert all(not deals.deal_applies("mge", s.ticket_url) for s in external)


# --------------------------------------------------------------------------
# Paradox and 013
# --------------------------------------------------------------------------

def test_paradox_prefers_aanvang_over_the_broken_content_attribute():
    """`content="2026-09-10 11:00"` on a show whose page says Aanvang 16:00."""
    adapter = ParadoxAdapter(FakeFetcher(default=load_text("paradox_agenda.html.gz")))
    pairs = adapter.fetch()
    assert pairs
    first = next(s for s, _ in pairs if s.starts_at.date() == datetime(2026, 9, 10).date())
    assert first.starts_at.hour == 16, "the visible start time wins"


def test_paradox_reads_prices_and_sold_out():
    adapter = ParadoxAdapter(FakeFetcher(default=load_text("paradox_agenda.html.gz")))
    pairs = adapter.fetch()
    assert any(n.price_min_cents for _, n in pairs)
    assert any(n.state == SOLD_OUT for _, n in pairs)


def test_013_reads_iso_datetimes_and_ribbons():
    adapter = Poppodium013Adapter(FakeFetcher(default=load_text("013_programma.html.gz")))
    pairs = adapter.fetch()
    assert len(pairs) == 161
    assert all(s.starts_at is not None for s, _ in pairs)
    assert all(s.starts_at.tzinfo is None for s, _ in pairs), "naive, like the rest"
    assert any(n.state == SOLD_OUT for _, n in pairs)


def test_013_ignores_marketing_ribbons():
    """'Enige NL show' is not an availability statement."""
    adapter = Poppodium013Adapter(FakeFetcher(default=load_text("013_programma.html.gz")))
    notes = {n.note for _, n in adapter.fetch() if n.note}
    assert "enige nl show" not in notes
    assert "013 op locatie" not in notes


# --------------------------------------------------------------------------
# Shared guarantees
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "adapter_cls,fixture",
    [
        (ChasseAdapter, "chasse_programma.html.gz"),
        (ParktheaterAdapter, "parktheater_programma.html.gz"),
        (VerkadefabriekAdapter, "verkadefabriek_podium.html.gz"),
    ],
)
def test_no_duplicate_ids_within_a_page(adapter_cls, fixture):
    """Two rows with one id means two snapshots per poll on one show."""
    pairs = adapter_cls(None)._parse_page(load_text(fixture))
    ids = [s.external_id for s, _ in pairs]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "adapter_cls,fixture",
    [
        (ChasseAdapter, "chasse_programma.html.gz"),
        (ParktheaterAdapter, "parktheater_programma.html.gz"),
        (VerkadefabriekAdapter, "verkadefabriek_podium.html.gz"),
        (TilburgAdapter, "tilburg_agenda.html.gz"),
    ],
)
def test_every_show_has_a_title_and_a_start(adapter_cls, fixture):
    for show, snap in adapter_cls(None)._parse_page(load_text(fixture)):
        assert show.title
        assert show.starts_at is not None
        assert snap.state in {AVAILABLE, LIMITED, SOLD_OUT, "unknown"}


def test_peppered_skips_cards_it_cannot_resolve_to_one_performance():
    """Two shapes of the same problem, both previously recorded as midnight.

    A start/end pair is a run of nights; "Meerdere tijdstippen" is several
    shows in one day. Either way the card summarises performances it does not
    list, so one row at 00:00 is both the wrong time and the wrong count --
    26 Parktheater rows looked like real data because of it.
    """
    from bs4 import BeautifulSoup

    def card(inner):
        return BeautifulSoup(
            '<li class="eventCard" data-entry-id="99">'
            '<a class="desc" href="/programma/x"></a>'
            '<div class="title">Test</div>' + inner + "</li>",
            "html.parser",
        ).select_one("li.eventCard")

    multi_day = card(
        '<div class="top-date"><span class="start">wo 2 sep ’26</span>'
        '<span class="end">zo 13 sep ’26</span></div>'
    )
    multi_time = card(
        '<div class="top-date"><span class="start">zo 27 sep ’26</span>'
        '<span class="time">Meerdere tijdstippen</span></div>'
    )
    single = card(
        '<div class="top-date"><span class="start">zo 7 mrt ’27</span>'
        '<span class="time">- 20.00</span></div>'
        '<div class="venue">grote zaal</div>'
        "<a class='status-info status-uitverkocht' href='/programma/x'></a>"
    )

    assert ChasseAdapter(None)._parse_card(multi_day) == []
    assert ChasseAdapter(None)._parse_card(multi_time) == []

    resolved = ChasseAdapter(None)._parse_card(single)
    assert len(resolved) == 1
    show, snap = resolved[0]
    assert show.starts_at == datetime(2027, 3, 7, 20, 0), "two-digit year and time"
    assert snap.state == SOLD_OUT
