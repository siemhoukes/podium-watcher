"""Walking a listing page by page, and noticing when that goes wrong.

Every failure this file pins down was silent in production. In late September
2026 Peppered renamed its page parameter, Verkadefabriek turned `/podium` into
a six-show teaser, and both scrapers kept recording clean runs while reading a
fraction of the programme.
"""

from __future__ import annotations

import gzip
from datetime import datetime
from pathlib import Path

import pytest

from podiumwatch.http import Fetcher, block_reason
from podiumwatch.models import Show, Snapshot
from podiumwatch.venues.base import FetchError, VenueAdapter, next_link

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


# --------------------------------------------------------------------------
# next_link
# --------------------------------------------------------------------------

def test_peppered_next_link_uses_the_renamed_parameter():
    """`?page=2` now returns page one; the site's own link says `p54_page`."""
    html = load_text("theaterrotterdam_agenda.html.gz")
    assert next_link(html, "https://www.theaterrotterdam.nl/agenda") == (
        "https://www.theaterrotterdam.nl/agenda?p54_page=2"
    )


def test_verkadefabriek_next_link_keeps_every_genre_filter():
    html = load_text("verkadefabriek_agenda.html.gz")
    url = next_link(html, "https://www.verkadefabriek.nl/agenda?genre=Cabaret")
    assert url.startswith("https://www.verkadefabriek.nl/agenda?genre=Cabaret&genre=Club")
    assert url.endswith("&page=2")
    assert "&amp;" not in url and " " not in url


def test_no_next_link_on_the_last_page():
    assert next_link('<a rel="prev" href="?page=3">', "https://x.nl/") is None
    assert next_link("<p>nothing here</p>", "https://x.nl/") is None


def test_fragment_is_dropped():
    html = '<a class="btn next" rel="next" href="/programma?p54_page=2#page_part_54">'
    assert next_link(html, "https://www.chasse.nl/programma") == (
        "https://www.chasse.nl/programma?p54_page=2"
    )


# --------------------------------------------------------------------------
# paginate, against a scripted fetcher
# --------------------------------------------------------------------------

class ScriptedFetcher:
    """Serves canned pages by URL; a URL mapped to None fails like a 403."""

    def __init__(self, pages: dict[str, str | None], problem="HTTP 403"):
        self.pages = pages
        self.problem = problem
        self.asked: list[str] = []
        self.last_problem = None

    def get_text(self, url, **kwargs):
        self.asked.append(url)
        page = self.pages.get(url)
        self.last_problem = None if page is not None else self.problem
        return page


def page(ids: list[str], next_url: str | None = None) -> str:
    items = "".join(f'<li data-id="{i}"></li>' for i in ids)
    nxt = f'<a rel="next" href="{next_url}">volgende</a>' if next_url else ""
    return f"<ul>{items}</ul>{nxt}"


def parse(html: str) -> list[tuple[Show, Snapshot]]:
    import re

    return [
        (Show(venue_id="t", external_id=i, title=i,
              starts_at=datetime(2026, 11, 1), url="https://venue.test/x"),
         Snapshot())
        for i in re.findall(r'data-id="([^"]+)"', html)
    ]


BASE = "https://venue.test/agenda"


def adapter_for(pages, **kw):
    a = VenueAdapter(ScriptedFetcher(pages, **kw))
    a.venue_id = "t"
    return a


def test_follows_next_links_to_the_end():
    a = adapter_for({
        BASE: page(["a", "b"], "?p54_page=2"),
        BASE + "?p54_page=2": page(["c", "d"], "?p54_page=3"),
        BASE + "?p54_page=3": page(["e"]),
    })
    got = a.paginate(BASE, parse, max_pages=10)
    assert [s.external_id for s, _ in got] == list("abcde")
    assert a.problems == []
    assert a.pages_read == 3


def test_an_unreadable_first_page_is_a_failure_with_a_reason():
    a = adapter_for({BASE: None}, problem="virtuele wachtrij (HTTP 200)")
    with pytest.raises(FetchError, match="wachtrij"):
        a.paginate(BASE, parse, max_pages=10)


def test_a_first_page_without_shows_is_a_failure_not_a_quiet_venue():
    a = adapter_for({BASE: "<html>redesigned</html>"})
    with pytest.raises(FetchError, match="opmaak"):
        a.paginate(BASE, parse, max_pages=10)


def test_a_later_page_failing_keeps_what_was_read_and_says_so():
    a = adapter_for({
        BASE: page(["a", "b"], "?p54_page=2"),
        BASE + "?p54_page=2": None,
    })
    got = a.paginate(BASE, parse, max_pages=10)
    assert [s.external_id for s, _ in got] == ["a", "b"]
    assert len(a.problems) == 1
    assert "Pagina 2" in a.problems[0] and "HTTP 403" in a.problems[0]


def test_a_site_ignoring_the_page_number_is_reported():
    """Exactly the September failure: page two came back as page one."""
    a = adapter_for({
        BASE: page(["a", "b"], "?page=2"),
        BASE + "?page=2": page(["a", "b"], "?page=3"),
    })
    got = a.paginate(BASE, parse, max_pages=10)
    assert len(got) == 2
    assert any("negeert" in p for p in a.problems)


def test_a_listing_longer_than_the_page_cap_is_reported():
    pages = {BASE: page(["p1"], "?n=2")}
    for n in range(2, 6):
        pages[f"{BASE}?n={n}"] = page([f"p{n}"], f"?n={n + 1}")
    a = adapter_for(pages)
    got = a.paginate(BASE, parse, max_pages=3)
    assert len(got) == 3
    assert any("afgekapt" in p for p in a.problems)


def test_a_next_link_back_to_a_visited_page_stops_the_walk():
    a = adapter_for({
        BASE: page(["a"], "?p=2"),
        BASE + "?p=2": page(["b"], BASE),
    })
    got = a.paginate(BASE, parse, max_pages=10)
    assert [s.external_id for s, _ in got] == ["a", "b"]
    assert a.problems == []


# --------------------------------------------------------------------------
# Verkadefabriek: the landing page is not the listing
# --------------------------------------------------------------------------

def test_verkadefabriek_reads_the_full_listing_not_the_landing_page():
    from podiumwatch.venues.verkadefabriek import BASE as VF, PODIUM, VerkadefabriekAdapter

    listing = (
        VF + "/agenda?genre=Cabaret&genre=Club&genre=Cursus&genre=Dans&genre=Festival"
        "&genre=Flaneerprogramma&genre=Kinderen&genre=Masterclass&genre=Muziek"
        "&genre=Open-repetitie&genre=Talkshow&genre=theater&genre=Training"
        "&genre=Woord&genre=Workshop"
    )
    fetcher = ScriptedFetcher({
        PODIUM: load_text("verkadefabriek_landing.html.gz"),
        listing: load_text("verkadefabriek_agenda.html.gz"),
    })
    adapter = VerkadefabriekAdapter(fetcher)
    assert adapter._listing_url() == listing

    got = adapter.paginate(listing, adapter._parse_page, max_pages=1)
    # Twenty per page on the listing, against six on the teaser.
    assert len(got) == 20


def test_verkadefabriek_listing_has_the_shows_the_teaser_skipped():
    html = load_text("verkadefabriek_agenda.html.gz")
    landing = load_text("verkadefabriek_landing.html.gz")
    # OTION and Heartbeats were dropped for a week while still on sale.
    assert "165569" in html and "162331" in html
    assert "165569" not in landing


# --------------------------------------------------------------------------
# Waiting rooms and bot checks
# --------------------------------------------------------------------------

def test_parktheater_waiting_room_is_recognised():
    assert block_reason(load_text("parktheater_wachtrij.html.gz")) == "virtuele wachtrij"


def test_bunny_bot_check_is_recognised():
    page_text = (
        "<html><body>Establishing a secure connection ... Please enable "
        "JavaScript to continue. Request ID: aa4f</body></html>"
    )
    assert block_reason(page_text) == "botcheck"


def test_a_real_agenda_is_not_mistaken_for_a_block_page():
    assert block_reason(load_text("theaterrotterdam_agenda.html.gz")) is None


class FakeResponse:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = {}
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.response


def test_fetcher_turns_a_waiting_room_into_a_failure_with_a_reason():
    f = Fetcher(min_delay=0, max_delay=0)
    f.session = FakeSession(FakeResponse(200, load_text("parktheater_wachtrij.html.gz")))
    assert f.get_text("https://www.parktheater.nl/programma") is None
    assert f.last_problem == "virtuele wachtrij (HTTP 200)"
    # Detected, not fought: one request, no retries.
    assert f.session.calls == 1


def test_fetcher_names_a_plain_http_error():
    f = Fetcher(min_delay=0, max_delay=0)
    f.session = FakeSession(FakeResponse(404, "<html>" + "x" * 60_000 + "</html>"))
    assert f.get_text("https://x.test/") is None
    assert f.last_problem == "HTTP 404"
