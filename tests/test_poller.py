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
