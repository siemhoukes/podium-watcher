"""How the per-run request budget is spent."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from podiumwatch import poller
from podiumwatch.db import Database
from podiumwatch.models import Show, Snapshot

NOW = datetime.now()


class RecordingAdapter:
    venue_id = "test"
    enrich_budget = 8

    def __init__(self):
        self.enriched: list[str] = []

    def enrich(self, show, snap, cached_price=None):
        self.enriched.append(show.external_id)
        snap.price_min_cents = 1000
        return True


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.sqlite3")


def pairs(n=40):
    """Shows one day apart, all comfortably in the future.

    Starting at +1 rather than +0 on purpose: a show beginning this instant is
    correctly treated as no longer upcoming, and that boundary is not what
    these tests are about.
    """
    out = []
    for i in range(n):
        out.append((
            Show(
                venue_id="test", external_id=str(i), title=f"Show {i}",
                starts_at=NOW + timedelta(days=i + 1), url="u",
            ),
            Snapshot(),
        ))
    return out


def test_budget_is_respected(db):
    adapter = RecordingAdapter()
    spent = poller._enrich(db, adapter, pairs())
    assert spent == adapter.enrich_budget


def test_most_of_the_budget_goes_to_the_soonest_shows(db):
    adapter = RecordingAdapter()
    poller._enrich(db, adapter, pairs())
    near = [e for e in adapter.enriched if int(e) < 10]
    assert len(near) >= adapter.enrich_budget * 0.6


def test_some_budget_reaches_the_far_end(db):
    """Soonest-first alone never priced anything past a few weeks."""
    adapter = RecordingAdapter()
    poller._enrich(db, adapter, pairs())
    far = [e for e in adapter.enriched if int(e) > 25]
    assert far, "a slice is reserved for shows never enriched before"


def test_past_shows_are_skipped(db):
    adapter = RecordingAdapter()
    old = [(
        Show(venue_id="test", external_id="old", title="Past",
             starts_at=NOW - timedelta(days=3), url="u"),
        Snapshot(),
    )]
    poller._enrich(db, adapter, old + pairs(5))
    assert "old" not in adapter.enriched


def test_known_prices_are_reused_beyond_the_budget(db):
    """The regression that halved Verkadefabriek's priced rows.

    Reusing a cached price costs no request, so budget must not gate it.
    """
    seeded = pairs(30)
    for show, snap in seeded:
        snap.price_min_cents = 2500
        db.add_snapshot(db.upsert_show(show), snap)
    db.commit()

    fresh = pairs(30)
    poller._enrich(db, RecordingAdapter(), fresh)
    assert all(s.price_min_cents is not None for _, s in fresh), (
        "every show keeps a price it already had"
    )


def test_an_adapter_without_a_budget_is_left_alone(db):
    class NoBudget:
        venue_id = "test"
        enrich_budget = 0

        def enrich(self, *a, **k):  # pragma: no cover - must never be called
            raise AssertionError("should not be called")

    assert poller._enrich(db, NoBudget(), pairs()) == 0


# --------------------------------------------------------------------------
# What a run records about itself
# --------------------------------------------------------------------------

class _Base:
    enrich_budget = 0

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.problems: list[str] = []


class EmptyAdapter(_Base):
    venue_id = "leeg"

    def fetch(self):
        return []


class PartialAdapter(_Base):
    venue_id = "half"

    def fetch(self):
        self.problems.append("Pagina 3 niet opgehaald (HTTP 403); 2 voorstellingen gelezen")
        return pairs(2)


class BlockedAdapter(_Base):
    venue_id = "dicht"

    def fetch(self):
        from podiumwatch.venues.base import FetchError

        raise FetchError("Agenda niet opgehaald: virtuele wachtrij (HTTP 200)")


def test_runs_record_why_they_failed_or_fell_short(tmp_path, monkeypatch):
    """An empty run used to be logged with no reason, and a truncated one as a
    clean success -- which is how three weeks of breakage stayed invisible."""
    monkeypatch.setattr(poller, "ADAPTERS", [EmptyAdapter, PartialAdapter, BlockedAdapter])
    monkeypatch.setattr(poller, "Fetcher", lambda: None)
    monkeypatch.setattr(poller.notify, "send_window_alerts", lambda conn: 0)
    monkeypatch.setattr(poller.notify, "send_health_alerts", lambda conn, ids: 0)

    path = tmp_path / "p.sqlite3"
    poller.poll_once(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    runs = {r["venue_id"]: r for r in conn.execute("SELECT * FROM poll_runs")}
    assert runs["leeg"]["ok"] == 0 and "Geen voorstellingen" in runs["leeg"]["error"]
    assert runs["half"]["ok"] == 1 and "Pagina 3" in runs["half"]["error"]
    assert runs["dicht"]["ok"] == 0 and "wachtrij" in runs["dicht"]["error"]

    # Stamped before the fetch, so every show the run saw is newer than it.
    seen = [r["last_seen"] for r in conn.execute("SELECT last_seen FROM shows")]
    assert seen and all(stamp >= runs["half"]["started_at"] for stamp in seen)
